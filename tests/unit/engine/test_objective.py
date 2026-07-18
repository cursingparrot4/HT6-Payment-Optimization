from __future__ import annotations

from decimal import Decimal

import pytest

from engine.models import Goal, Intent, RawFactorBreakdown
from engine.objective import (
    apply_goal_weights,
    objective_from_factors,
    quantize_intent_weights,
    quantize_weights,
)


def equal_weights() -> dict[Goal, float]:
    return {goal: 1.0 for goal in Goal}


def test_equal_weights_use_largest_remainder_and_goal_order() -> None:
    ppm = quantize_weights(equal_weights())

    assert list(ppm) == list(Goal)
    assert list(ppm.values()) == [166_667, 166_667, 166_667, 166_667, 166_666, 166_666]
    assert sum(ppm.values()) == 1_000_000


def test_quantization_handles_sparse_and_decimal_weights() -> None:
    sparse = {goal: Decimal("0") for goal in Goal}
    sparse[Goal.CREDIT_HEALTH] = Decimal("7.5")
    assert quantize_weights(sparse)[Goal.CREDIT_HEALTH] == 1_000_000

    repeating = {goal: Decimal("0.1") for goal in Goal}
    repeating[Goal.MAX_CASHBACK] = Decimal("0.4")
    ppm = quantize_weights(repeating)
    assert ppm[Goal.MAX_CASHBACK] == 444_445
    assert sum(ppm.values()) == 1_000_000


def test_intent_quantization_reuses_the_canonical_path() -> None:
    intent = Intent.equal_weights()
    assert quantize_intent_weights(intent) == quantize_weights(intent.weights)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True, "0.5"])
def test_quantization_rejects_invalid_values(invalid: object) -> None:
    weights: dict[Goal, object] = {goal: 1 for goal in Goal}
    weights[Goal.MIN_RISK] = invalid
    with pytest.raises(ValueError):
        quantize_weights(weights)  # type: ignore[arg-type]


def test_quantization_requires_every_goal_and_positive_total() -> None:
    missing = equal_weights()
    missing.pop(Goal.MIN_RISK)
    with pytest.raises(ValueError, match=r"missing=.*min_risk"):
        quantize_weights(missing)

    with pytest.raises(ValueError, match="at least one weight"):
        quantize_weights({goal: 0 for goal in Goal})


def test_apply_goal_weights_keeps_signed_integer_contributions() -> None:
    raw = {goal: 0 for goal in Goal}
    raw[Goal.MAX_CASHBACK] = 2_200
    raw[Goal.CREDIT_HEALTH] = -3_000
    ppm = {goal: 0 for goal in Goal}
    ppm[Goal.MAX_CASHBACK] = 600_000
    ppm[Goal.CREDIT_HEALTH] = 400_000

    objective = apply_goal_weights(raw, ppm)

    assert objective.utility_by_goal[Goal.MAX_CASHBACK] == 1_320_000_000
    assert objective.utility_by_goal[Goal.CREDIT_HEALTH] == -1_200_000_000
    assert objective.total_utility == 120_000_000
    assert all(isinstance(value, int) for value in objective.utility_by_goal.values())


def test_apply_goal_weights_rejects_float_utility_and_wrong_scale() -> None:
    raw: dict[Goal, object] = {goal: 0 for goal in Goal}
    raw[Goal.MAX_CASHBACK] = 2_200.0
    ppm = {goal: 0 for goal in Goal}
    ppm[Goal.MAX_CASHBACK] = 1_000_000
    with pytest.raises(ValueError, match="must be an integer"):
        apply_goal_weights(raw, ppm)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="sum exactly"):
        apply_goal_weights({goal: 0 for goal in Goal}, {goal: 1 for goal in Goal})


def test_objective_from_factors_maps_positive_and_negative_goal_signals() -> None:
    factors = RawFactorBreakdown(
        cashback_cents=100,
        travel_value_cents=200,
        signup_eligible_spend_cents=10_000,
        signup_progress_cents=10_000,
        signup_bonus_earned_cents=0,
        signup_goal_points=300,
        cashflow_days=30,
        cashflow_value_cents=40,
        utilization_before_bps=2_000,
        utilization_after_bps=4_000,
        credit_penalty_points=500,
        risk_penalty_points=600,
    )
    ppm = {goal: 0 for goal in Goal}
    ppm[Goal.MAX_CASHBACK] = 200_000
    ppm[Goal.MAX_TRAVEL] = 100_000
    ppm[Goal.CREDIT_HEALTH] = 200_000
    ppm[Goal.HIT_SIGNUP_BONUS] = 200_000
    ppm[Goal.MAX_CASHFLOW] = 100_000
    ppm[Goal.MIN_RISK] = 200_000

    result = objective_from_factors(factors, ppm)

    assert result.utility_by_goal[Goal.MAX_CASHBACK] == 20_000_000
    assert result.utility_by_goal[Goal.CREDIT_HEALTH] == -100_000_000
    assert result.utility_by_goal[Goal.MIN_RISK] == -120_000_000
