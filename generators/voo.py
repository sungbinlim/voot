import sys
import numpy as np
from samplers import gaussian_randomly_place_in_region
from generator import Generator
from mover_library.utils import pick_parameter_distance, place_parameter_distance, se2_distance, visualize_path
from mover_library.utils import *
import time


class VOOGenerator(Generator):
    def __init__(self, operator_name, problem_env, explr_p, c1, sampling_mode):
        Generator.__init__(self, operator_name, problem_env)
        self.explr_p = explr_p
        self.evaled_actions = []
        self.evaled_q_values = []
        self.c1 = c1
        self.feasible_actions = []
        self.feasible_q_values = []
        self.idx_to_update = None
        self.robot = self.problem_env.robot
        self.sampling_mode = sampling_mode
        self.counter_ratio = 0.5

    def update_evaled_values(self, node):
        executed_actions_in_node = node.Q.keys()
        executed_action_values_in_node = node.Q.values()
        if len(executed_action_values_in_node) == 0:
            return

        if self.idx_to_update is not None:
            found = False
            for a, q in zip(executed_actions_in_node, executed_action_values_in_node):
                if np.all(np.isclose(self.evaled_actions[self.idx_to_update], a.continuous_parameters['action_parameters'])):
                    found = True
                    break
            try:
                assert found
            except AssertionError:
                print "idx to update not found"
                import pdb;pdb.set_trace()

            self.evaled_q_values[self.idx_to_update] = q

        # What does the code snippet below do? Update the feasible operator instances?
        feasible_idxs = np.where(np.array(executed_action_values_in_node) != self.problem_env.infeasible_reward)[0].tolist()
        assert np.sum(np.array(executed_action_values_in_node) != self.problem_env.infeasible_reward) == len(feasible_idxs)
        for i in feasible_idxs:
            action = executed_actions_in_node[i]
            q_value = executed_action_values_in_node[i]

            is_in_array = [np.array_equal(action.continuous_parameters['action_parameters'], a)
                           for a in self.evaled_actions]
            is_action_included = np.any(is_in_array)

            try:
                assert is_action_included
            except AssertionError:
                import pdb; pdb.set_trace()

            self.evaled_q_values[np.where(is_in_array)[0][0]] = q_value

    def sample_next_point(self, node, n_iter):
        self.update_evaled_values(node)

        is_more_than_one_action_in_node = len(self.evaled_actions) > 1
        if is_more_than_one_action_in_node:
            max_reward_of_each_action = np.array([np.max(rlist) for rlist in node.reward_history.values()])
            n_feasible_actions = np.sum(max_reward_of_each_action > -2)
            we_have_feasible_action = n_feasible_actions >= 1
        else:
            we_have_feasible_action = False

        rnd = np.random.random()
        is_sample_from_best_v_region = rnd < 1 - self.explr_p and we_have_feasible_action

        if is_sample_from_best_v_region:
            print 'Sample ' + node.operator_skeleton.type + ' from best region'
        else:
            maxrwd = None if len(self.evaled_actions) == 0 else np.max(node.reward_history.values())
            print 'Sample ' + node.operator_skeleton.type + ' from uniform, max rwd: ', maxrwd

        action, status = self.sample_feasible_action(is_sample_from_best_v_region, n_iter, node)

        """
        if is_more_than_one_action_in_node and n_feasible_actions > 5 \
                and node.operator_skeleton.type == 'two_arm_place':
            import pdb;pdb.set_trace()
            to_plot = self.get_to_draw_configs(node)
            set_robot_config(self.get_best_evaled_action(), self.robot)
            import pdb;pdb.set_trace()
        """

        if status != 'HasSolution':
            print node.operator_skeleton.type + " sampling failed"
        else:
            self.evaled_actions.append(action['action_parameters'])
            self.evaled_q_values.append('update_me')
            self.idx_to_update = len(self.evaled_actions) - 1

        return action

    def get_to_draw_configs(self, node):
        to_plot = []
        for i in range(30):
            action, status = self.sample_feasible_action(True, 50, node)
            if status == 'HasSolution':
                to_plot.append(action['action_parameters'])
        to_plot.append(get_body_xytheta(self.robot))
        to_plot.append(self.get_best_evaled_action())
        return to_plot

    def sample_feasible_action(self, is_sample_from_best_v_region, n_iter, node):
        action = None
        for i in range(n_iter):
            if is_sample_from_best_v_region:
                action_parameters = self.sample_from_best_voronoi_region(node)
            else:
                action_parameters = self.sample_from_uniform()

            action, status = self.feasibility_checker.check_feasibility(node, action_parameters)
            if status == 'HasSolution':
                break
            else:
                pass
        return action, status

    def sample_from_best_voronoi_region(self, node):
        operator = node.operator_skeleton.type
        if operator == 'two_arm_pick':
            obj = node.operator_skeleton.discrete_parameters['object']
            params = self.sample_pick_from_best_voroi_region(obj)
        elif operator == 'two_arm_place':
            params = self.sample_place_from_best_voroi_region()
        else:
            raise NotImplementedError
        return params

    def get_best_evaled_action(self):
        DEBUG = True
        if DEBUG:
            if 'update_me' in self.evaled_q_values:
                best_action_idxs = np.argwhere(self.evaled_q_values[:-1] == np.amax(self.evaled_q_values[:-1]))
            else:
                best_action_idxs = np.argwhere(self.evaled_q_values == np.amax(self.evaled_q_values))
            best_action_idxs = best_action_idxs.reshape((len(best_action_idxs, )))
            best_action_idx = np.random.choice(best_action_idxs)
        else:
            best_action_idxs = np.argwhere(self.evaled_q_values == np.amax(self.evaled_q_values))
            best_action_idxs = best_action_idxs.reshape((len(best_action_idxs, )))
            best_action_idx = np.random.choice(best_action_idxs)
        return self.evaled_actions[best_action_idx]

    def sample_near_best_action(self, best_evaled_action, counter):
        dim_x = self.domain[1].shape[-1]
        possible_max = (self.domain[1] - best_evaled_action) / np.exp(self.counter_ratio*counter)
        possible_min = (self.domain[0] - best_evaled_action) / np.exp(self.counter_ratio*counter)

        possible_values = np.random.uniform(possible_min, possible_max, (dim_x,))
        new_parameters = best_evaled_action + possible_values
        while np.any(new_parameters > self.domain[1]) or np.any(new_parameters < self.domain[0]):
            possible_values = np.random.uniform(possible_min, possible_max, (dim_x,))
            new_parameters = best_evaled_action + possible_values
        return new_parameters

    def gaussian_sample_near_best_action(self, best_evaled_action, counter):
        variance = (self.domain[1] - self.domain[0]) / np.exp(counter)
        new_parameters = np.random.normal(best_evaled_action, variance)
        new_parameters = np.clip(new_parameters, self.domain[0], self.domain[1])

        return new_parameters

    def sample_place_from_best_voroi_region(self):
        best_dist = np.inf
        other_dists = np.array([-1])
        counter = 0

        best_evaled_action = self.get_best_evaled_action()
        other_actions = self.evaled_actions

        new_parameters = None
        while np.any(best_dist > other_dists) and counter < 1000:
            if self.sampling_mode == 'gaussian':
                new_parameters = self.gaussian_sample_near_best_action(best_evaled_action, counter)
            else:
                new_parameters = self.sample_near_best_action(best_evaled_action, counter)

            best_dist = place_parameter_distance(new_parameters, best_evaled_action, self.c1)
            other_dists = np.array([place_parameter_distance(other, new_parameters, self.c1) for other in other_actions])
            counter += 1

        return new_parameters

    def sample_pick_from_best_voroi_region(self, obj):
        best_dist = np.inf
        other_dists = np.array([-1])
        counter = 0

        best_evaled_action = self.get_best_evaled_action()
        other_actions = self.evaled_actions

        new_parameters = None
        while np.any(best_dist > other_dists) and counter < 1000:
            new_parameters = self.sample_near_best_action(best_evaled_action, counter)

            best_dist = [pick_parameter_distance(obj, new_parameters, best_evaled_action)]
            other_dists = np.array([pick_parameter_distance(obj, other, new_parameters) for other in other_actions])
            counter += 1

        print "Counter ", counter
        return new_parameters



