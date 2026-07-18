from __future__ import annotations

from datetime import date

from engine.config import EngineConfig
from engine.models import Card, Goal, Intent, Purchase, SolverMethod
from engine.pareto import sample_frontier


def card(card_id: str, limit: int, balance: int, rate: int) -> Card:
    return Card(
        id=card_id,
        name=f"{card_id} (synthetic)",
        credit_limit_cents=limit,
        current_balance_cents=balance,
        reward_rules=[],
        base_rate_bps=rate,
        base_reward_type="cashback",
        point_value_millicents=1_000,
        annual_fee_cents=0,
        statement_day=10,
        due_day=5,
    )


def purchase(purchase_id: str, amount: int) -> Purchase:
    return Purchase(
        id=purchase_id,
        amount_cents=amount,
        category="other",
        date=date(2026, 8, 1),
        is_recurring=False,
    )


def intent(**weights: float) -> Intent:
    values = {goal: 0.0 for goal in Goal}
    values.update({Goal(name): value for name, value in weights.items()})
    return Intent(weights=values)


def assignment_key(point) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (assignment.purchase_id, assignment.card_id)
            for assignment in point.allocation.assignments
        )
    )


def test_two_goal_frontier_keeps_nondominated_reward_health_tradeoffs() -> None:
    cards = [
        card("cash", 100_000, 20_000, 500),
        card("healthy", 500_000, 0, 100),
    ]
    result = sample_frontier(
        cards,
        [purchase("rent", 40_000)],
        intent(max_cashback=0.5, credit_health=0.5),
        method=SolverMethod.ILP,
    )

    assert result.grid_size == 5
    assert result.attempted_solves == 5
    assert result.successful_solves == 5
    assert result.active_goal_ids == [Goal.MAX_CASHBACK, Goal.CREDIT_HEALTH]
    assert result.swept_goal_ids == [Goal.MAX_CASHBACK, Goal.CREDIT_HEALTH]
    assert len(result.points) == 2
    assert len({assignment_key(point) for point in result.points}) == 2
    assert {point.label for point in result.points} == {"Max cashback", "Best credit health"}
    assert all(point.allocation.assignments[0].alternatives for point in result.points)
    assert result.complete_frontier is False


def test_one_active_goal_returns_one_plan_and_disclosure() -> None:
    result = sample_frontier(
        [card("a", 100_000, 0, 200), card("b", 100_000, 0, 100)],
        [purchase("p1", 10_000)],
        intent(max_cashback=1.0),
        method=SolverMethod.GREEDY,
    )

    assert result.grid_size == 1
    assert result.swept_goal_ids == [Goal.MAX_CASHBACK]
    assert len(result.points) == 1
    assert any("Only one goal" in warning for warning in result.warnings)
    assert any("heuristic" in warning for warning in result.warnings)


def test_three_goal_grid_honors_solve_cap_and_representative_limit() -> None:
    result = sample_frontier(
        [card("a", 100_000, 10_000, 300), card("b", 200_000, 0, 100)],
        [purchase("p1", 10_000), purchase("p2", 20_000)],
        intent(max_cashback=1, credit_health=1, min_risk=1),
        method=SolverMethod.GREEDY,
        max_points=3,
        config=EngineConfig(frontier_max_solves=4),
    )

    assert result.grid_size == 15
    assert result.attempted_solves == 4
    assert result.truncation_reason == "solve_cap"
    assert len(result.points) <= 3


def test_duplicate_allocations_collapse_across_weight_settings() -> None:
    result = sample_frontier(
        [card("a", 100_000, 0, 300), card("b", 100_000, 0, 100)],
        [purchase("p1", 10_000)],
        intent(max_cashback=0.5, max_cashflow=0.5),
        method=SolverMethod.GREEDY,
    )

    assert len(result.points) == 1


def test_all_failed_sweeps_return_structured_empty_frontier() -> None:
    result = sample_frontier(
        [card("small", 5_000, 0, 100)],
        [purchase("too-large", 10_000)],
        intent(max_cashback=0.5, credit_health=0.5),
        method=SolverMethod.ILP,
    )

    assert result.attempted_solves == 5
    assert result.successful_solves == 0
    assert result.points == []
    assert result.issues
    assert any("infeasible" in warning for warning in result.warnings)
