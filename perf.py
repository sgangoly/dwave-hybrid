#!/usr/bin/env python

# Copyright 2018 D-Wave Systems Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Performance tests."""

import sys
from itertools import chain
from collections import OrderedDict
from glob import glob

import dimod
from dwave.system.samplers import DWaveSampler
from dwave.system.composites import EmbeddingComposite
from dwave_qbsolv import QBSolv

from hybrid.samplers import (
    QPUSubproblemExternalEmbeddingSampler, QPUSubproblemAutoEmbeddingSampler,
    SimulatedAnnealingSubproblemSampler, RandomSubproblemSampler,
    TabuSubproblemSampler, TabuProblemSampler, InterruptableTabuSampler)
from hybrid.decomposers import (
    RandomSubproblemDecomposer, IdentityDecomposer,
    TilingChimeraDecomposer, EnergyImpactDecomposer)
from hybrid.composers import SplatComposer
from hybrid.core import State, SampleSet, Runnable
from hybrid.flow import RacingBranches, ArgMinFold, SimpleIterator
from hybrid.utils import min_sample
from hybrid.profiling import tictoc


class QBSolvProblemSampler(Runnable):
    """QBSolv wrapper for hybrid."""

    def __init__(self, bqm, qpu=None):
        self.bqm = bqm
        self.sampler = EmbeddingComposite(qpu) if qpu is not None else None

    def next(self, state):
        response = QBSolv().sample(self.bqm, solver=self.sampler)
        return state.updated(samples=response,
                             debug=dict(sampler=self.name))


problems = chain(
    sorted(glob('problems/qbsolv/bqp100_*'))[:5],
    sorted(glob('problems/qbsolv/bqp2500_*'))[:5],
    sorted(glob('problems/random-chimera/2048*'))[:5],
    sorted(glob('problems/random-chimera/8192*'))[:5],
    sorted(glob('problems/ac3/*'))[:5],
)

solver_factories = [
    ("10 second Tabu",
        lambda bqm, **kw: TabuProblemSampler(bqm, timeout=10000)),

    ("10k sweeps Simulated Annealing",
        lambda bqm, **kw: IdentityDecomposer(bqm) | SimulatedAnnealingSubproblemSampler(sweeps=10000) | SplatComposer(bqm)),

    ("qbsolv-like solver",
        lambda bqm, qpu, **kw: SimpleIterator(RacingBranches(
            InterruptableTabuSampler(bqm, quantum_timeout=200),
            EnergyImpactDecomposer(bqm, max_size=50, min_diff=30)
            | QPUSubproblemAutoEmbeddingSampler(qpu_sampler=qpu)
            | SplatComposer(bqm)
        ) | ArgMinFold(), max_iter=100, convergence=10)),

    ("tiling chimera solver",
        lambda bqm, qpu, **kw: SimpleIterator(RacingBranches(
            InterruptableTabuSampler(bqm, quantum_timeout=200),
            TilingChimeraDecomposer(bqm, size=(16,16,4))
            | QPUSubproblemExternalEmbeddingSampler(qpu_sampler=qpu)
            | SplatComposer(bqm),
        ) | ArgMinFold(), max_iter=100, convergence=10)),

    ("qbsolv-classic",
        lambda bqm, **kw: QBSolvProblemSampler(bqm)),

    ("qbsolv-qpu",
        lambda bqm, qpu, **kw: QBSolvProblemSampler(bqm, qpu=qpu)),

]


def run(problems, solver_factories):
    results = OrderedDict()

    # reuse the cloud client
    qpu = DWaveSampler()

    for problem in problems:
        results[problem] = OrderedDict()

        with open(problem) as fp:
            bqm = dimod.BinaryQuadraticModel.from_coo(fp)

        for name, factory in solver_factories:
            case = '{!r} with {!r}'.format(problem, name)

            try:
                solver = factory(bqm=bqm, qpu=qpu)
                init_state = State.from_sample(min_sample(bqm), bqm)

                with tictoc(case) as timer:
                    solution = solver.run(init_state).result()

            except Exception as exc:
                print("FAILED {case}: {exc!r}".format(**locals()))
                results[problem][name] = repr(exc)

            else:
                print("case={case!r}"
                      " energy={solution.samples.first.energy!r},"
                      " wallclock={timer.dt!r}".format(**locals()))
                results[problem][name] = dict(
                    energy=solution.samples.first.energy,
                    wallclock=timer.dt)

    return results


if __name__ == "__main__":
    import json
    results = run(problems, solver_factories)
    print(json.dumps(results), file=sys.stderr)
