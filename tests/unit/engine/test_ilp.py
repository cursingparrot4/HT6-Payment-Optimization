from __future__ import annotations

import time
from datetime import date
from multiprocessing.connection import Connection

import pulp

from engine.config import EngineConfig
from engine.ilp import allocate_ilp
from engine.models import Card, Goal, Intent, OptimizationStatus, Purchase, SolverMethod
from engine.optimize import allocate_month


def sleeping_worker(
    connection: Connection,
    cards,
    purchases,
    intent,
    config,
    include_alternatives,
) -> None:
    time.sleep(10)
    connection.close()


def card(card_id: str, limit: int, rate: int) -> Card:
    return Card(
        id=card_id,
        name=f"{card_id} (synthetic)",
        credit_limit_cents=limit,
        current_balance_cents=0,
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


def cashback_intent() -> Intent:
    weights = {goal: 0 for goal in Goal}
    weights[Goal.MAX_CASHBACK] = 1
    return Intent(weights=weights)


def assignment_map(result) -> dict[str, str]:
    return {assignment.purchase_id: assignment.card_id for assignment in result.assignments}


def test_ilp_solves_rewards_and_capacity_exactly() -> None:
    cards = [card("high", 10_000, 500), card("flat", 20_000, 100)]
    purchases = [purchase("p1", 10_000), purchase("p2", 10_000)]

    result = allocate_ilp(cards, purchases, cashback_intent())

    assert result.status is OptimizationStatus.OPTIMAL
    assert assignment_map(result) == {"p1": "flat", "p2": "high"}
    assert result.metrics is not None
    assert result.metrics.cashback_cents == 600


def test_ilp_tie_break_prefers_lexicographically_lower_card_ids() -> None:
    cards = [card("z-card", 20_000, 100), card("a-card", 20_000, 100)]
    purchases = [purchase("p1", 5_000), purchase("p2", 5_000)]

    result = allocate_ilp(cards, purchases, cashback_intent())

    assert result.status is OptimizationStatus.OPTIMAL
    assert assignment_map(result) == {"p1": "a-card", "p2": "a-card"}


class FailingSolver(pulp.LpSolver):
    def available(self) -> bool:
        return True

    def actualSolve(self, lp: pulp.LpProblem, **kwargs) -> int:
        raise pulp.PulpSolverError("synthetic solver failure")


def test_ilp_solver_error_returns_verified_greedy_fallback() -> None:
    result = allocate_ilp(
        [card("high", 20_000, 500), card("flat", 20_000, 100)],
        [purchase("p1", 10_000)],
        cashback_intent(),
        solver=FailingSolver(),
    )

    assert result.status is OptimizationStatus.HEURISTIC_FALLBACK
    assert result.successful
    assert result.issues[-1].code.value == "solver_error"


def test_exact_state_limit_returns_honest_greedy_fallback() -> None:
    result = allocate_ilp(
        [card("a", 50_000, 300), card("b", 50_000, 100)],
        [purchase("p1", 1_000), purchase("p2", 2_000)],
        Intent(weights={goal: 1 for goal in Goal}),
        EngineConfig(ilp_max_card_states=2),
    )

    assert result.status is OptimizationStatus.HEURISTIC_FALLBACK
    assert result.issues[-1].code.value == "solver_error"
    assert "spend-state limit" in result.issues[-1].message


def test_public_allocate_month_dispatches_to_ilp() -> None:
    result = allocate_month(
        [card("a", 20_000, 300), card("b", 20_000, 100)],
        [purchase("p1", 5_000)],
        cashback_intent(),
        method=SolverMethod.ILP,
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert result.solver_method is SolverMethod.ILP


def test_valid_ids_that_normalize_similarly_do_not_collide_in_pulp() -> None:
    result = allocate_ilp(
        [card("a-b", 20_000, 300), card("a_b", 20_000, 100)],
        [purchase("p-1", 5_000), purchase("p_1", 5_000)],
        cashback_intent(),
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert assignment_map(result) == {"p-1": "a-b", "p_1": "a-b"}


def test_hard_wall_timeout_kills_worker_and_returns_greedy_fallback(monkeypatch) -> None:
    import engine.ilp as ilp_module

    monkeypatch.setattr(ilp_module, "_isolated_ilp_worker", sleeping_worker)
    started = time.monotonic()
    result = ilp_module.allocate_ilp(
        [card("high", 20_000, 500), card("flat", 20_000, 100)],
        [purchase("p1", 10_000)],
        cashback_intent(),
        EngineConfig(ilp_wall_timeout_seconds=1),
    )
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert result.status is OptimizationStatus.HEURISTIC_FALLBACK
    assert result.issues[-1].code.value == "solver_timeout"
    assert "hard wall-clock" in result.issues[-1].message
