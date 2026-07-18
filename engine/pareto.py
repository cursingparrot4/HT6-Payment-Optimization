"""Bounded weight sweeps and dominance filtering for sampled strategies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from math import ceil
from time import perf_counter

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.greedy import allocate_greedy, attach_alternatives
from engine.ilp import allocate_ilp
from engine.models import (
    AllocationResult,
    Card,
    FrontierPoint,
    FrontierResult,
    Goal,
    Intent,
    OptimizationIssue,
    Purchase,
    SolverMethod,
)
from engine.objective import evaluate_plan, quantize_intent_weights

_MINIMIZED_GOALS = {Goal.CREDIT_HEALTH, Goal.MIN_RISK}
_GOAL_LABELS = {
    Goal.MAX_CASHBACK: "Cashback",
    Goal.MAX_TRAVEL: "Travel value",
    Goal.CREDIT_HEALTH: "Credit health",
    Goal.HIT_SIGNUP_BONUS: "Bonus progress",
    Goal.MAX_CASHFLOW: "Cashflow",
    Goal.MIN_RISK: "Headroom",
}
_EXTREME_LABELS = {
    Goal.MAX_CASHBACK: "Max cashback",
    Goal.MAX_TRAVEL: "Max travel value",
    Goal.CREDIT_HEALTH: "Best credit health",
    Goal.HIT_SIGNUP_BONUS: "Best bonus progress",
    Goal.MAX_CASHFLOW: "Best cashflow",
    Goal.MIN_RISK: "Most headroom",
}


@dataclass(frozen=True, slots=True)
class _SampledPlan:
    intent: Intent
    weights_ppm: dict[Goal, int]
    allocation: AllocationResult
    metrics: dict[Goal, int]
    assignment_key: tuple[tuple[str, str], ...]


def _solve(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    method: SolverMethod,
    config: EngineConfig,
    *,
    include_alternatives: bool,
) -> AllocationResult:
    if method is SolverMethod.ILP:
        return allocate_ilp(
            cards,
            purchases,
            intent,
            config,
            include_alternatives=include_alternatives,
        )
    if method is SolverMethod.GREEDY:
        return allocate_greedy(cards, purchases, intent, config)
    raise ValueError(f"unsupported frontier solver method: {method.value}")


def _frontier_metrics(allocation: AllocationResult) -> dict[Goal, int]:
    metrics = allocation.metrics
    if metrics is None:
        raise ValueError("successful frontier allocation is missing metrics")
    return {
        Goal.MAX_CASHBACK: metrics.cashback_cents,
        Goal.MAX_TRAVEL: metrics.travel_value_cents,
        Goal.CREDIT_HEALTH: metrics.credit_penalty_points,
        Goal.HIT_SIGNUP_BONUS: metrics.signup_goal_points,
        Goal.MAX_CASHFLOW: metrics.cashflow_value_cents,
        Goal.MIN_RISK: metrics.risk_penalty_points,
    }


def _assignment_key(allocation: AllocationResult) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (assignment.purchase_id, assignment.card_id)
            for assignment in allocation.assignments
        )
    )


def _dominates(first: _SampledPlan, second: _SampledPlan, goals: Sequence[Goal]) -> bool:
    weakly_better = True
    strictly_better = False
    for goal in goals:
        first_value = first.metrics[goal]
        second_value = second.metrics[goal]
        if goal in _MINIMIZED_GOALS:
            weakly_better &= first_value <= second_value
            strictly_better |= first_value < second_value
        else:
            weakly_better &= first_value >= second_value
            strictly_better |= first_value > second_value
    return weakly_better and strictly_better


def _weight_grid(
    active_goals: Sequence[Goal],
    swept_goals: Sequence[Goal],
    intent: Intent,
    config: EngineConfig,
) -> list[Intent]:
    if len(swept_goals) == 1:
        return [intent]

    raw_vectors: list[dict[Goal, int]] = []
    if len(swept_goals) == 2:
        steps = config.frontier_two_goal_steps
        first, second = swept_goals
        for second_units in range(steps):
            vector = {goal: 0 for goal in Goal}
            vector[first] = steps - second_units - 1
            vector[second] = second_units
            raw_vectors.append(vector)
    else:
        denominator = config.frontier_three_goal_denominator
        first, second, third = swept_goals
        for first_units in range(denominator, -1, -1):
            for second_units in range(denominator - first_units, -1, -1):
                vector = {goal: 0 for goal in Goal}
                vector[first] = first_units
                vector[second] = second_units
                vector[third] = denominator - first_units - second_units
                raw_vectors.append(vector)

    return [
        Intent(weights=vector, constraints=intent.constraints)
        for vector in raw_vectors
        if any(vector[goal] > 0 for goal in active_goals)
    ]


def _extreme_plan(plans: Sequence[_SampledPlan], goal: Goal) -> _SampledPlan:
    if goal in _MINIMIZED_GOALS:
        return min(plans, key=lambda plan: (plan.metrics[goal], plan.assignment_key))
    return min(plans, key=lambda plan: (-plan.metrics[goal], plan.assignment_key))


def _distance(
    first: _SampledPlan,
    second: _SampledPlan,
    goals: Sequence[Goal],
    ranges: Mapping[Goal, int],
) -> Fraction:
    return sum(
        (
            Fraction(abs(first.metrics[goal] - second.metrics[goal]), ranges[goal])
            if ranges[goal]
            else Fraction(0)
        )
        for goal in goals
    )


def _representatives(
    plans: Sequence[_SampledPlan],
    goals: Sequence[Goal],
    maximum: int,
) -> list[_SampledPlan]:
    if not plans:
        return []
    selected: list[_SampledPlan] = []
    for goal in goals:
        extreme = _extreme_plan(plans, goal)
        if extreme.assignment_key not in {plan.assignment_key for plan in selected}:
            selected.append(extreme)
        if len(selected) == maximum:
            return selected

    ranges = {
        goal: max(plan.metrics[goal] for plan in plans)
        - min(plan.metrics[goal] for plan in plans)
        for goal in goals
    }
    remaining = sorted(
        (
            plan
            for plan in plans
            if plan.assignment_key not in {item.assignment_key for item in selected}
        ),
        key=lambda plan: plan.assignment_key,
    )
    while remaining and len(selected) < maximum:
        if not selected:
            selected.append(remaining.pop(0))
            continue
        scored = [
            (
                min(_distance(candidate, chosen, goals, ranges) for chosen in selected),
                candidate,
            )
            for candidate in remaining
        ]
        best_distance = max(score for score, _ in scored)
        candidate = next(item for score, item in scored if score == best_distance)
        selected.append(candidate)
        remaining.remove(candidate)
    return selected


def _label(weights_ppm: Mapping[Goal, int], goals: Sequence[Goal]) -> str:
    pure = [goal for goal in goals if weights_ppm[goal] == 1_000_000]
    if pure:
        return _EXTREME_LABELS[pure[0]]
    positive = sorted(
        (goal for goal in goals if weights_ppm[goal] > 0),
        key=lambda goal: (-weights_ppm[goal], list(Goal).index(goal)),
    )
    return "Balanced: " + " + ".join(_GOAL_LABELS[goal] for goal in positive)


def _with_alternatives(
    plan: _SampledPlan,
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    config: EngineConfig,
) -> AllocationResult:
    if all(assignment.alternatives for assignment in plan.allocation.assignments):
        return plan.allocation
    assignments = {
        assignment.purchase_id: assignment.card_id
        for assignment in plan.allocation.assignments
    }
    evaluation = evaluate_plan(
        list(cards), list(purchases), plan.intent, assignments, config
    )
    return plan.allocation.model_copy(
        update={
            "assignments": attach_alternatives(
                cards, purchases, plan.intent, assignments, evaluation, config
            )
        }
    )


def sample_frontier(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    method: SolverMethod = SolverMethod.ILP,
    max_points: int = 5,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> FrontierResult:
    """Return representative nondominated plans from a bounded weight sweep."""

    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 1 <= max_points <= 5:
        raise ValueError("max_points must be an integer from 1 through 5")
    original_weights = quantize_intent_weights(intent, config)
    active_goals = [goal for goal in Goal if original_weights[goal] > 0]
    swept_goals = sorted(
        active_goals,
        key=lambda goal: (-original_weights[goal], list(Goal).index(goal)),
    )[:3]
    grid = _weight_grid(active_goals, swept_goals, intent, config)
    grid_size = len(grid)
    sampled: list[_SampledPlan] = []
    issues: list[OptimizationIssue] = []
    warnings: list[str] = []
    attempted = 0
    truncation_reason: str | None = None
    started = perf_counter()

    for sweep_intent in grid:
        if attempted >= config.frontier_max_solves:
            truncation_reason = "solve_cap"
            break
        if perf_counter() - started >= config.frontier_timeout_seconds:
            truncation_reason = "time_budget"
            break
        attempted += 1
        solve_config = config
        if method is SolverMethod.ILP:
            remaining_seconds = max(
                1,
                ceil(config.frontier_timeout_seconds - (perf_counter() - started)),
            )
            solve_config = replace(
                config,
                ilp_wall_timeout_seconds=min(
                    config.ilp_wall_timeout_seconds,
                    remaining_seconds,
                ),
            )
        allocation = _solve(
            cards,
            purchases,
            sweep_intent,
            method,
            solve_config,
            include_alternatives=False,
        )
        if not allocation.successful:
            issues.extend(allocation.issues)
            warnings.append(
                f"Weight setting {attempted} returned {allocation.status.value}."
            )
            continue
        sampled.append(
            _SampledPlan(
                intent=sweep_intent,
                weights_ppm=quantize_intent_weights(sweep_intent, config),
                allocation=allocation,
                metrics=_frontier_metrics(allocation),
                assignment_key=_assignment_key(allocation),
            )
        )

    unique: dict[tuple[tuple[str, str], ...], _SampledPlan] = {}
    for plan in sampled:
        existing = unique.get(plan.assignment_key)
        if existing is None or max(plan.weights_ppm.values()) > max(
            existing.weights_ppm.values()
        ):
            unique[plan.assignment_key] = plan
    candidates = list(unique.values())
    nondominated = [
        candidate
        for candidate in candidates
        if not any(
            other.assignment_key != candidate.assignment_key
            and _dominates(other, candidate, swept_goals)
            for other in candidates
        )
    ]
    nondominated.sort(key=lambda plan: plan.assignment_key)
    representatives = _representatives(nondominated, swept_goals, max_points)

    labels_seen: dict[str, int] = {}
    points: list[FrontierPoint] = []
    for plan in representatives:
        label = _label(plan.weights_ppm, swept_goals)
        labels_seen[label] = labels_seen.get(label, 0) + 1
        if labels_seen[label] > 1:
            label = f"{label} ({labels_seen[label]})"
        points.append(
            FrontierPoint(
                label=label,
                weights_ppm=plan.weights_ppm,
                frontier_metrics={goal: plan.metrics[goal] for goal in swept_goals},
                allocation=_with_alternatives(plan, cards, purchases, config),
            )
        )

    if len(swept_goals) == 1:
        warnings.append("Only one goal is active; no tradeoff dimension was invented.")
    if method is SolverMethod.GREEDY:
        warnings.append("Strategies are sampled from heuristic allocations.")
    warnings.append(
        "This is a sampled strategy frontier; unsupported or unsampled "
        "nondominated allocations may exist."
    )
    return FrontierResult(
        solver_method=method,
        active_goal_ids=active_goals,
        swept_goal_ids=swept_goals,
        grid_size=grid_size,
        attempted_solves=attempted,
        successful_solves=len(sampled),
        points=points,
        truncation_reason=truncation_reason,
        issues=issues,
        warnings=warnings,
    )
