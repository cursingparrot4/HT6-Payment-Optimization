from __future__ import annotations

from datetime import date

from engine.config import EngineConfig
from engine.feasibility import analyze_assignment
from engine.greedy import allocate_greedy
from engine.models import (
    Card,
    Constraint,
    Goal,
    Intent,
    IssueCode,
    OptimizationStatus,
    Purchase,
    RewardRule,
    SignupBonus,
)


def card(card_id: str, limit: int, rate: int, **overrides: object) -> Card:
    values: dict[str, object] = {
        "id": card_id,
        "name": f"{card_id} (synthetic)",
        "credit_limit_cents": limit,
        "current_balance_cents": 0,
        "reward_rules": [],
        "base_rate_bps": rate,
        "base_reward_type": "cashback",
        "point_value_millicents": 1_000,
        "annual_fee_cents": 0,
        "statement_day": 10,
        "due_day": 5,
        "signup_bonus": None,
    }
    values.update(overrides)
    return Card.model_validate(values)


def purchase(purchase_id: str, amount: int, **overrides: object) -> Purchase:
    values: dict[str, object] = {
        "id": purchase_id,
        "amount_cents": amount,
        "category": "other",
        "date": date(2026, 8, 1),
        "is_recurring": False,
        "locked_card_id": None,
    }
    values.update(overrides)
    return Purchase.model_validate(values)


def cashback_intent(constraints: Constraint | None = None) -> Intent:
    weights = {goal: 0 for goal in Goal}
    weights[Goal.MAX_CASHBACK] = 1
    return Intent(weights=weights, constraints=constraints or Constraint())


def assignment_map(result) -> dict[str, str]:
    return {assignment.purchase_id: assignment.card_id for assignment in result.assignments}


def test_greedy_respects_cumulative_capacity_and_returns_final_state_alternatives() -> None:
    cards = [card("high", 15_000, 500), card("flat", 20_000, 100)]
    purchases = [purchase("large", 10_000), purchase("small", 10_000)]

    result = allocate_greedy(cards, purchases, cashback_intent())

    assert result.status is OptimizationStatus.HEURISTIC
    assert analyze_assignment(cards, purchases, cashback_intent(), assignment_map(result)).feasible
    assert set(assignment_map(result).values()) == {"flat", "high"}
    assert all(assignment.alternatives for assignment in result.assignments)
    assert result.metrics is not None
    assert result.metrics.cashback_cents == 600


def test_bounded_repair_recovers_a_two_purchase_repacking_dead_end() -> None:
    cards = [card("a-high", 8_000, 500), card("b-low", 7_000, 100)]
    purchases = [
        purchase("p7", 7_000),
        purchase("p5", 5_000),
        purchase("p3", 3_000),
    ]

    result = allocate_greedy(cards, purchases, cashback_intent())

    assert result.status is OptimizationStatus.HEURISTIC
    assert assignment_map(result) == {"p3": "a-high", "p5": "a-high", "p7": "b-low"}


def test_greedy_dead_end_is_unresolved_when_repair_is_disabled() -> None:
    cards = [card("a-high", 8_000, 500), card("b-low", 7_000, 100)]
    purchases = [purchase("p7", 7_000), purchase("p5", 5_000), purchase("p3", 3_000)]
    config = EngineConfig(greedy_repair_depth=0)

    result = allocate_greedy(cards, purchases, cashback_intent(), config)

    assert result.status is OptimizationStatus.UNRESOLVED
    assert result.assignments == []
    assert result.issues[0].code is IssueCode.HEURISTIC_DEAD_END


def test_analytical_capacity_failure_is_infeasible_not_unresolved() -> None:
    result = allocate_greedy(
        [card("small", 5_000, 100)],
        [purchase("too-large", 10_000)],
        cashback_intent(),
    )

    assert result.status is OptimizationStatus.INFEASIBLE
    assert IssueCode.PURCHASE_EXCEEDS_CAPACITY in [problem.code for problem in result.issues]


def test_forced_bonus_reservation_overrides_soft_cashback_preference() -> None:
    bonus_card = card(
        "bonus",
        20_000,
        100,
        signup_bonus=SignupBonus(
            spend_required_cents=10_000,
            spend_so_far_cents=0,
            reward_value_cents=20_000,
            deadline_date=date(2026, 8, 31),
        ),
    )
    rich_cashback = card(
        "cash",
        20_000,
        100,
        reward_rules=[RewardRule(category="other", rate_bps=1_000, reward_type="cashback")],
    )
    required = cashback_intent(Constraint(must_hit_bonus_card_ids=["bonus"]))

    result = allocate_greedy(
        [bonus_card, rich_cashback],
        [purchase("only", 10_000)],
        required,
    )

    assert result.status is OptimizationStatus.HEURISTIC
    assert assignment_map(result) == {"only": "bonus"}
    assert result.metrics is not None
    assert result.metrics.signup_bonus_earned_cents == 20_000


def test_locked_purchase_is_placed_before_unlocked_spend() -> None:
    cards = [card("limited", 10_000, 500), card("other", 20_000, 100)]
    purchases = [
        purchase("unlocked", 10_000),
        purchase("locked", 10_000, locked_card_id="limited"),
    ]

    result = allocate_greedy(cards, purchases, cashback_intent())

    assert result.status is OptimizationStatus.HEURISTIC
    assert assignment_map(result) == {"locked": "limited", "unlocked": "other"}


def test_repeat_runs_are_deeply_deterministic() -> None:
    cards = [card("a", 20_000, 200), card("b", 20_000, 200)]
    purchases = [purchase("p1", 5_000), purchase("p2", 5_000)]
    first = allocate_greedy(cards, purchases, cashback_intent())
    second = allocate_greedy(cards, purchases, cashback_intent())

    assert first.model_dump() == second.model_dump()
