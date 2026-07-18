from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

from engine.greedy import allocate_greedy
from engine.models import (
    AllocationResult,
    Card,
    Goal,
    Intent,
    IssueCode,
    OptimizationIssue,
    OptimizationStatus,
    Purchase,
    SolverMethod,
    WhatIfResult,
)
from engine.pareto import sample_frontier
from engine.what_if import run_what_if
from explain.frontier import explain_frontier, explain_what_if
from explain.models import ExplanationKind, ExplanationTone, ExplanationUnit


def card(card_id: str, rent_rate: int, dining_rate: int, limit: int = 100_000) -> Card:
    return Card.model_validate(
        {
            "id": card_id,
            "name": f"{card_id} Card",
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


def purchase(
    purchase_id: str,
    amount: int,
    category: str,
    recurring: bool = False,
) -> Purchase:
    return Purchase(
        id=purchase_id,
        amount_cents=amount,
        category=category,
        date=date(2026, 8, 1),
        is_recurring=recurring,
    )


def intent(**weights: float) -> Intent:
    values = {goal: 0.0 for goal in Goal}
    values.update({Goal(name): value for name, value in weights.items()})
    return Intent(weights=values)


def test_frontier_explanation_uses_raw_swept_metrics_and_incomplete_disclosure() -> None:
    cards = [
        card("cash", 500, 100, limit=100_000),
        card("healthy", 100, 100, limit=500_000),
    ]
    result = sample_frontier(
        cards,
        [purchase("rent", 40_000, "rent", recurring=True)],
        intent(max_cashback=0.5, credit_health=0.5),
        method=SolverMethod.GREEDY,
    )

    explanation = explain_frontier(result)

    assert explanation.complete_frontier is False
    assert explanation.attempted_solves == result.attempted_solves
    assert explanation.swept_goals == result.swept_goal_ids
    count_line = explanation.disclosure_lines[0]
    assert count_line.raw_value == result.attempted_solves
    assert count_line.unit is ExplanationUnit.COUNT
    assert "not complete" in explanation.disclosure_lines[1].text.lower()
    for point_index, point in enumerate(explanation.points):
        source_point = result.points[point_index]
        for line in point.metric_lines:
            assert line.goal in result.swept_goal_ids
            assert line.raw_value == source_point.frontier_metrics[line.goal]


def test_empty_frontier_explanation_preserves_failures_without_fake_points() -> None:
    result = sample_frontier(
        [card("small", 100, 100, limit=5_000)],
        [purchase("large", 10_000, "rent")],
        intent(max_cashback=0.5, credit_health=0.5),
        method=SolverMethod.GREEDY,
    )

    explanation = explain_frontier(result)

    assert explanation.points == []
    assert "No sampled strategy" in explanation.headline
    assert any(line.tone is ExplanationTone.BLOCKING for line in explanation.warning_lines)


def test_what_if_explanation_shows_signed_deltas_and_all_moved_purchases() -> None:
    cards = [
        card("rent", 500, 100, limit=6_000),
        card("dining", 100, 500, limit=6_000),
    ]
    purchases = [
        purchase("rent-purchase", 6_000, "rent", recurring=True),
        purchase("dining-purchase", 6_000, "dining"),
    ]
    result = run_what_if(
        cards,
        purchases,
        intent(max_cashback=1),
        "rent-purchase",
        "dining",
        method=SolverMethod.GREEDY,
    )

    explanation = explain_what_if(result)

    assert explanation.failure is None
    assert result.deltas is not None
    reward_line = next(
        line for line in explanation.delta_lines if line.label == "what-if-reward-delta"
    )
    assert reward_line.raw_value == result.deltas.projected_reward_value_cents
    assert reward_line.text.startswith("Projected reward-value change: -$")
    assert {"Rent Purchase", "Dining Purchase"} == {
        line.text.split(" moves", 1)[0]
        for line in explanation.changed_assignment_lines
    }
    assert all(line.kind is ExplanationKind.SOLVER for line in explanation.changed_assignment_lines)


def test_infeasible_what_if_omits_delta_lines_and_preserves_issue() -> None:
    cards = [card("large", 500, 100), card("small", 100, 500, limit=5_000)]
    result = run_what_if(
        cards,
        [purchase("rent", 10_000, "rent")],
        intent(max_cashback=1),
        "rent",
        "small",
        method=SolverMethod.GREEDY,
    )

    explanation = explain_what_if(result)

    assert result.deltas is None
    assert explanation.delta_lines == []
    assert explanation.failure is not None
    assert explanation.failure.lines[0].tone is ExplanationTone.BLOCKING
    assert "$0.00" not in explanation.failure.headline


def test_explanation_module_does_not_import_solver_or_scoring_modules() -> None:
    explain_root = Path(__file__).parents[3] / "explain"
    forbidden = {
        "engine.scoring",
        "engine.objective",
        "engine.feasibility",
        "engine.recommend",
        "engine.greedy",
        "engine.ilp",
        "engine.pareto",
        "engine.what_if",
        "engine.optimize",
    }
    imported: set[str] = set()
    for path in explain_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    assert forbidden.isdisjoint(imported)


def test_what_if_base_failure_is_attributed_to_base_scenario() -> None:
    cards = [card("large", 500, 100)]
    scenario_purchases = [purchase("rent", 1_000, "rent")]
    target_intent = intent(max_cashback=1)
    override = allocate_greedy(cards, scenario_purchases, target_intent)
    base = AllocationResult(
        status=OptimizationStatus.UNRESOLVED,
        solver_method=SolverMethod.GREEDY,
        issues=[
            OptimizationIssue(
                code=IssueCode.HEURISTIC_DEAD_END,
                message="Base search stopped.",
                suggestion="Use exact allocation.",
            )
        ],
    )
    result = WhatIfResult(
        purchase_id="rent",
        override_card_id="large",
        base_result=base,
        override_result=override,
    )

    explanation = explain_what_if(result)

    assert explanation.failure is not None
    assert "base scenario" in explanation.failure.headline.lower()
    assert explanation.failure.lines[0].source_path.startswith("base_result.issues")
