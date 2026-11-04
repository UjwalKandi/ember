"""One self-contained simulated episode: scenario -> wired swarm -> run to assessment time."""
from __future__ import annotations

import random
from dataclasses import dataclass

from . import sim
from .swarm import Swarm
from .tools import Toolbox

AREAS = ("Sector A", "Ridge North", "Pine Valley", "East Canyon", "Lakeside Grid", "Oak Hollow", "Cedar Flats")


@dataclass
class Episode:
    scenario: str
    seed: int
    net: sim.SimNetwork
    swarm: Swarm
    tools: Toolbox
    truth: sim.Truth


def make_episode(scenario: str, seed: int) -> Episode:
    rng = random.Random(f"{scenario}:{seed}")
    net = sim.build(scenario, seed)
    swarm = Swarm(net.node_positions(), net.wind, clock=lambda: net.t, area=rng.choice(AREAS))
    net.on_rx = swarm.on_rx
    swarm.transports.append(net)
    net.run(sim.assessment_time(scenario, rng))
    truth = sim.finalize_truth(net)
    return Episode(scenario, seed, net, swarm, Toolbox(swarm, timeout=0.0), truth)
