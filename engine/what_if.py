"""Reoptimized one-purchase card override and metric deltas."""

from __future__ import annotations

from collections.abc import Sequence

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.greedy import allocate_greedy, metric_delta
from engine.ilp import allocate_ilp
from engine.models import (
    AllocationResult,
    AssignmentChange,
    Card,
    Intent,
    IssueCode,
    OptimizationIssue,
    OptimizationStatus,
    Purchase,
    SolverMethod,
    WhatIfResult,
)


def _solve(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    method: SolverMethod,
    config: EngineConfig,
) -> AllocationResult:
    if method is SolverMethod.ILP:
        return allocate_ilp(cards, purchases, intent, config)
    if method is SolverMethod.GREEDY:
        return allocate_greedy(cards, purchases, intent, config)
    raise ValueError(f"unsupported what-if solver method: {method.value}")


def _failed_override(
    method: SolverMethod,
    code: IssueCode,
    message: str,
    suggestion: str,
    *,
    purchase_ids: list[str] | None = None,
    card_ids: list[str] | None = None,
) -> AllocationResult:
    return AllocationResult(
        status=OptimizationStatus.INFEASIBLE,
        solver_method=method,
        issues=[
            OptimizationIssue(
                code=code,
                message=message,
                suggestion=suggestion,
                purchase_ids=purchase_ids or [],
                card_ids=card_ids or [],
            )
        ],
    )


def run_what_if(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    purchase_id: str,
    override_card_id: str,
    method: SolverMethod = SolverMethod.ILP,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> WhatIfResult:
    """Lock one purchase to a new card and reoptimize every other assignment."""

    base = _solve(cards, purchases, intent, method, config)
    purchases_by_id = {purchase.id: purchase for purchase in purchases}
    card_ids = {card.id for card in cards}
    if purchase_id not in purchases_by_id:
        override = _failed_override(
            method,
            IssueCode.UNKNOWN_PURCHASE,
            f"Purchase {purchase_id} is not in this scenario.",
            "Choose a purchase in the current plan.",
            purchase_ids=[purchase_id],
        )
        return WhatIfResult(
            purchase_id=purchase_id,
            override_card_id=override_card_id,
            base_result=base,
            override_result=override,
        )
    if override_card_id not in card_ids:
        override = _failed_override(
            method,
            IssueCode.UNKNOWN_ASSIGNED_CARD,
            f"Card {override_card_id} is not in this portfolio.",
            "Choose a card in the current portfolio.",
            purchase_ids=[purchase_id],
            card_ids=[override_card_id],
        )
        return WhatIfResult(
            purchase_id=purchase_id,
            override_card_id=override_card_id,
            base_result=base,
            override_result=override,
        )

    overridden = [
        purchase.model_copy(update={"locked_card_id": override_card_id})
        if purchase.id == purchase_id
        else purchase.model_copy(deep=True)
        for purchase in purchases
    ]
    override = _solve(cards, overridden, intent, method, config)
    if not base.successful or not override.successful:
        return WhatIfResult(
            purchase_id=purchase_id,
            override_card_id=override_card_id,
            base_result=base,
            override_result=override,
        )

    assert base.metrics is not None
    assert override.metrics is not None
    base_map = {
        assignment.purchase_id: assignment.card_id for assignment in base.assignments
    }
    override_map = {
        assignment.purchase_id: assignment.card_id for assignment in override.assignments
    }
    changes = [
        AssignmentChange(
            purchase_id=current_purchase_id,
            base_card_id=base_map[current_purchase_id],
            override_card_id=override_map[current_purchase_id],
        )
        for current_purchase_id in sorted(base_map)
        if base_map[current_purchase_id] != override_map[current_purchase_id]
    ]
    return WhatIfResult(
        purchase_id=purchase_id,
        override_card_id=override_card_id,
        base_result=base,
        override_result=override,
        deltas=metric_delta(override.metrics, base.metrics),
        changed_assignments=changes,
    )
