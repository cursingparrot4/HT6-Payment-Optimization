from __future__ import annotations

from datetime import date

from engine.models import Card, Goal, Intent, Purchase, RewardRule, SignupBonus
from engine.objective import evaluate_plan


def card(card_id: str, **overrides: object) -> Card:
    values: dict[str, object] = {
        "id": card_id,
        "name": f"{card_id} (synthetic)",
        "credit_limit_cents": 100_000,
        "current_balance_cents": 10_000,
        "reward_rules": [],
        "base_rate_bps": 100,
        "base_reward_type": "cashback",
        "point_value_millicents": 1_000,
        "annual_fee_cents": 0,
        "statement_day": 10,
        "due_day": 5,
        "signup_bonus": None,
    }
    values.update(overrides)
    return Card.model_validate(values)


def purchase(purchase_id: str, amount: int, category: str = "other") -> Purchase:
    return Purchase(
        id=purchase_id,
        amount_cents=amount,
        category=category,
        date=date(2026, 8, 1),
        is_recurring=False,
    )


def intent() -> Intent:
    return Intent(weights={goal: 1 for goal in Goal})


def test_plan_evaluation_uses_aggregate_card_state_and_reconciles_metrics() -> None:
    bonus_card = card(
        "bonus",
        reward_rules=[RewardRule(category="groceries", rate_bps=300, reward_type="cashback")],
        signup_bonus=SignupBonus(
            spend_required_cents=50_000,
            spend_so_far_cents=10_000,
            reward_value_cents=20_000,
            deadline_date=date(2026, 8, 31),
        ),
    )
    other = card("other")
    purchases = [purchase("g1", 20_000, "groceries"), purchase("g2", 20_000, "groceries")]

    evaluation = evaluate_plan(
        [bonus_card, other],
        purchases,
        intent(),
        {"g1": "bonus", "g2": "bonus"},
    )

    assert evaluation.metrics.cashback_cents == 1_200
    assert evaluation.metrics.signup_progress_cents == 40_000
    assert evaluation.metrics.signup_bonus_earned_cents == 20_000
    assert evaluation.metrics.projected_reward_value_cents == 21_200
    assert evaluation.metrics.max_card_utilization_bps == 5_000
    assert evaluation.metrics.total_utility == evaluation.objective.total_utility
    bonus_summary = next(
        summary for summary in evaluation.card_summaries if summary.card_id == "bonus"
    )
    assert bonus_summary.assigned_spend_cents == 40_000
    assert bonus_summary.bonus_remaining_cents == 0
    assert bonus_summary.bonus_hit is True


def test_partial_plan_evaluation_does_not_require_all_purchases_assigned() -> None:
    cards = [card("a"), card("b")]
    purchases = [purchase("p1", 10_000), purchase("p2", 10_000)]
    partial = evaluate_plan(cards, purchases, intent(), {"p1": "a"})

    assert len(partial.assignments) == 1
    assert partial.metrics.cashback_cents == 100
