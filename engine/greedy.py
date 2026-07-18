"""Deterministic greedy monthly allocation with bounded repair and local search."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.feasibility import (
    analyze_assignment,
    candidate_assignment_issues,
    validate_scenario,
)
from engine.models import (
    AllocationMetrics,
    AllocationResult,
    AssignmentAlternative,
    Card,
    Intent,
    IssueCode,
    MetricDelta,
    OptimizationIssue,
    OptimizationStatus,
    Purchase,
    PurchaseAssignment,
    SolverMethod,
)
from engine.objective import PlanEvaluation, evaluate_plan


def _allocation_key(assignments: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(assignments.items()))


def _forced_bonuses_remain_reachable(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
) -> bool:
    cards_by_id = {card.id: card for card in cards}
    assigned_purchase_ids = set(assignments)
    for card_id in intent.constraints.must_hit_bonus_card_ids:
        card = cards_by_id[card_id]
        bonus = card.signup_bonus
        assert bonus is not None
        remaining = bonus.remaining_spend_cents
        if remaining == 0:
            continue
        assigned_to_card = [
            purchase
            for purchase in purchases
            if assignments.get(purchase.id) == card_id
        ]
        assigned_all_spend = sum(purchase.amount_cents for purchase in assigned_to_card)
        assigned_eligible = sum(
            purchase.amount_cents
            for purchase in assigned_to_card
            if purchase.date <= bonus.deadline_date
        )
        unassigned_eligible = sum(
            purchase.amount_cents
            for purchase in purchases
            if purchase.id not in assigned_purchase_ids
            and purchase.date <= bonus.deadline_date
            and purchase.locked_card_id in (None, card_id)
        )
        remaining_credit = max(
            0,
            card.credit_limit_cents - card.current_balance_cents - assigned_all_spend,
        )
        possible_additional = min(unassigned_eligible, remaining_credit)
        if assigned_eligible + possible_additional < remaining:
            return False
    return True


def _candidate_cards(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
    purchase: Purchase,
) -> list[Card]:
    candidates: list[Card] = []
    for card in sorted(cards, key=lambda item: item.id):
        if candidate_assignment_issues(
            cards, purchases, intent, assignments, purchase, card
        ):
            continue
        tentative = dict(assignments)
        tentative[purchase.id] = card.id
        if _forced_bonuses_remain_reachable(cards, purchases, intent, tentative):
            candidates.append(card)
    return candidates


def _best_card_for_purchase(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
    purchase: Purchase,
    config: EngineConfig,
) -> str | None:
    best_card_id: str | None = None
    best_utility: int | None = None
    for card in _candidate_cards(cards, purchases, intent, assignments, purchase):
        tentative = dict(assignments)
        tentative[purchase.id] = card.id
        utility = evaluate_plan(
            list(cards), list(purchases), intent, tentative, config
        ).objective.total_utility
        if best_utility is None or utility > best_utility:
            best_utility = utility
            best_card_id = card.id
    return best_card_id


def _repair_dead_end(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
    blocked: Purchase,
    config: EngineConfig,
) -> dict[str, str] | None:
    purchases_by_id = {purchase.id: purchase for purchase in purchases}
    movable_ids = sorted(
        purchase_id
        for purchase_id in assignments
        if purchases_by_id[purchase_id].locked_card_id is None
    )
    best: dict[str, str] | None = None
    best_utility: int | None = None
    best_key: tuple[tuple[str, str], ...] | None = None

    for size in range(1, min(config.greedy_repair_depth, len(movable_ids)) + 1):
        for displaced_ids in combinations(movable_ids, size):
            base = {key: value for key, value in assignments.items() if key not in displaced_ids}
            displaced = [purchases_by_id[purchase_id] for purchase_id in displaced_ids]
            to_place = sorted(
                [*displaced, blocked],
                key=lambda item: (-item.amount_cents, item.id),
            )

            def search(
                index: int,
                partial: dict[str, str],
                items: tuple[Purchase, ...] = tuple(to_place),
            ) -> None:
                nonlocal best, best_key, best_utility
                if index == len(items):
                    utility = evaluate_plan(
                        list(cards), list(purchases), intent, partial, config
                    ).objective.total_utility
                    key = _allocation_key(partial)
                    if (
                        best_utility is None
                        or utility > best_utility
                        or (utility == best_utility and (best_key is None or key < best_key))
                    ):
                        best = dict(partial)
                        best_utility = utility
                        best_key = key
                    return

                item = items[index]
                for card in _candidate_cards(cards, purchases, intent, partial, item):
                    partial[item.id] = card.id
                    search(index + 1, partial)
                    del partial[item.id]

            search(0, dict(base))
        if best is not None:
            return best
    return None


def _best_relocation(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
    current_utility: int,
    config: EngineConfig,
) -> dict[str, str] | None:
    best: dict[str, str] | None = None
    best_utility = current_utility
    best_key: tuple[tuple[str, str], ...] | None = None
    for purchase in sorted(purchases, key=lambda item: item.id):
        if purchase.locked_card_id is not None:
            continue
        without = dict(assignments)
        current_card_id = without.pop(purchase.id)
        for card in _candidate_cards(cards, purchases, intent, without, purchase):
            if card.id == current_card_id:
                continue
            tentative = dict(without)
            tentative[purchase.id] = card.id
            if not analyze_assignment(cards, purchases, intent, tentative).feasible:
                continue
            utility = evaluate_plan(
                list(cards), list(purchases), intent, tentative, config
            ).objective.total_utility
            key = _allocation_key(tentative)
            if utility > best_utility or (
                utility == best_utility
                and utility > current_utility
                and (best_key is None or key < best_key)
            ):
                best = tentative
                best_utility = utility
                best_key = key
    return best


def _best_swap(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
    current_utility: int,
    config: EngineConfig,
) -> dict[str, str] | None:
    movable = sorted(
        (purchase for purchase in purchases if purchase.locked_card_id is None),
        key=lambda item: item.id,
    )
    best: dict[str, str] | None = None
    best_utility = current_utility
    best_key: tuple[tuple[str, str], ...] | None = None
    for first, second in combinations(movable, 2):
        first_card = assignments[first.id]
        second_card = assignments[second.id]
        if first_card == second_card:
            continue
        tentative = dict(assignments)
        tentative[first.id] = second_card
        tentative[second.id] = first_card
        if not analyze_assignment(cards, purchases, intent, tentative).feasible:
            continue
        utility = evaluate_plan(
            list(cards), list(purchases), intent, tentative, config
        ).objective.total_utility
        key = _allocation_key(tentative)
        if utility > best_utility or (
            utility == best_utility
            and utility > current_utility
            and (best_key is None or key < best_key)
        ):
            best = tentative
            best_utility = utility
            best_key = key
    return best


def _local_search(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
    config: EngineConfig,
) -> dict[str, str]:
    current = dict(assignments)
    for _ in range(config.local_search_max_passes):
        current_utility = evaluate_plan(
            list(cards), list(purchases), intent, current, config
        ).objective.total_utility
        relocation = _best_relocation(
            cards, purchases, intent, current, current_utility, config
        )
        if relocation is not None:
            current = relocation
            continue
        swap = _best_swap(cards, purchases, intent, current, current_utility, config)
        if swap is not None:
            current = swap
            continue
        break
    return current


def metric_delta(override: AllocationMetrics, base: AllocationMetrics) -> MetricDelta:
    return MetricDelta(
        cashback_cents=override.cashback_cents - base.cashback_cents,
        travel_value_cents=override.travel_value_cents - base.travel_value_cents,
        signup_progress_cents=override.signup_progress_cents - base.signup_progress_cents,
        signup_bonus_earned_cents=(
            override.signup_bonus_earned_cents - base.signup_bonus_earned_cents
        ),
        signup_goal_points=override.signup_goal_points - base.signup_goal_points,
        projected_reward_value_cents=(
            override.projected_reward_value_cents - base.projected_reward_value_cents
        ),
        max_card_utilization_bps=(
            override.max_card_utilization_bps - base.max_card_utilization_bps
        ),
        credit_penalty_points=override.credit_penalty_points - base.credit_penalty_points,
        risk_penalty_points=override.risk_penalty_points - base.risk_penalty_points,
        cashflow_days_total=override.cashflow_days_total - base.cashflow_days_total,
        cashflow_value_cents=override.cashflow_value_cents - base.cashflow_value_cents,
        total_utility=override.total_utility - base.total_utility,
    )


def attach_alternatives(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
    evaluation: PlanEvaluation,
    config: EngineConfig,
) -> list[PurchaseAssignment]:
    enriched: list[PurchaseAssignment] = []
    for assignment in evaluation.assignments:
        alternatives: list[AssignmentAlternative] = []
        for card in sorted(cards, key=lambda item: item.id):
            if card.id == assignment.card_id:
                continue
            moved = dict(assignments)
            moved[assignment.purchase_id] = card.id
            report = analyze_assignment(cards, purchases, intent, moved)
            if not report.feasible:
                alternatives.append(
                    AssignmentAlternative(
                        card_id=card.id,
                        feasible=False,
                        issues=list(report.issues),
                    )
                )
                continue
            moved_evaluation = evaluate_plan(
                list(cards), list(purchases), intent, moved, config
            )
            alternatives.append(
                AssignmentAlternative(
                    card_id=card.id,
                    feasible=True,
                    resulting_plan_utility=moved_evaluation.objective.total_utility,
                    total_utility_delta=(
                        moved_evaluation.objective.total_utility
                        - evaluation.objective.total_utility
                    ),
                    metric_deltas=metric_delta(moved_evaluation.metrics, evaluation.metrics),
                )
            )
        alternatives.sort(
            key=lambda item: (
                not item.feasible,
                -(item.resulting_plan_utility or 0),
                item.card_id,
            )
        )
        enriched.append(assignment.model_copy(update={"alternatives": alternatives}))
    return enriched


def allocate_greedy(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> AllocationResult:
    """Build and locally improve a deterministic feasible monthly assignment."""

    scenario_issues = validate_scenario(cards, purchases, intent)
    if scenario_issues:
        return AllocationResult(
            status=OptimizationStatus.INFEASIBLE,
            solver_method=SolverMethod.GREEDY,
            issues=scenario_issues,
        )

    assignments: dict[str, str] = {}
    ordered = sorted(
        purchases,
        key=lambda item: (
            item.locked_card_id is None,
            -item.amount_cents,
            item.id,
        ),
    )
    for purchase in ordered:
        card_id = _best_card_for_purchase(
            cards, purchases, intent, assignments, purchase, config
        )
        if card_id is not None:
            assignments[purchase.id] = card_id
            continue
        repaired = _repair_dead_end(
            cards, purchases, intent, assignments, purchase, config
        )
        if repaired is None:
            return AllocationResult(
                status=OptimizationStatus.UNRESOLVED,
                solver_method=SolverMethod.GREEDY,
                issues=[
                    OptimizationIssue(
                        code=IssueCode.HEURISTIC_DEAD_END,
                        message=(
                            f"The greedy search could not place purchase {purchase.id} "
                            "within its repair depth."
                        ),
                        suggestion="Try the exact ILP solver or relax an explicit constraint.",
                        purchase_ids=[purchase.id],
                    )
                ],
            )
        assignments = repaired

    complete_report = analyze_assignment(cards, purchases, intent, assignments)
    if not complete_report.feasible:
        return AllocationResult(
            status=OptimizationStatus.UNRESOLVED,
            solver_method=SolverMethod.GREEDY,
            issues=[
                *complete_report.issues,
                OptimizationIssue(
                    code=IssueCode.HEURISTIC_DEAD_END,
                    message="The greedy assignment did not satisfy every hard constraint.",
                    suggestion="Try the exact ILP solver or relax an explicit constraint.",
                ),
            ],
        )

    assignments = _local_search(cards, purchases, intent, assignments, config)
    final_report = analyze_assignment(cards, purchases, intent, assignments)
    if not final_report.feasible:
        raise AssertionError("local search returned an infeasible assignment")
    evaluation = evaluate_plan(list(cards), list(purchases), intent, assignments, config)
    return AllocationResult(
        status=OptimizationStatus.HEURISTIC,
        solver_method=SolverMethod.GREEDY,
        assignments=attach_alternatives(
            cards, purchases, intent, assignments, evaluation, config
        ),
        card_summaries=list(evaluation.card_summaries),
        metrics=evaluation.metrics,
    )
