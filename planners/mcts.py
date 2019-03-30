import sys
import socket
import numpy as np

from mcts_tree_node import TreeNode
from mcts_tree import MCTSTree

from generators.uniform import UniformGenerator
from generators.voo import VOOGenerator
from generators.doo import DOOGenerator
from generators.randomized_doo import RandomizedDOOGenerator

## openrave helper libraries
sys.path.append('../mover_library/')
from manipulation.primitives.savers import DynamicEnvironmentStateSaver
from samplers import *
from utils import *

import time
from generators.doo_utils.doo_tree import BinaryDOOTree, DOOTreeNode
sys.path.append('../mover_library/')
from utils import get_pick_domain, get_place_domain
from generators.gpucb import GPUCBGenerator
from trajectory_representation.operator import Operator

sys.setrecursionlimit(15000)

DEBUG = True

hostname = socket.gethostname()
if hostname == 'dell-XPS-15-9560':
    from mcts_graphics import write_dot_file


def create_doo_agent(operator):
    if operator == 'two_arm_pick':
        domain = get_pick_domain()
    else:
        domain = get_place_domain()
    return BinaryDOOTree(domain)


class MCTS:
    def __init__(self, widening_parameter, exploration_parameters,
                 sampling_strategy, sampling_strategy_exploration_parameter, c1, n_feasibility_checks,
                 environment, use_progressive_widening, domain_name):
        self.c1 = c1
        self.widening_parameter = widening_parameter
        self.exploration_parameters = exploration_parameters
        self.time_limit = np.inf
        self.discount_rate = 0.9
        self.environment = environment
        self.sampling_strategy = sampling_strategy
        self.sampling_strategy_exploration_parameter = sampling_strategy_exploration_parameter
        self.depth_limit = 300
        self.use_progressive_widening = use_progressive_widening

        self.env = self.environment.env
        self.robot = self.environment.robot
        self.s0_node = self.create_node(None, depth=0, reward=0, is_init_node=True)

        self.original_s0_node = self.s0_node
        self.tree = MCTSTree(self.s0_node, self.exploration_parameters)
        self.found_solution = False
        self.goal_reward = 2
        self.n_feasibility_checks = n_feasibility_checks

    def create_sampling_agent(self, node, operator_name):
        if self.sampling_strategy == 'unif':
            return UniformGenerator(operator_name, self.environment)
        elif self.sampling_strategy == 'voo':
            return VOOGenerator(operator_name, self.environment, self.sampling_strategy_exploration_parameter, self.c1)
        elif self.sampling_strategy == 'gpucb':
            return GPUCBGenerator(operator_name, self.environment, self.sampling_strategy_exploration_parameter)
        elif self.sampling_strategy == 'doo':
            return DOOGenerator(node, self.environment, self.sampling_strategy_exploration_parameter)
        elif self.sampling_strategy == 'randomized_doo':
            return RandomizedDOOGenerator(node, self.environment, self.sampling_strategy_exploration_parameter)
        else:
            print "Wrong sampling strategy"
            return -1

    def create_node(self, parent_action, depth, reward, is_init_node):
        if self.environment.is_goal_reached():
            operator_skeleton = None
        else:
            if parent_action is None or parent_action.type == 'two_arm_place':
                operator_skeleton = Operator(operator_type='two_arm_pick',
                                             discrete_parameters={'object':
                                                                  self.environment.objects_currently_not_in_goal[0]},
                                             continuous_parameters=None,
                                             low_level_motion=None)
            else:
                operator_skeleton = Operator(operator_type='two_arm_place',
                                             discrete_parameters={'region': self.environment.regions['entire_region']},
                                             continuous_parameters=None,
                                             low_level_motion=None)

        state_saver = DynamicEnvironmentStateSaver(self.environment.env)
        node = TreeNode(operator_skeleton, self.exploration_parameters, depth, state_saver, self.sampling_strategy,
                        is_init_node)

        if not self.environment.is_goal_reached():
            node.sampling_agent = self.create_sampling_agent(node, operator_skeleton.type)

        node.objects_not_in_goal = self.environment.objects_currently_not_in_goal
        node.parent_action_reward = reward
        node.parent_action = parent_action
        return node

    def retrace_best_plan(self):
        plan = []
        _, _, best_leaf_node = self.tree.get_best_trajectory_sum_rewards_and_node(self.discount_rate)
        curr_node = best_leaf_node

        while not curr_node.parent is None:
            plan.append(curr_node.parent_action)
            curr_node = curr_node.parent

        plan = plan[::-1]
        return plan

    def get_best_goal_node(self):
        leaves = self.tree.get_leaf_nodes()
        goal_nodes = [leaf for leaf in leaves if leaf.is_goal_node]
        if len(goal_nodes) > 1:
            best_traj_reward, curr_node, _ = self.tree.get_best_trajectory_sum_rewards_and_node(self.discount_rate)
        else:
            curr_node = goal_nodes[0]
        return curr_node

    def switch_init_node(self, node):
        self.s0_node.is_init_node = False
        self.s0_node = node
        self.s0_node.is_init_node = True
        self.environment.reset_to_init_state(node)
        self.found_solution = False

    def log_current_tree_to_dot_file(self, iteration):
        if socket.gethostname() == 'dell-XPS-15-9560':
            write_dot_file(self.tree, iteration, '')

    def is_time_to_switch_initial_node(self):
        if self.s0_node.is_goal_node:
            return True

        is_pick_node = self.s0_node.operator_skeleton.type == 'two_arm_pick'

        if len(self.s0_node.Q) == 0:
            we_have_feasible_action = False
        else:
            root_node_reward_history = self.s0_node.reward_history.values()
            root_node_reward_history = [r for R in root_node_reward_history for r in R]
            we_have_feasible_action = np.max(root_node_reward_history) >= 0

        if is_pick_node:
            we_evaluated_the_node_enough = we_have_feasible_action and self.s0_node.Nvisited > 10
        else:
            we_evaluated_the_node_enough = we_have_feasible_action and self.s0_node.Nvisited > 30

        return we_evaluated_the_node_enough

    def choose_child_node_to_descend_to(self):
        if self.s0_node.is_goal_node:
            best_node = self.tree.root
        else:
            best_action = self.s0_node.Q.keys()[np.argmax(self.s0_node.Q.values())] # choose the best Q value
            best_node = self.s0_node.children[best_action]
        return best_node

    def search(self, n_iter=100, max_time=np.inf):
        # n_optimal_iter: additional number of iterations you are allowed to run after finding a solution
        depth = 0
        time_to_search = 0
        search_time_to_reward = []
        plan = None
        for iteration in range(n_iter):
            print '*****SIMULATION ITERATION %d' % iteration
            self.environment.reset_to_init_state(self.s0_node)

            if self.is_time_to_switch_initial_node():
                best_child_node = self.choose_child_node_to_descend_to()
                self.switch_init_node(best_child_node)

            stime = time.time()
            self.simulate(self.s0_node, depth)
            time_to_search += time.time() - stime

            self.log_current_tree_to_dot_file(iteration)

            best_traj_rwd, progress, best_node = self.tree.get_best_trajectory_sum_rewards_and_node(self.discount_rate)
            search_time_to_reward.append([time_to_search, iteration, best_traj_rwd, len(progress)])
            plan = self.retrace_best_plan()
            print search_time_to_reward

            if time_to_search > max_time:
                break

        self.environment.reset_to_init_state(self.s0_node)
        return search_time_to_reward, plan

    def choose_action(self, curr_node):
        if not curr_node.is_ucb_step(self.widening_parameter, self.environment.infeasible_reward,
                                     self.use_progressive_widening):
            print "Sampling new action"
            new_continuous_parameters = self.sample_continuous_parameters(curr_node)
            curr_node.add_actions(new_continuous_parameters)
        action = curr_node.perform_ucb_over_actions()

        return action

    @staticmethod
    def update_node_statistics(curr_node, action, sum_rewards, reward):
        # todo rewrite this function
        curr_node.Nvisited += 1

        is_action_never_tried = curr_node.N[action] == 0
        if is_action_never_tried:
            curr_node.reward_history[action] = [reward]
            curr_node.N[action] += 1
            curr_node.Q[action] = sum_rewards
        else:
            curr_node.reward_history[action].append(reward)
            curr_node.N[action] += 1
            curr_node.Q[action] += (sum_rewards - curr_node.Q[action]) / float(curr_node.N[action])

    @staticmethod
    def update_goal_node_statistics(curr_node, reward):
        # todo rewrite this function
        curr_node.Nvisited += 1
        curr_node.reward = reward

    def simulate(self, curr_node, depth):
        if self.environment.is_goal_reached():
            # arrived at the goal state
            if not curr_node.is_goal_and_already_visited:
                self.found_solution = True
                curr_node.is_goal_node = True
                print "Solution found, returning the goal reward", self.goal_reward
                self.update_goal_node_statistics(curr_node, self.goal_reward)
            return self.goal_reward

        if depth == self.depth_limit:
            return 0

        if DEBUG:
            print "At depth ", depth
            print "Is it time to pick?", self.environment.is_pick_time()

        action = self.choose_action(curr_node)
        reward = self.environment.apply_operator_instance(action)

        if not curr_node.is_action_tried(action):
            next_node = self.create_node(action, depth+1, reward, is_init_node=False)
            self.tree.add_node(next_node, action, curr_node)
            next_node.sum_ancestor_action_rewards = next_node.parent.sum_ancestor_action_rewards + reward
        else:
            next_node = curr_node.children[action]

        is_infeasible_action = reward == self.environment.infeasible_reward
        if is_infeasible_action:
            sum_rewards = reward
        else:
            sum_rewards = reward + self.discount_rate * self.simulate(next_node, depth + 1)

        self.update_node_statistics(curr_node, action, sum_rewards, reward)
        if curr_node.is_init_node and curr_node.parent is not None:
            self.update_ancestor_node_statistics(curr_node.parent, curr_node.parent_action, sum_rewards)

        return sum_rewards

    def update_ancestor_node_statistics(self, node, action, child_sum_rewards):
        if node is None:
            return

        parent_reward_to_node = node.reward_history[action][0]
        parent_sum_rewards = parent_reward_to_node + self.discount_rate * child_sum_rewards
        self.update_node_statistics(node, action, parent_sum_rewards, parent_reward_to_node)
        self.update_ancestor_node_statistics(node.parent, node.parent_action, parent_sum_rewards)

    def sample_continuous_parameters(self, node):
        return node.sampling_agent.sample_next_point(node, self.n_feasibility_checks)


