"""Exact single-purchase card enumeration."""

from __future__ import annotations

from collections.abc import Sequence

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.feasibility import analyze_assignment, validate_scenario
from engine.models import (
    CandidateDecision,
    Card,
    Intent,
    IssueCode,
    OptimizationIssue,
    OptimizationStatus,
    Purchase,
    RecommendationResult,
)
from engine.objective import objective_from_factors, quantize_intent_weights
from engine.scoring import factor_breakdown

__all__ = ["recommend_purchase"]


def _lock_exclusion(purchase: Purchase, card: Card) -> OptimizationIssue:
    assert purchase.locked_card_id is not None
    return OptimizationIssue(
        code=IssueCode.PURCHASE_LOCKED_TO_OTHER_CARD,
        message=f"Purchase {purchase.id} is locked to {purchase.locked_card_id}.",
        suggestion="Unlock the purchase to consider this card.",
        card_ids=[card.id, purchase.locked_card_id],
        purchase_ids=[purchase.id],
    )


def recommend_purchase(
    cards: Sequence[Card],
    purchase: Purchase,
    intent: Intent,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> RecommendationResult:
    """Return the exact best feasible card under the modeled one-purchase horizon."""

    scenario_issues = validate_scenario(cards, [purchase], intent)
    reference_failures = {
        IssueCode.DUPLICATE_ID,
        IssueCode.UNKNOWN_LOCKED_CARD,
        IssueCode.UNKNOWN_BONUS_CARD,
        IssueCode.CARD_HAS_NO_BONUS,
    }
    if any(problem.code in reference_failures for problem in scenario_issues):
        return RecommendationResult(
            status=OptimizationStatus.INFEASIBLE,
            issues=scenario_issues,
        )

    weights_ppm = quantize_intent_weights(intent, config)
    feasible: list[CandidateDecision] = []
    excluded: list[CandidateDecision] = []
    for card in sorted(cards, key=lambda item: item.id):
        if purchase.locked_card_id is not None and card.id != purchase.locked_card_id:
            excluded.append(
                CandidateDecision(
                    card_id=card.id,
                    feasible=False,
                    issues=[_lock_exclusion(purchase, card)],
                )
            )
            continue

        report = analyze_assignment(cards, [purchase], intent, {purchase.id: card.id})
        if not report.feasible:
            excluded.append(
                CandidateDecision(card_id=card.id, feasible=False, issues=list(report.issues))
            )
            continue

        factors = factor_breakdown(card, purchase, config)
        feasible.append(
            CandidateDecision(
                card_id=card.id,
                feasible=True,
                raw_factors=factors,
                objective=objective_from_factors(factors, weights_ppm),
            )
        )

    feasible.sort(
        key=lambda item: (-item.objective.total_utility, item.card_id)  # type: ignore[union-attr]
    )
    ranked = [
        candidate.model_copy(update={"rank": index})
        for index, candidate in enumerate(feasible, 1)
    ]
    if not ranked:
        issues = list(scenario_issues)
        issues.append(
            OptimizationIssue(
                code=IssueCode.NO_FEASIBLE_ASSIGNMENT,
                message=f"No card can accept purchase {purchase.id} under all hard constraints.",
                suggestion="Review the excluded-card issues and relax one explicit constraint.",
                purchase_ids=[purchase.id],
            )
        )
        return RecommendationResult(
            status=OptimizationStatus.INFEASIBLE,
            excluded_cards=excluded,
            issues=issues,
        )

    return RecommendationResult(
        status=OptimizationStatus.OPTIMAL,
        winner=ranked[0],
        runner_up=ranked[1] if len(ranked) > 1 else None,
        candidates=ranked,
        excluded_cards=excluded,
    )
