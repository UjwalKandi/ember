"""One self-contained simulated episode: scenario -> wired swarm -> run to assessment time."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from . import sim
from .swarm import Swarm
from .tools import Toolbox

BUDGETS = ("generous", "tight", "exhausted")

AREAS = ("Sector A", "Ridge North", "Pine Valley", "East Canyon", "Lakeside Grid", "Oak Hollow", "Cedar Flats")


@dataclass
class Episode:
    scenario: str
    seed: int
    net: sim.SimNetwork
    swarm: Swarm
    tools: Toolbox
    truth: sim.Truth
    budget: str = "generous"


def apply_budget(swarm: Swarm, budget: str, rng: random.Random) -> None:
    """Pre-load the airtime ledger as if earlier traffic this hour had used part of the duty cycle."""
    total = swarm.airtime_budget_ms
    left = {"generous": rng.uniform(total - 12_000, total),
            "tight": rng.uniform(1_600, 4_000),          # roughly one direct CONFIRM plus an alert
            "exhausted": rng.uniform(0, 600)}[budget]
    swarm.spend(total - left)


def make_episode(scenario: str, seed: int, budget: Optional[str] = None) -> Episode:
    rng = random.Random(f"{scenario}:{seed}")
    net = sim.build(scenario, seed)
    swarm = Swarm(net.node_positions(), net.wind, clock=lambda: net.t, area=rng.choice(AREAS))
    net.on_rx = swarm.on_rx
    swarm.transports.append(net)
    net.run(sim.assessment_time(scenario, rng))
    truth = sim.finalize_truth(net)
    if budget is None:
        budget = rng.choices(BUDGETS, weights=(0.6, 0.25, 0.15))[0]
    apply_budget(swarm, budget, rng)
    return Episode(scenario, seed, net, swarm, Toolbox(swarm, timeout=0.0), truth, budget)
