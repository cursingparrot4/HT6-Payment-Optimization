"""Fixed-point intent weighting and objective composition helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.models import (
    AllocationMetrics,
    Card,
    CardPlanSummary,
    ConstraintKind,
    ConstraintSlack,
    Goal,
    Intent,
    ObjectiveBreakdown,
    Purchase,
    PurchaseAssignment,
    RawFactorBreakdown,
)
from engine.scoring import (
    cashflow_days,
    cashflow_value_cents,
    incremental_risk_penalty_points,
    incremental_utilization_penalty_points,
    reward_values,
    signup_bonus_factors,
    utilization_after,
)


@dataclass(frozen=True, slots=True)
class PlanEvaluation:
    objective: ObjectiveBreakdown
    metrics: AllocationMetrics
    assignments: tuple[PurchaseAssignment, ...]
    card_summaries: tuple[CardPlanSummary, ...]


def _parse_goal_mapping(values: Mapping[Goal | str, Any], *, name: str) -> dict[Goal, Any]:
    parsed: dict[Goal, Any] = {}
    for raw_goal, value in values.items():
        try:
            goal = raw_goal if isinstance(raw_goal, Goal) else Goal(raw_goal)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contains unknown goal: {raw_goal!r}") from exc
        if goal in parsed:
            raise ValueError(f"{name} contains duplicate goal: {goal.value}")
        parsed[goal] = value

    expected = set(Goal)
    if set(parsed) != expected:
        missing = sorted(goal.value for goal in expected - set(parsed))
        raise ValueError(f"{name} must contain every goal; missing={missing}")
    return {goal: parsed[goal] for goal in Goal}


def quantize_weights(
    weights: Mapping[Goal | str, int | float | Decimal],
    *,
    scale: int = DEFAULT_ENGINE_CONFIG.weight_scale_ppm,
) -> dict[Goal, int]:
    """Normalize positive weights and allocate an exact integer ppm total.

    Largest-remainder ties are resolved by canonical ``Goal`` enum order.
    """

    if isinstance(scale, bool) or not isinstance(scale, int) or scale <= 0:
        raise ValueError("scale must be a positive integer")
    parsed = _parse_goal_mapping(weights, name="weights")

    decimals: dict[Goal, Decimal] = {}
    for goal, raw_weight in parsed.items():
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float, Decimal)):
            raise ValueError(f"weight for {goal.value} must be numeric")
        try:
            weight = Decimal(str(raw_weight))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"weight for {goal.value} is invalid") from exc
        if not weight.is_finite() or weight < 0:
            raise ValueError(f"weight for {goal.value} must be finite and nonnegative")
        decimals[goal] = weight

    total = sum(decimals.values(), start=Decimal(0))
    if total <= 0:
        raise ValueError("at least one weight must be positive")

    exact = {goal: decimals[goal] * scale / total for goal in Goal}
    floors = {
        goal: int(exact[goal].to_integral_value(rounding=ROUND_FLOOR)) for goal in Goal
    }
    remaining = scale - sum(floors.values())
    goal_index = {goal: index for index, goal in enumerate(Goal)}
    remainder_order = sorted(
        Goal,
        key=lambda goal: (-(exact[goal] - floors[goal]), goal_index[goal]),
    )
    for goal in remainder_order[:remaining]:
        floors[goal] += 1

    if sum(floors.values()) != scale:
        raise AssertionError("largest-remainder quantization did not preserve the configured scale")
    return floors


def quantize_intent_weights(
    intent: Intent,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> dict[Goal, int]:
    return quantize_weights(intent.weights, scale=config.weight_scale_ppm)


def apply_goal_weights(
    unweighted_utility_by_goal: Mapping[Goal | str, int],
    weights_ppm: Mapping[Goal | str, int],
    *,
    expected_scale: int = DEFAULT_ENGINE_CONFIG.weight_scale_ppm,
) -> ObjectiveBreakdown:
    """Apply canonical integer weights to signed integer utility factors."""

    unweighted = _parse_goal_mapping(unweighted_utility_by_goal, name="unweighted utility")
    weights = _parse_goal_mapping(weights_ppm, name="weights_ppm")

    for goal in Goal:
        utility = unweighted[goal]
        weight = weights[goal]
        if isinstance(utility, bool) or not isinstance(utility, int):
            raise ValueError(f"unweighted utility for {goal.value} must be an integer")
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
            raise ValueError(f"ppm weight for {goal.value} must be a nonnegative integer")
    if sum(weights.values()) != expected_scale:
        raise ValueError(f"ppm weights must sum exactly to {expected_scale}")

    contributions = {goal: unweighted[goal] * weights[goal] for goal in Goal}
    return ObjectiveBreakdown(
        utility_by_goal=contributions,
        total_utility=sum(contributions.values()),
    )


def objective_from_factors(
    factors: RawFactorBreakdown,
    weights_ppm: Mapping[Goal | str, int],
) -> ObjectiveBreakdown:
    """Map one candidate's raw facts to signed utility, then apply weights."""

    unweighted = {
        Goal.MAX_CASHBACK: factors.cashback_cents,
        Goal.MAX_TRAVEL: factors.travel_value_cents,
        Goal.CREDIT_HEALTH: -factors.credit_penalty_points,
        Goal.HIT_SIGNUP_BONUS: factors.signup_goal_points,
        Goal.MAX_CASHFLOW: factors.cashflow_value_cents,
        Goal.MIN_RISK: -factors.risk_penalty_points,
    }
    return apply_goal_weights(unweighted, weights_ppm)


def _ceiling_active(purchases: list[Purchase], intent: Intent) -> bool:
    constraints = intent.constraints
    if constraints.max_utilization_bps is None:
        return False
    cutoff = constraints.max_utilization_until
    return cutoff is None or any(purchase.date <= cutoff for purchase in purchases)


def _metric_factors_for_assignment(
    card: Card,
    purchase: Purchase,
    ending_utilization_bps: int,
    config: EngineConfig,
) -> RawFactorBreakdown:
    rewards = reward_values(card, purchase)
    bonus_eligible = int(
        card.signup_bonus is not None and purchase.date <= card.signup_bonus.deadline_date
    ) * purchase.amount_cents
    return RawFactorBreakdown(
        cashback_cents=rewards.cashback_cents,
        travel_value_cents=rewards.travel_value_cents,
        signup_eligible_spend_cents=bonus_eligible,
        signup_progress_cents=0,
        signup_bonus_earned_cents=0,
        signup_goal_points=0,
        cashflow_days=cashflow_days(card, purchase),
        cashflow_value_cents=cashflow_value_cents(card, purchase, config),
        utilization_before_bps=utilization_after(card, 0),
        utilization_after_bps=ending_utilization_bps,
        credit_penalty_points=0,
        risk_penalty_points=0,
    )


def evaluate_plan(
    cards: list[Card],
    purchases: list[Purchase],
    intent: Intent,
    assignment_map: Mapping[str, str],
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> PlanEvaluation:
    """Evaluate a complete or partial assignment without applying hard feasibility."""

    cards_by_id = {card.id: card for card in cards}
    purchases_by_id = {purchase.id: purchase for purchase in purchases}
    unknown_purchases = set(assignment_map) - set(purchases_by_id)
    unknown_cards = set(assignment_map.values()) - set(cards_by_id)
    if unknown_purchases:
        raise ValueError(f"assignment contains unknown purchases: {sorted(unknown_purchases)}")
    if unknown_cards:
        raise ValueError(f"assignment contains unknown cards: {sorted(unknown_cards)}")

    assigned_by_card: dict[str, list[Purchase]] = {card.id: [] for card in cards}
    for purchase_id, card_id in assignment_map.items():
        assigned_by_card[card_id].append(purchases_by_id[purchase_id])
    for assigned in assigned_by_card.values():
        assigned.sort(key=lambda item: (item.date, item.id))

    weights_ppm = quantize_intent_weights(intent, config)
    cashback_total = 0
    travel_total = 0
    cashflow_days_total = 0
    cashflow_value_total = 0
    signup_progress_total = 0
    signup_earned_total = 0
    signup_goal_points_total = 0
    signup_hit_count = 0
    credit_penalty_total = 0
    risk_penalty_total = 0
    max_utilization_bps = 0
    card_summaries: list[CardPlanSummary] = []
    ceiling_active = _ceiling_active(purchases, intent)

    for card in sorted(cards, key=lambda item: item.id):
        assigned = assigned_by_card[card.id]
        assigned_spend = sum(purchase.amount_cents for purchase in assigned)
        for purchase in assigned:
            rewards = reward_values(card, purchase)
            cashback_total += rewards.cashback_cents
            travel_total += rewards.travel_value_cents
            cashflow_days_total += cashflow_days(card, purchase)
            cashflow_value_total += cashflow_value_cents(card, purchase, config)

        bonus = signup_bonus_factors(card, assigned, config)
        signup_progress_total += bonus.progress_cents
        signup_earned_total += bonus.bonus_earned_cents
        signup_goal_points_total += bonus.goal_points
        signup_hit_count += int(bonus.bonus_earned_cents > 0)

        credit_penalty = incremental_utilization_penalty_points(card, assigned_spend, config)
        risk_penalty = incremental_risk_penalty_points(card, assigned_spend, config)
        credit_penalty_total += credit_penalty
        risk_penalty_total += risk_penalty
        ending_utilization = utilization_after(card, assigned_spend)
        max_utilization_bps = max(max_utilization_bps, ending_utilization)
        credit_slack = max(
            0,
            max(0, card.credit_limit_cents - card.current_balance_cents) - assigned_spend,
        )
        constraint_slacks = [
            ConstraintSlack(
                kind=ConstraintKind.CREDIT_LIMIT,
                card_id=card.id,
                slack_cents=credit_slack,
                binding=credit_slack == 0,
                near_binding=0 < credit_slack <= config.near_binding_threshold_cents,
            )
        ]

        utilization_slack: int | None = None
        if ceiling_active:
            ceiling = intent.constraints.max_utilization_bps
            assert ceiling is not None
            maximum_balance = ceiling * card.credit_limit_cents // 10_000
            dated_spend = sum(
                purchase.amount_cents
                for purchase in assigned
                if intent.constraints.max_utilization_until is None
                or purchase.date <= intent.constraints.max_utilization_until
            )
            utilization_slack = max(
                0,
                maximum_balance - card.current_balance_cents - dated_spend,
            )
            constraint_slacks.append(
                ConstraintSlack(
                    kind=ConstraintKind.UTILIZATION_CEILING,
                    card_id=card.id,
                    slack_cents=utilization_slack,
                    binding=utilization_slack == 0,
                    near_binding=(
                        0 < utilization_slack <= config.near_binding_threshold_cents
                    ),
                )
            )

        bonus_remaining: int | None = None
        bonus_hit: bool | None = None
        if card.signup_bonus is not None:
            bonus_remaining = max(
                0,
                card.signup_bonus.remaining_spend_cents - bonus.progress_cents,
            )
            bonus_hit = bonus.bonus_hit
            if card.id in intent.constraints.must_hit_bonus_card_ids and bonus_remaining == 0:
                bonus_slack = max(
                    0,
                    bonus.eligible_spend_cents - card.signup_bonus.remaining_spend_cents,
                )
                constraint_slacks.append(
                    ConstraintSlack(
                        kind=ConstraintKind.SIGNUP_BONUS,
                        card_id=card.id,
                        slack_cents=bonus_slack,
                        binding=bonus_slack == 0,
                        near_binding=(
                            0 < bonus_slack <= config.near_binding_threshold_cents
                        ),
                    )
                )

        card_summaries.append(
            CardPlanSummary(
                card_id=card.id,
                assigned_purchase_ids=[purchase.id for purchase in assigned],
                assigned_spend_cents=assigned_spend,
                ending_balance_cents=card.current_balance_cents + assigned_spend,
                ending_utilization_bps=ending_utilization,
                credit_limit_slack_cents=credit_slack,
                utilization_slack_cents=utilization_slack,
                bonus_eligible_spend_cents=bonus.eligible_spend_cents,
                bonus_progress_cents=bonus.progress_cents,
                bonus_remaining_cents=bonus_remaining,
                bonus_hit=bonus_hit,
                cashflow_days_total=sum(cashflow_days(card, purchase) for purchase in assigned),
                constraint_slacks=constraint_slacks,
            )
        )

    unweighted = {
        Goal.MAX_CASHBACK: cashback_total,
        Goal.MAX_TRAVEL: travel_total,
        Goal.CREDIT_HEALTH: -credit_penalty_total,
        Goal.HIT_SIGNUP_BONUS: signup_goal_points_total,
        Goal.MAX_CASHFLOW: cashflow_value_total,
        Goal.MIN_RISK: -risk_penalty_total,
    }
    objective = apply_goal_weights(unweighted, weights_ppm)
    metrics = AllocationMetrics(
        cashback_cents=cashback_total,
        travel_value_cents=travel_total,
        signup_progress_cents=signup_progress_total,
        signup_bonus_earned_cents=signup_earned_total,
        signup_goal_points=signup_goal_points_total,
        signup_bonus_hit_count=signup_hit_count,
        projected_reward_value_cents=cashback_total + travel_total + signup_earned_total,
        max_card_utilization_bps=max_utilization_bps,
        credit_penalty_points=credit_penalty_total,
        risk_penalty_points=risk_penalty_total,
        cashflow_days_total=cashflow_days_total,
        cashflow_value_cents=cashflow_value_total,
        total_utility=objective.total_utility,
    )

    purchase_assignments: list[PurchaseAssignment] = []
    summary_by_card = {summary.card_id: summary for summary in card_summaries}
    for purchase in sorted(
        (item for item in purchases if item.id in assignment_map),
        key=lambda item: (item.date, item.id),
    ):
        card = cards_by_id[assignment_map[purchase.id]]
        factors = _metric_factors_for_assignment(
            card,
            purchase,
            summary_by_card[card.id].ending_utilization_bps,
            config,
        )
        purchase_assignments.append(
            PurchaseAssignment(
                purchase_id=purchase.id,
                card_id=card.id,
                raw_factors=factors,
                objective=objective_from_factors(factors, weights_ppm),
            )
        )

    return PlanEvaluation(
        objective=objective,
        metrics=metrics,
        assignments=tuple(purchase_assignments),
        card_summaries=tuple(card_summaries),
    )

