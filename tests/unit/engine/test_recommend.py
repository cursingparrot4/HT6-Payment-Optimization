from __future__ import annotations

from datetime import date

from engine.models import Card, Constraint, Goal, Intent, IssueCode, Purchase, RewardRule
from engine.recommend import recommend_purchase


def card(card_id: str, base_rate_bps: int, **overrides: object) -> Card:
    values: dict[str, object] = {
        "id": card_id,
        "name": f"{card_id} (synthetic)",
        "credit_limit_cents": 500_000,
        "current_balance_cents": 0,
        "reward_rules": [],
        "base_rate_bps": base_rate_bps,
        "base_reward_type": "cashback",
        "point_value_millicents": 1_000,
        "annual_fee_cents": 0,
        "statement_day": 10,
        "due_day": 5,
        "signup_bonus": None,
    }
    values.update(overrides)
    return Card.model_validate(values)


def purchase(**overrides: object) -> Purchase:
    values: dict[str, object] = {
        "id": "groceries-1",
        "amount_cents": 10_000,
        "category": "groceries",
        "date": date(2026, 8, 1),
        "is_recurring": False,
        "locked_card_id": None,
    }
    values.update(overrides)
    return Purchase.model_validate(values)


def intent(**weights: float) -> Intent:
    values = {goal: 0.0 for goal in Goal}
    values[Goal.MAX_CASHBACK] = 1.0
    values.update({Goal(key): value for key, value in weights.items()})
    return Intent(weights=values)


def test_recommender_returns_exact_winner_runner_up_and_breakdowns() -> None:
    cards = [
        card("flat", 200),
        card(
            "grocery",
            100,
            reward_rules=[
                RewardRule(category="groceries", rate_bps=400, reward_type="cashback")
            ],
        ),
        card("low", 100),
    ]

    result = recommend_purchase(cards, purchase(), intent())

    assert result.status.value == "optimal"
    assert result.winner is not None
    assert result.runner_up is not None
    assert result.winner.card_id == "grocery"
    assert result.winner.raw_factors.cashback_cents == 400
    assert result.runner_up.card_id == "flat"
    assert [candidate.rank for candidate in result.candidates] == [1, 2, 3]


def test_equal_scores_tie_break_by_card_id() -> None:
    result = recommend_purchase(
        [card("z-card", 100), card("a-card", 100)],
        purchase(),
        intent(),
    )
    assert result.winner is not None
    assert result.winner.card_id == "a-card"


def test_lock_evaluates_only_locked_card_and_explains_exclusions() -> None:
    result = recommend_purchase(
        [card("best", 500), card("locked", 100)],
        purchase(locked_card_id="locked"),
        intent(),
    )

    assert result.winner is not None
    assert result.winner.card_id == "locked"
    assert [candidate.card_id for candidate in result.excluded_cards] == ["best"]
    assert result.excluded_cards[0].issues[0].code is IssueCode.PURCHASE_LOCKED_TO_OTHER_CARD


def test_capacity_and_utilization_exclusions_do_not_crash() -> None:
    constrained_intent = Intent(
        weights={goal: 1 for goal in Goal},
        constraints=Constraint(max_utilization_bps=3_000),
    )
    result = recommend_purchase(
        [
            card("zero", 500, credit_limit_cents=0),
            card("tight", 500, credit_limit_cents=100_000, current_balance_cents=29_000),
            card("usable", 100, credit_limit_cents=100_000, current_balance_cents=0),
        ],
        purchase(amount_cents=10_000),
        constrained_intent,
    )

    assert result.winner is not None
    assert result.winner.card_id == "usable"
    assert {candidate.card_id for candidate in result.excluded_cards} == {"tight", "zero"}


def test_no_feasible_card_returns_structured_result() -> None:
    result = recommend_purchase(
        [card("small", 100, credit_limit_cents=5_000)],
        purchase(amount_cents=10_000),
        intent(),
    )

    assert result.status.value == "infeasible"
    assert result.winner is None
    assert IssueCode.NO_FEASIBLE_ASSIGNMENT in [problem.code for problem in result.issues]
    assert result.excluded_cards[0].card_id == "small"
