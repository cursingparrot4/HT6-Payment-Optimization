from __future__ import annotations

from datetime import date
from itertools import product
from random import Random

import pytest

from engine.config import EngineConfig
from engine.feasibility import analyze_assignment
from engine.greedy import allocate_greedy
from engine.ilp import allocate_ilp
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
from engine.objective import PlanEvaluation, evaluate_plan

pytestmark = pytest.mark.oracle


def card(card_id: str, **overrides: object) -> Card:
    values: dict[str, object] = {
        "id": card_id,
        "name": f"{card_id} (synthetic)",
        "credit_limit_cents": 100_000,
        "current_balance_cents": 0,
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


def intent(
    constraints: Constraint | None = None,
    **goal_weights: float,
) -> Intent:
    weights = {goal: 0.0 for goal in Goal}
    for goal_name, weight in goal_weights.items():
        weights[Goal(goal_name)] = weight
    if not any(weights.values()):
        weights[Goal.MAX_CASHBACK] = 1.0
    return Intent(weights=weights, constraints=constraints or Constraint())


def result_assignment(result) -> dict[str, str]:
    return {assignment.purchase_id: assignment.card_id for assignment in result.assignments}


def brute_force(
    cards: list[Card],
    purchases: list[Purchase],
    target_intent: Intent,
    config: EngineConfig | None = None,
) -> tuple[dict[str, str], PlanEvaluation] | None:
    ordered_cards = sorted(cards, key=lambda item: item.id)
    ordered_purchases = sorted(purchases, key=lambda item: item.id)
    best_assignment: dict[str, str] | None = None
    best_evaluation: PlanEvaluation | None = None
    best_key: tuple[str, ...] | None = None
    active_config = config or EngineConfig()

    for choices in product([card.id for card in ordered_cards], repeat=len(ordered_purchases)):
        assignment = {
            purchase_item.id: card_id
            for purchase_item, card_id in zip(ordered_purchases, choices, strict=True)
        }
        if not analyze_assignment(cards, purchases, target_intent, assignment).feasible:
            continue
        evaluation = evaluate_plan(
            cards,
            purchases,
            target_intent,
            assignment,
            active_config,
        )
        key = tuple(assignment[item.id] for item in ordered_purchases)
        if (
            best_evaluation is None
            or evaluation.objective.total_utility
            > best_evaluation.objective.total_utility
            or (
                evaluation.objective.total_utility
                == best_evaluation.objective.total_utility
                and (best_key is None or key < best_key)
            )
        ):
            best_assignment = assignment
            best_evaluation = evaluation
            best_key = key

    if best_assignment is None or best_evaluation is None:
        return None
    return best_assignment, best_evaluation


def assert_matches_oracle(
    cards: list[Card],
    purchases: list[Purchase],
    target_intent: Intent,
    config: EngineConfig | None = None,
) -> None:
    exact = allocate_ilp(
        cards,
        purchases,
        target_intent,
        config or EngineConfig(),
        include_alternatives=False,
    )
    oracle = brute_force(cards, purchases, target_intent, config)

    assert oracle is not None
    expected_assignment, expected_evaluation = oracle
    assert exact.status is OptimizationStatus.OPTIMAL
    assert result_assignment(exact) == expected_assignment
    assert exact.metrics is not None
    assert exact.metrics.total_utility == expected_evaluation.metrics.total_utility
    assert exact.metrics.model_dump() == expected_evaluation.metrics.model_dump()


def test_ilp_matches_oracle_for_convex_utilization_tradeoff() -> None:
    cards = [
        card(
            "reward",
            current_balance_cents=25_000,
            base_rate_bps=500,
        ),
        card(
            "healthy",
            credit_limit_cents=200_000,
            current_balance_cents=0,
            base_rate_bps=100,
        ),
    ]
    purchases = [purchase("p1", 20_000), purchase("p2", 20_000)]
    assert_matches_oracle(
        cards,
        purchases,
        intent(max_cashback=0.2, credit_health=0.8),
    )


def test_ilp_matches_oracle_for_incremental_headroom_risk() -> None:
    cards = [
        card("reward", current_balance_cents=80_000, base_rate_bps=600),
        card("headroom", credit_limit_cents=200_000, base_rate_bps=100),
    ]
    purchases = [purchase("large", 15_000), purchase("small", 5_000)]
    assert_matches_oracle(
        cards,
        purchases,
        intent(max_cashback=0.3, min_risk=0.7),
    )


def test_ilp_matches_oracle_for_bonus_progress_floor_and_completion() -> None:
    cards = [
        card(
            "bonus",
            base_rate_bps=0,
            signup_bonus=SignupBonus(
                spend_required_cents=30_001,
                spend_so_far_cents=0,
                reward_value_cents=10_003,
                deadline_date=date(2026, 8, 31),
            ),
        ),
        card(
            "cash",
            base_rate_bps=100,
            reward_rules=[
                RewardRule(category="other", rate_bps=500, reward_type="cashback")
            ],
        ),
    ]
    purchases = [
        purchase("p1", 10_000),
        purchase("p2", 20_000),
        purchase("p3", 15_000, date=date(2026, 9, 1)),
    ]
    assert_matches_oracle(
        cards,
        purchases,
        intent(max_cashback=0.25, hit_signup_bonus=0.75),
    )


def test_ilp_matches_oracle_with_lock_dated_ceiling_and_forced_bonus() -> None:
    cards = [
        card(
            "bonus",
            current_balance_cents=20_000,
            signup_bonus=SignupBonus(
                spend_required_cents=20_000,
                spend_so_far_cents=10_000,
                reward_value_cents=8_000,
                deadline_date=date(2026, 8, 31),
            ),
        ),
        card("other", credit_limit_cents=150_000, base_rate_bps=300),
    ]
    purchases = [
        purchase("locked", 10_000, locked_card_id="bonus"),
        purchase("early", 10_000, date=date(2026, 8, 15)),
        purchase("late", 50_000, date=date(2026, 9, 1)),
    ]
    constraints = Constraint(
        max_utilization_bps=3_000,
        max_utilization_until=date(2026, 8, 31),
        must_hit_bonus_card_ids=["bonus"],
    )
    assert_matches_oracle(
        cards,
        purchases,
        intent(constraints, max_cashback=0.5, hit_signup_bonus=0.5),
    )


def test_ilp_proves_indivisible_packing_infeasible() -> None:
    cards = [card("a", credit_limit_cents=10_000), card("b", credit_limit_cents=10_000)]
    purchases = [purchase("p1", 6_000), purchase("p2", 6_000), purchase("p3", 6_000)]

    assert brute_force(cards, purchases, intent()) is None
    result = allocate_ilp(cards, purchases, intent())

    assert result.status is OptimizationStatus.INFEASIBLE
    assert result.solver_method.value == "ilp"
    assert result.issues[0].code is IssueCode.NO_FEASIBLE_ASSIGNMENT


def test_exact_objective_is_never_worse_than_greedy_on_seeded_tiny_cases() -> None:
    random = Random(42)
    for case_index in range(4):
        cards = [
            card(
                f"c{card_index}",
                credit_limit_cents=random.choice([20_000, 25_000, 30_000]),
                current_balance_cents=random.choice([0, 2_000, 5_000]),
                base_rate_bps=random.choice([100, 200, 300]),
                statement_day=random.choice([5, 10, 20]),
                due_day=random.choice([3, 15, 25]),
            )
            for card_index in range(3)
        ]
        purchases = [
            purchase(
                f"case-{case_index}-p{purchase_index}",
                random.choice([2_000, 4_000, 6_000]),
                date=date(2026, 8, random.choice([1, 8, 16, 24])),
            )
            for purchase_index in range(4)
        ]
        target_intent = intent(
            max_cashback=random.random(),
            credit_health=random.random(),
            max_cashflow=random.random(),
            min_risk=random.random(),
        )

        exact = allocate_ilp(cards, purchases, target_intent)
        greedy = allocate_greedy(cards, purchases, target_intent)
        oracle = brute_force(cards, purchases, target_intent)

        assert oracle is not None
        expected_assignment, expected_evaluation = oracle
        assert exact.status is OptimizationStatus.OPTIMAL
        assert result_assignment(exact) == expected_assignment
        assert exact.metrics is not None
        assert exact.metrics.total_utility == expected_evaluation.metrics.total_utility
        if greedy.successful:
            assert greedy.metrics is not None
            assert exact.metrics.total_utility >= greedy.metrics.total_utility


def test_objective_bound_overflow_uses_honest_greedy_fallback() -> None:
    cards = [card("a", base_rate_bps=500), card("b", base_rate_bps=100)]
    purchases = [purchase("p1", 10_000)]
    config = EngineConfig(cbc_exact_integer_limit=10)

    result = allocate_ilp(cards, purchases, intent(), config)

    assert result.status is OptimizationStatus.HEURISTIC_FALLBACK
    assert result.issues[-1].code is IssueCode.SOLVER_ERROR
    assert "exact-integer bound" in result.issues[-1].message
