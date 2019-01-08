import os
import sys
from multiprocessing.pool import ThreadPool  # dummy is nothing but multiprocessing but wrapper around threading
import argparse


def worker_p(config):
    s = config['sampling_strategy']
    d = config['domain']
    pidx = config['trial']
    w = config['widening_parameter']
    e = config['epsilon']

    command = 'python ./test_scripts/test_hierarchical_mcts.py -sampling_strategy ' + s + \
        ' -problem_idx ' + str(pidx) + ' -domain ' + d + ' -epsilon ' + str(e) + ' -widening_parameter ' + str(w)

    print command
    os.system(command)


def worker_wrapper_multi_input(multi_args):
    return worker_p(multi_args)


def main():
    parser = argparse.ArgumentParser(description='MCTS parameters')
    parser.add_argument('-sampling', type=str, default='unif')
    parser.add_argument('-domain', type=str, default='convbelt')
    parser.add_argument('-w', nargs='+', type=float)
    parser.add_argument('-epsilon', nargs='+', type=float)

    args = parser.parse_args()

    sampling_strategy = args.sampling
    epsilons = args.epsilon if args.epsilon is not None else [-1]
    domain = args.domain
    widening_parameters = args.w
    trials = range(120)
    configs = []
    for e in epsilons:
        for t in trials:
            for widening_parameter in widening_parameters:
                config = {"widening_parameter": widening_parameter,
                          "epsilon": e, 'trial':t, 'domain':domain, 'sampling_strategy':sampling_strategy}
                configs.append(config)

    """
    else:
        trials = range(120)
        configs = []
        for t in trials:
            config = {"widening_parameter": widening_parameter,
                      "epsilon": None, 'trial':t, 'domain':domain, 'sampling':sampling_strategy}
            configs.append([t, domain, sampling_strategy])
    """

    n_workers = int(30)

    print configs
    pool = ThreadPool(n_workers)
    results = pool.map(worker_wrapper_multi_input, configs)


if __name__ == '__main__':
    main()
