"""Stable public facade for implemented optimization operations."""

from collections.abc import Sequence

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.greedy import allocate_greedy
from engine.models import AllocationResult, Card, Intent, Purchase, SolverMethod
from engine.recommend import recommend_purchase

__all__ = ["allocate_month", "recommend_purchase"]


def allocate_month(
	cards: Sequence[Card],
	purchases: Sequence[Purchase],
	intent: Intent,
	method: SolverMethod = SolverMethod.GREEDY,
	config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> AllocationResult:
	if method is not SolverMethod.GREEDY:
		raise NotImplementedError("the ILP allocator has not been implemented yet")
	return allocate_greedy(cards, purchases, intent, config)

