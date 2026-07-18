"""Stable public facade for implemented optimization operations."""

from collections.abc import Sequence

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.greedy import allocate_greedy
from engine.ilp import allocate_ilp
from engine.models import AllocationResult, Card, Intent, Purchase, SolverMethod
from engine.pareto import sample_frontier
from engine.recommend import recommend_purchase
from engine.what_if import run_what_if

__all__ = [
    "allocate_month",
    "recommend_purchase",
    "run_what_if",
    "sample_frontier",
]


def allocate_month(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    method: SolverMethod = SolverMethod.GREEDY,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> AllocationResult:
    if method is SolverMethod.GREEDY:
        return allocate_greedy(cards, purchases, intent, config)
    if method is SolverMethod.ILP:
        return allocate_ilp(cards, purchases, intent, config)
    raise ValueError(f"unsupported monthly solver method: {method.value}")
