"""Pure integer scoring facts for cards, purchases, and aggregate card state."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.dates import interest_free_float_days
from engine.models import Card, Purchase, RawFactorBreakdown, RewardType


def _require_nonnegative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class RewardValues:
    cashback_cents: int
    travel_value_cents: int


@dataclass(frozen=True, slots=True)
class SignupBonusFactors:
    eligible_spend_cents: int
    progress_cents: int
    progress_points: int
    completion_points: int
    goal_points: int
    bonus_earned_cents: int
    bonus_hit: bool | None


def reward_values(card: Card, purchase: Purchase) -> RewardValues:
    """Return cashback or static travel value, flooring at documented stages."""

    matching_rule = next(
        (rule for rule in card.reward_rules if rule.category == purchase.category),
        None,
    )
    rate_bps = matching_rule.rate_bps if matching_rule else card.base_rate_bps
    reward_type = matching_rule.reward_type if matching_rule else card.base_reward_type
    accrued_units = purchase.amount_cents * rate_bps // 10_000

    if reward_type is RewardType.CASHBACK:
        return RewardValues(cashback_cents=accrued_units, travel_value_cents=0)
    travel_value_cents = accrued_units * card.point_value_millicents // 1_000
    return RewardValues(cashback_cents=0, travel_value_cents=travel_value_cents)


def utilization_after(card: Card, extra_cents: int) -> int:
    _require_nonnegative_int("extra_cents", extra_cents)
    if card.credit_limit_cents == 0:
        return 10_000
    return (card.current_balance_cents + extra_cents) * 10_000 // card.credit_limit_cents


def utilization_penalty_points(
    utilization_bps: int,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> int:
    _require_nonnegative_int("utilization_bps", utilization_bps)
    penalty = 0
    lower_bps = 0
    for band in config.utilization_bands:
        width = max(0, min(utilization_bps, band.upper_bps) - lower_bps)
        penalty += width * band.penalty_points_per_bps
        lower_bps = band.upper_bps
        if utilization_bps <= band.upper_bps:
            return penalty
    return penalty + (
        (utilization_bps - lower_bps) * config.over_limit_penalty_points_per_bps
    )


def incremental_utilization_penalty_points(
    card: Card,
    extra_cents: int,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> int:
    before = utilization_penalty_points(utilization_after(card, 0), config)
    after = utilization_penalty_points(utilization_after(card, extra_cents), config)
    return after - before


def desired_headroom_cents(
    card: Card,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> int:
    percentage_reserve = card.credit_limit_cents * config.desired_headroom_bps // 10_000
    return min(
        card.credit_limit_cents,
        max(config.minimum_headroom_cents, percentage_reserve),
    )


def incremental_risk_penalty_points(
    card: Card,
    extra_cents: int,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> int:
    _require_nonnegative_int("extra_cents", extra_cents)
    desired = desired_headroom_cents(card, config)
    headroom_before = card.credit_limit_cents - card.current_balance_cents
    headroom_after = headroom_before - extra_cents
    penalty_before = max(0, desired - headroom_before)
    penalty_after = max(0, desired - headroom_after)
    return penalty_after - penalty_before


def cashflow_days(card: Card, purchase: Purchase) -> int:
    return interest_free_float_days(purchase.date, card.statement_day, card.due_day)


def cashflow_value_cents(
    card: Card,
    purchase: Purchase,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> int:
    days = cashflow_days(card, purchase)
    numerator = purchase.amount_cents * config.annual_carry_rate_bps * days
    return numerator // (10_000 * 365)


def signup_bonus_factors(
    card: Card,
    assigned_purchases: Iterable[Purchase],
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> SignupBonusFactors:
    bonus = card.signup_bonus
    if bonus is None:
        return SignupBonusFactors(0, 0, 0, 0, 0, 0, None)

    remaining = bonus.remaining_spend_cents
    if remaining == 0:
        return SignupBonusFactors(0, 0, 0, 0, 0, 0, True)

    eligible_spend = sum(
        purchase.amount_cents
        for purchase in assigned_purchases
        if purchase.date <= bonus.deadline_date
    )
    progress = min(remaining, eligible_spend)
    progress_pool = bonus.reward_value_cents * config.signup_progress_pool_bps // 10_000
    progress_points = progress_pool * progress // remaining
    hit = progress == remaining
    completion_points = bonus.reward_value_cents - progress_pool if hit else 0
    return SignupBonusFactors(
        eligible_spend_cents=eligible_spend,
        progress_cents=progress,
        progress_points=progress_points,
        completion_points=completion_points,
        goal_points=progress_points + completion_points,
        bonus_earned_cents=bonus.reward_value_cents if hit else 0,
        bonus_hit=hit,
    )


def factor_breakdown(
    card: Card,
    purchase: Purchase,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> RawFactorBreakdown:
    rewards = reward_values(card, purchase)
    bonus = signup_bonus_factors(card, (purchase,), config)
    return RawFactorBreakdown(
        cashback_cents=rewards.cashback_cents,
        travel_value_cents=rewards.travel_value_cents,
        signup_eligible_spend_cents=bonus.eligible_spend_cents,
        signup_progress_cents=bonus.progress_cents,
        signup_bonus_earned_cents=bonus.bonus_earned_cents,
        signup_goal_points=bonus.goal_points,
        cashflow_days=cashflow_days(card, purchase),
        cashflow_value_cents=cashflow_value_cents(card, purchase, config),
        utilization_before_bps=utilization_after(card, 0),
        utilization_after_bps=utilization_after(card, purchase.amount_cents),
        credit_penalty_points=incremental_utilization_penalty_points(
            card, purchase.amount_cents, config
        ),
        risk_penalty_points=incremental_risk_penalty_points(
            card, purchase.amount_cents, config
        ),
    )
