from __future__ import annotations

from datetime import date

import pytest

from engine.config import EngineConfig
from engine.models import Card, Purchase, RewardRule, RewardType, SignupBonus
from engine.scoring import (
    cashflow_days,
    cashflow_value_cents,
    desired_headroom_cents,
    factor_breakdown,
    incremental_risk_penalty_points,
    incremental_utilization_penalty_points,
    reward_values,
    signup_bonus_factors,
    utilization_after,
    utilization_penalty_points,
)


def make_card(**overrides: object) -> Card:
    values: dict[str, object] = {
        "id": "test-card",
        "name": "Test Card (synthetic)",
        "credit_limit_cents": 1_000_000,
        "current_balance_cents": 100_000,
        "reward_rules": [],
        "base_rate_bps": 100,
        "base_reward_type": RewardType.CASHBACK,
        "point_value_millicents": 1_250,
        "annual_fee_cents": 0,
        "statement_day": 12,
        "due_day": 7,
        "signup_bonus": None,
    }
    values.update(overrides)
    return Card.model_validate(values)


def make_purchase(**overrides: object) -> Purchase:
    values: dict[str, object] = {
        "id": "purchase-1",
        "amount_cents": 12_345,
        "category": "groceries",
        "date": date(2026, 8, 13),
        "is_recurring": False,
        "locked_card_id": None,
    }
    values.update(overrides)
    return Purchase.model_validate(values)


def test_cashback_uses_category_rule_and_floors_fractional_cents() -> None:
    card = make_card(
        reward_rules=[
            RewardRule(category="groceries", rate_bps=333, reward_type="cashback")
        ]
    )
    reward = reward_values(card, make_purchase(amount_cents=10_001))

    assert reward.cashback_cents == 333
    assert reward.travel_value_cents == 0


def test_points_floor_before_static_millicent_conversion() -> None:
    card = make_card(
        base_rate_bps=300,
        base_reward_type=RewardType.POINTS,
        point_value_millicents=1_250,
    )
    reward = reward_values(card, make_purchase(amount_cents=12_345))

    assert 12_345 * 300 // 10_000 == 370
    assert reward.cashback_cents == 0
    assert reward.travel_value_cents == 462


def test_other_rule_is_literal_and_does_not_replace_base_fallback() -> None:
    card = make_card(
        reward_rules=[RewardRule(category="other", rate_bps=500, reward_type="cashback")],
        base_rate_bps=100,
    )

    assert reward_values(card, make_purchase(category="groceries")).cashback_cents == 123
    assert reward_values(card, make_purchase(category="other")).cashback_cents == 617


def test_utilization_handles_flooring_and_zero_limit() -> None:
    assert utilization_after(make_card(), 123_456) == 2_234
    assert utilization_after(make_card(credit_limit_cents=0), 1) == 10_000


@pytest.mark.parametrize(
    "utilization_bps, expected",
    [
        (3_000, 0),
        (4_500, 3_000),
        (5_000, 4_000),
        (6_000, 10_000),
        (7_500, 19_000),
        (10_000, 69_000),
        (11_000, 119_000),
    ],
)
def test_utilization_penalty_integrates_convex_band_slopes(
    utilization_bps: int, expected: int
) -> None:
    assert utilization_penalty_points(utilization_bps) == expected


def test_incremental_utilization_penalty_excludes_existing_balance_penalty() -> None:
    card = make_card(current_balance_cents=400_000)
    assert incremental_utilization_penalty_points(card, 100_000) == 2_000


def test_risk_penalty_is_incremental_headroom_shortfall() -> None:
    card = make_card(
        credit_limit_cents=1_000_000,
        current_balance_cents=850_000,
    )
    assert desired_headroom_cents(card) == 100_000
    assert incremental_risk_penalty_points(card, 25_000) == 0
    assert incremental_risk_penalty_points(card, 75_000) == 25_000


def test_cashflow_reports_days_and_integer_carrying_value() -> None:
    card = make_card(statement_day=12, due_day=7)
    purchase = make_purchase(amount_cents=50_000, date=date(2026, 8, 13))

    assert cashflow_days(card, purchase) == 55
    assert cashflow_value_cents(card, purchase) == (50_000 * 500 * 55) // (10_000 * 365)


def test_signup_progress_is_capped_and_completion_is_earned_only_on_threshold() -> None:
    card = make_card(
        signup_bonus=SignupBonus(
            spend_required_cents=400_000,
            spend_so_far_cents=100_000,
            reward_value_cents=60_000,
            deadline_date=date(2026, 8, 31),
        )
    )
    partial = signup_bonus_factors(card, [make_purchase(amount_cents=150_000)])
    completed = signup_bonus_factors(card, [make_purchase(amount_cents=350_000)])

    assert partial.progress_cents == 150_000
    assert partial.progress_points == 6_000
    assert partial.completion_points == 0
    assert partial.bonus_earned_cents == 0
    assert partial.bonus_hit is False

    assert completed.eligible_spend_cents == 350_000
    assert completed.progress_cents == 300_000
    assert completed.progress_points == 12_000
    assert completed.completion_points == 48_000
    assert completed.goal_points == 60_000
    assert completed.bonus_earned_cents == 60_000
    assert completed.bonus_hit is True


def test_signup_deadline_is_inclusive_and_later_spend_is_ineligible() -> None:
    card = make_card(
        signup_bonus=SignupBonus(
            spend_required_cents=100_000,
            spend_so_far_cents=0,
            reward_value_cents=20_000,
            deadline_date=date(2026, 8, 31),
        )
    )
    factors = signup_bonus_factors(
        card,
        [
            make_purchase(id="on", amount_cents=60_000, date=date(2026, 8, 31)),
            make_purchase(id="after", amount_cents=60_000, date=date(2026, 9, 1)),
        ],
    )

    assert factors.eligible_spend_cents == 60_000
    assert factors.progress_cents == 60_000
    assert factors.bonus_hit is False


def test_absent_and_already_completed_bonus_have_no_new_utility() -> None:
    absent = signup_bonus_factors(make_card(), [make_purchase()])
    completed = signup_bonus_factors(
        make_card(
            signup_bonus=SignupBonus(
                spend_required_cents=100_000,
                spend_so_far_cents=100_000,
                reward_value_cents=20_000,
                deadline_date=date(2026, 8, 31),
            )
        ),
        [make_purchase()],
    )

    assert absent.bonus_hit is None
    assert absent.goal_points == 0
    assert completed.bonus_hit is True
    assert completed.goal_points == 0
    assert completed.bonus_earned_cents == 0


def test_factor_breakdown_is_complete_and_integer_only() -> None:
    card = make_card(
        reward_rules=[
            RewardRule(category="groceries", rate_bps=300, reward_type="points")
        ],
        signup_bonus=SignupBonus(
            spend_required_cents=100_000,
            spend_so_far_cents=0,
            reward_value_cents=20_000,
            deadline_date=date(2026, 8, 31),
        ),
    )
    factors = factor_breakdown(card, make_purchase(amount_cents=100_000))

    assert factors.travel_value_cents == 3_750
    assert factors.signup_bonus_earned_cents == 20_000
    assert factors.signup_goal_points == 20_000
    assert all(not isinstance(value, float) for value in factors.model_dump().values())


def test_cashflow_rate_is_configurable_without_float_math() -> None:
    config = EngineConfig(annual_carry_rate_bps=0)
    assert cashflow_value_cents(make_card(), make_purchase(), config) == 0
