from __future__ import annotations

from datetime import date

from engine.models import Card, Goal, Intent, IssueCode, OptimizationStatus, Purchase
from engine.what_if import run_what_if


def card(card_id: str, rent_rate: int, dining_rate: int, limit: int = 6_000) -> Card:
    return Card.model_validate(
        {
            "id": card_id,
            "name": f"{card_id} (synthetic)",
            "credit_limit_cents": limit,
            "current_balance_cents": 0,
            "reward_rules": [
                {"category": "rent", "rate_bps": rent_rate, "reward_type": "cashback"},
                {
                    "category": "dining",
                    "rate_bps": dining_rate,
                    "reward_type": "cashback",
                },
            ],
            "base_rate_bps": 100,
            "base_reward_type": "cashback",
            "point_value_millicents": 1_000,
            "annual_fee_cents": 0,
            "statement_day": 10,
            "due_day": 5,
        }
    )


def purchases() -> list[Purchase]:
    return [
        Purchase(
            id="rent",
            amount_cents=6_000,
            category="rent",
            date=date(2026, 8, 1),
            is_recurring=True,
        ),
        Purchase(
            id="dining",
            amount_cents=6_000,
            category="dining",
            date=date(2026, 8, 2),
            is_recurring=False,
        ),
    ]


def cashback_intent() -> Intent:
    weights = {goal: 0 for goal in Goal}
    weights[Goal.MAX_CASHBACK] = 1
    return Intent(weights=weights)


def test_what_if_locks_purchase_and_reoptimizes_other_assignments() -> None:
    cards = [card("rent-card", 500, 100), card("dining-card", 100, 500)]
    original = purchases()

    result = run_what_if(
        cards,
        original,
        cashback_intent(),
        purchase_id="rent",
        override_card_id="dining-card",
    )

    assert result.base_result.status is OptimizationStatus.OPTIMAL
    assert result.override_result.status is OptimizationStatus.OPTIMAL
    assert result.deltas is not None
    assert result.deltas.cashback_cents == -480
    assert {change.purchase_id for change in result.changed_assignments} == {"rent", "dining"}
    assert all(purchase.locked_card_id is None for purchase in original)


def test_infeasible_override_keeps_base_and_omits_fake_deltas() -> None:
    cards = [card("rent-card", 500, 100), card("too-small", 100, 500, limit=5_000)]
    result = run_what_if(
        cards,
        [purchases()[0]],
        cashback_intent(),
        purchase_id="rent",
        override_card_id="too-small",
    )

    assert result.base_result.successful
    assert result.override_result.status is OptimizationStatus.INFEASIBLE
    assert result.deltas is None
    assert result.changed_assignments == []


def test_unknown_purchase_and_card_are_structured() -> None:
    cards = [card("rent-card", 500, 100)]
    scenario_purchases = [purchases()[0]]
    unknown_purchase = run_what_if(
        cards,
        scenario_purchases,
        cashback_intent(),
        purchase_id="missing",
        override_card_id="rent-card",
    )
    assert unknown_purchase.override_result.issues[0].code is IssueCode.UNKNOWN_PURCHASE

    unknown_card = run_what_if(
        cards,
        scenario_purchases,
        cashback_intent(),
        purchase_id="rent",
        override_card_id="missing-card",
    )
    assert unknown_card.override_result.issues[0].code is IssueCode.UNKNOWN_ASSIGNED_CARD


def test_what_if_replaces_an_existing_purchase_lock_without_mutating_input() -> None:
    cards = [card("rent-card", 500, 100), card("dining-card", 100, 500)]
    locked_rent = purchases()[0].model_copy(update={"locked_card_id": "rent-card"})

    result = run_what_if(
        cards,
        [locked_rent],
        cashback_intent(),
        purchase_id="rent",
        override_card_id="dining-card",
    )

    assert result.override_result.successful
    assert result.override_result.assignments[0].card_id == "dining-card"
    assert locked_rent.locked_card_id == "rent-card"
