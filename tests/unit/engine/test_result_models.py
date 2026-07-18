from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from engine.models import (
    AllocationMetrics,
    AllocationResult,
    AssignmentAlternative,
    AssignmentChange,
    CandidateDecision,
    CardPlanSummary,
    ConstraintKind,
    ConstraintSlack,
    FrontierPoint,
    FrontierResult,
    Goal,
    IssueCode,
    MetricDelta,
    ObjectiveBreakdown,
    OptimizationIssue,
    OptimizationStatus,
    PurchaseAssignment,
    RawFactorBreakdown,
    RecommendationResult,
    SolverMethod,
    WhatIfResult,
)


def issue(code: IssueCode = IssueCode.NO_FEASIBLE_ASSIGNMENT) -> OptimizationIssue:
    return OptimizationIssue(
        code=code,
        message="The modeled constraints conflict.",
        suggestion="Relax one hard constraint.",
    )


def raw_factors() -> RawFactorBreakdown:
    return RawFactorBreakdown(
        cashback_cents=2_200,
        travel_value_cents=0,
        signup_eligible_spend_cents=220_000,
        signup_progress_cents=220_000,
        signup_bonus_earned_cents=0,
        signup_goal_points=3_000,
        cashflow_days=37,
        cashflow_value_cents=111,
        utilization_before_bps=708,
        utilization_after_bps=2_541,
        credit_penalty_points=0,
        risk_penalty_points=0,
    )


def objective(total: int = 12_000) -> ObjectiveBreakdown:
    contributions = {goal: 0 for goal in Goal}
    contributions[Goal.MAX_CASHBACK] = total
    return ObjectiveBreakdown(utility_by_goal=contributions, total_utility=total)


def candidate(card_id: str, rank: int = 1, total: int = 12_000) -> CandidateDecision:
    return CandidateDecision(
        card_id=card_id,
        feasible=True,
        rank=rank,
        raw_factors=raw_factors(),
        objective=objective(total),
    )


def metrics(total_utility: int = 12_000) -> AllocationMetrics:
    return AllocationMetrics(
        cashback_cents=2_200,
        travel_value_cents=500,
        signup_progress_cents=220_000,
        signup_bonus_earned_cents=0,
        signup_goal_points=3_000,
        signup_bonus_hit_count=0,
        projected_reward_value_cents=2_700,
        max_card_utilization_bps=2_541,
        credit_penalty_points=0,
        risk_penalty_points=0,
        cashflow_days_total=37,
        cashflow_value_cents=111,
        total_utility=total_utility,
    )


def assignment() -> PurchaseAssignment:
    return PurchaseAssignment(
        purchase_id="rent-2026-08",
        card_id="harbor-rent",
        raw_factors=raw_factors(),
        objective=objective(),
    )


def card_summary() -> CardPlanSummary:
    return CardPlanSummary(
        card_id="harbor-rent",
        assigned_purchase_ids=["rent-2026-08"],
        assigned_spend_cents=220_000,
        ending_balance_cents=305_000,
        ending_utilization_bps=2_541,
        credit_limit_slack_cents=895_000,
        utilization_slack_cents=55_000,
        bonus_remaining_cents=None,
        bonus_hit=None,
        cashflow_days_total=37,
    )


def successful_allocation(
    status: OptimizationStatus = OptimizationStatus.HEURISTIC,
    solver_method: SolverMethod = SolverMethod.GREEDY,
) -> AllocationResult:
    return AllocationResult(
        status=status,
        solver_method=solver_method,
        assignments=[assignment()],
        card_summaries=[card_summary()],
        metrics=metrics(),
    )


def assert_integer_contract(value: Any, path: str = "root") -> None:
    if isinstance(value, BaseModel):
        assert_integer_contract(value.model_dump(), path)
    elif isinstance(value, dict):
        for key, child in value.items():
            assert_integer_contract(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_integer_contract(child, f"{path}[{index}]")
    elif isinstance(value, float):
        pytest.fail(f"unexpected float at {path}")


def test_objective_requires_all_goals_and_reconciles_total() -> None:
    valid = objective(-500)
    assert valid.total_utility == -500

    incomplete = {Goal.MAX_CASHBACK: 10}
    with pytest.raises(ValidationError, match="contain every goal"):
        ObjectiveBreakdown(utility_by_goal=incomplete, total_utility=10)

    with pytest.raises(ValidationError, match="must equal the sum"):
        ObjectiveBreakdown(utility_by_goal={goal: 1 for goal in Goal}, total_utility=5)


def test_candidate_and_alternative_states_require_matching_evidence() -> None:
    with pytest.raises(ValidationError, match="require raw_factors"):
        CandidateDecision(card_id="harbor-rent", feasible=True)

    with pytest.raises(ValidationError, match="at least one issue"):
        CandidateDecision(card_id="harbor-rent", feasible=False)

    alternative = AssignmentAlternative(
        card_id="summit-journey",
        feasible=True,
        resulting_plan_utility=10_000,
        total_utility_delta=-2_000,
        metric_deltas=MetricDelta(projected_reward_value_cents=500),
    )
    assert alternative.total_utility_delta == -2_000

    with pytest.raises(ValidationError, match="cannot contain plan metrics"):
        AssignmentAlternative(
            card_id="summit-journey",
            feasible=False,
            resulting_plan_utility=10_000,
            issues=[issue(IssueCode.CREDIT_LIMIT_EXCEEDED)],
        )


def test_constraint_slack_distinguishes_binding_from_near_binding() -> None:
    binding = ConstraintSlack(
        kind=ConstraintKind.CREDIT_LIMIT,
        card_id="harbor-rent",
        slack_cents=0,
        binding=True,
        near_binding=False,
    )
    assert binding.binding

    with pytest.raises(ValidationError, match="binding must be true"):
        ConstraintSlack(
            kind=ConstraintKind.CREDIT_LIMIT,
            card_id="harbor-rent",
            slack_cents=1,
            binding=True,
            near_binding=False,
        )


def test_allocation_metrics_reconcile_projected_rewards() -> None:
    assert metrics().projected_reward_value_cents == 2_700

    with pytest.raises(ValidationError, match="must equal cashback"):
        metrics().model_copy(update={"projected_reward_value_cents": 99}).model_validate(
            metrics().model_dump() | {"projected_reward_value_cents": 99}
        )


def test_recommendation_enforces_winner_and_runner_up_order() -> None:
    winner = candidate("harbor-rent", rank=1, total=12_000)
    runner_up = candidate("summit-journey", rank=2, total=10_000)
    result = RecommendationResult(
        status=OptimizationStatus.OPTIMAL,
        winner=winner,
        runner_up=runner_up,
        candidates=[winner, runner_up],
    )

    assert result.winner == winner

    with pytest.raises(ValidationError, match="runner_up must be"):
        RecommendationResult(
            status=OptimizationStatus.OPTIMAL,
            winner=winner,
            candidates=[winner, runner_up],
        )


def test_infeasible_recommendation_contains_no_fake_candidate() -> None:
    result = RecommendationResult(
        status=OptimizationStatus.INFEASIBLE,
        issues=[issue()],
    )
    assert result.winner is None

    with pytest.raises(ValidationError, match="cannot contain feasible candidates"):
        RecommendationResult(
            status=OptimizationStatus.INFEASIBLE,
            winner=candidate("harbor-rent"),
            issues=[issue()],
        )


def test_allocation_statuses_preserve_honest_solver_semantics() -> None:
    assert successful_allocation().successful
    assert successful_allocation(
        OptimizationStatus.OPTIMAL, SolverMethod.ILP
    ).status is OptimizationStatus.OPTIMAL

    unresolved = AllocationResult(
        status=OptimizationStatus.UNRESOLVED,
        solver_method=SolverMethod.GREEDY,
        issues=[issue(IssueCode.HEURISTIC_DEAD_END)],
    )
    assert not unresolved.successful
    assert unresolved.metrics is None

    with pytest.raises(ValidationError, match="failed allocations cannot contain"):
        AllocationResult(
            status=OptimizationStatus.INFEASIBLE,
            solver_method=SolverMethod.ILP,
            metrics=metrics(),
            issues=[issue()],
        )

    with pytest.raises(ValidationError, match="optimal monthly allocations require"):
        successful_allocation(OptimizationStatus.OPTIMAL, SolverMethod.GREEDY)


def test_frontier_metadata_tracks_active_and_swept_goals() -> None:
    weights = {goal: 0 for goal in Goal}
    weights[Goal.MAX_CASHBACK] = 500_000
    weights[Goal.CREDIT_HEALTH] = 500_000
    point = FrontierPoint(
        label="Balanced cashback and credit health",
        weights_ppm=weights,
        frontier_metrics={Goal.MAX_CASHBACK: 2_200, Goal.CREDIT_HEALTH: 0},
        allocation=successful_allocation(),
    )
    frontier = FrontierResult(
        solver_method=SolverMethod.GREEDY,
        active_goal_ids=[Goal.MAX_CASHBACK, Goal.CREDIT_HEALTH, Goal.MIN_RISK],
        swept_goal_ids=[Goal.MAX_CASHBACK, Goal.CREDIT_HEALTH],
        grid_size=5,
        attempted_solves=5,
        successful_solves=5,
        points=[point],
    )

    assert frontier.complete_frontier is False
    assert frontier.swept_goal_ids == [Goal.MAX_CASHBACK, Goal.CREDIT_HEALTH]

    with pytest.raises(ValidationError, match="subset of active"):
        FrontierResult(
            solver_method=SolverMethod.GREEDY,
            active_goal_ids=[Goal.MAX_CASHBACK],
            swept_goal_ids=[Goal.MAX_TRAVEL],
            grid_size=1,
            attempted_solves=1,
            successful_solves=0,
        )


def test_frontier_weights_are_integer_ppm_with_exact_sum() -> None:
    with pytest.raises(ValidationError, match="sum exactly"):
        FrontierPoint(
            label="Invalid point",
            weights_ppm={goal: 1 for goal in Goal},
            frontier_metrics={Goal.MAX_CASHBACK: 2_200},
            allocation=successful_allocation(),
        )

    invalid_float_weights = {goal: 0 for goal in Goal}
    invalid_float_weights[Goal.MAX_CASHBACK] = 1_000_000.0
    with pytest.raises(ValidationError):
        FrontierPoint(
            label="Invalid point",
            weights_ppm=invalid_float_weights,
            frontier_metrics={Goal.MAX_CASHBACK: 2_200},
            allocation=successful_allocation(),
        )


def test_what_if_deltas_exist_only_for_two_successful_plans() -> None:
    base = successful_allocation()
    override = successful_allocation()
    result = WhatIfResult(
        purchase_id="rent-2026-08",
        override_card_id="summit-journey",
        base_result=base,
        override_result=override,
        deltas=MetricDelta(projected_reward_value_cents=-500),
        changed_assignments=[
            AssignmentChange(
                purchase_id="rent-2026-08",
                base_card_id="harbor-rent",
                override_card_id="summit-journey",
            )
        ],
    )
    assert result.deltas is not None

    failed_override = AllocationResult(
        status=OptimizationStatus.INFEASIBLE,
        solver_method=SolverMethod.ILP,
        issues=[issue()],
    )
    with pytest.raises(ValidationError, match="deltas exist exactly"):
        WhatIfResult(
            purchase_id="rent-2026-08",
            override_card_id="summit-journey",
            base_result=base,
            override_result=failed_override,
            deltas=MetricDelta(),
        )


def test_engine_result_money_paths_contain_no_float() -> None:
    result = WhatIfResult(
        purchase_id="rent-2026-08",
        override_card_id="summit-journey",
        base_result=successful_allocation(),
        override_result=successful_allocation(),
        deltas=MetricDelta(total_utility=-500),
    )

    assert_integer_contract(result)