from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from engine.models import (
    Card,
    Constraint,
    Goal,
    Intent,
    IssueCode,
    OptimizationStatus,
    Purchase,
    RewardRule,
    RewardType,
    Scenario,
    SignupBonus,
    SolverMethod,
)


def all_weights(**overrides: float) -> dict[str, float]:
    weights = {goal.value: 1.0 for goal in Goal}
    weights.update(overrides)
    return weights


def card_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "summit-journey",
        "name": "Summit Journey (synthetic)",
        "credit_limit_cents": 1_200_000,
        "current_balance_cents": 85_000,
        "reward_rules": [
            {"category": "Dining Out", "rate_bps": 300, "reward_type": "points"}
        ],
        "base_rate_bps": 100,
        "base_reward_type": "points",
        "point_value_millicents": 1_250,
        "annual_fee_cents": 9_500,
        "statement_day": 12,
        "due_day": 7,
        "signup_bonus": None,
    }
    payload.update(overrides)
    return payload


def purchase_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "rent-2026-08",
        "amount_cents": 220_000,
        "category": "Rent",
        "date": date(2026, 8, 1),
        "is_recurring": True,
        "locked_card_id": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("invalid", [1_000.0, 1_000.5, True, "1000"])
def test_money_fields_reject_coercion(invalid: object) -> None:
    with pytest.raises(ValidationError):
        Card.model_validate(card_payload(current_balance_cents=invalid))

    with pytest.raises(ValidationError):
        Purchase.model_validate(purchase_payload(amount_cents=invalid))


def test_categories_are_normalized_and_duplicate_rules_are_rejected() -> None:
    rule = RewardRule(category="  Dining-Out ", rate_bps=300, reward_type="points")
    assert rule.category == "dining_out"

    with pytest.raises(ValidationError, match="categories must be unique"):
        Card.model_validate(
            card_payload(
                reward_rules=[
                    {"category": "Dining Out", "rate_bps": 300, "reward_type": "points"},
                    {"category": "dining-out", "rate_bps": 200, "reward_type": "cashback"},
                ]
            )
        )


def test_card_accepts_zero_limit_and_balance_over_limit_for_later_diagnostics() -> None:
    zero_limit = Card.model_validate(card_payload(credit_limit_cents=0))
    over_limit = Card.model_validate(
        card_payload(credit_limit_cents=100_000, current_balance_cents=125_000)
    )

    assert zero_limit.credit_limit_cents == 0
    assert over_limit.current_balance_cents > over_limit.credit_limit_cents


def test_signup_bonus_remaining_spend_is_clamped_without_mutating_source_values() -> None:
    bonus = SignupBonus(
        spend_required_cents=400_000,
        spend_so_far_cents=450_000,
        reward_value_cents=60_000,
        deadline_date=date(2026, 10, 31),
    )

    assert bonus.remaining_spend_cents == 0
    assert bonus.spend_so_far_cents == 450_000


def test_constraint_requires_a_ceiling_for_cutoff_and_sorts_forced_ids() -> None:
    constraint = Constraint(
        max_utilization_bps=3_000,
        max_utilization_until="2026-10-31",
        must_hit_bonus_card_ids=["summit-journey", "aurora-bonus"],
    )

    assert constraint.max_utilization_until == date(2026, 10, 31)
    assert constraint.must_hit_bonus_card_ids == ["aurora-bonus", "summit-journey"]

    with pytest.raises(ValidationError, match="requires max_utilization_bps"):
        Constraint(max_utilization_until=date(2026, 10, 31))

    with pytest.raises(ValidationError, match="must be unique"):
        Constraint(must_hit_bonus_card_ids=["aurora-bonus", "aurora-bonus"])


def test_intent_requires_every_goal_and_normalizes_positive_weights() -> None:
    intent = Intent(
        weights=all_weights(credit_health=4.0, hit_signup_bonus=2.0),
        constraints=Constraint(),
    )

    assert list(intent.weights) == list(Goal)
    assert sum(intent.weights.values()) == pytest.approx(1.0)
    assert intent.weights[Goal.CREDIT_HEALTH] == pytest.approx(4 / 10)

    missing = all_weights()
    missing.pop(Goal.MIN_RISK.value)
    with pytest.raises(ValidationError, match=r"missing=.*min_risk"):
        Intent(weights=missing)

    with pytest.raises(ValidationError, match="unknown goal"):
        Intent(weights={**all_weights(), "unknown": 1.0})


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -0.1, True, "0.5"])
def test_intent_rejects_invalid_weight_values(invalid: object) -> None:
    with pytest.raises(ValidationError):
        Intent(weights=all_weights(max_cashback=invalid))


def test_intent_rejects_an_all_zero_vector() -> None:
    with pytest.raises(ValidationError, match="at least one intent weight"):
        Intent(weights={goal: 0 for goal in Goal})


def test_equal_weight_intent_uses_the_normal_validation_path() -> None:
    intent = Intent.equal_weights()

    assert list(intent.weights) == list(Goal)
    assert all(weight == pytest.approx(1 / 6) for weight in intent.weights.values())
    assert intent.constraints == Constraint()


def test_iso_dates_validate_from_json_and_serialize_as_iso_dates() -> None:
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "id": "sarah-august-2026",
            "name": "Sarah August (synthetic)",
            "synthetic": True,
            "reference_date": "2026-07-18",
            "cards": [card_payload()],
            "purchases": [
                {
                    **purchase_payload(),
                    "date": "2026-08-01",
                }
            ],
            "intent": {"weights": all_weights(), "constraints": {}},
        }
    )

    scenario = Scenario.model_validate_json(raw)
    serialized = scenario.model_dump(mode="json")

    assert scenario.reference_date == date(2026, 7, 18)
    assert serialized["reference_date"] == "2026-07-18"
    assert serialized["cards"][0]["base_reward_type"] == "points"


def test_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Purchase.model_validate({**purchase_payload(), "currency": "CAD"})


def test_serialized_enum_values_are_stable() -> None:
    assert [goal.value for goal in Goal] == [
        "max_cashback",
        "max_travel",
        "credit_health",
        "hit_signup_bonus",
        "max_cashflow",
        "min_risk",
    ]
    assert [reward.value for reward in RewardType] == ["cashback", "points", "miles"]
    assert [status.value for status in OptimizationStatus] == [
        "optimal",
        "heuristic",
        "heuristic_fallback",
        "infeasible",
        "unresolved",
    ]
    assert [method.value for method in SolverMethod] == ["single_purchase", "greedy", "ilp"]
    assert IssueCode.HEURISTIC_DEAD_END.value == "heuristic_dead_end"
