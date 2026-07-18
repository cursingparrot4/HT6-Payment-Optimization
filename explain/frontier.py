"""Sampled-frontier and what-if explanation builders."""

from __future__ import annotations

from collections.abc import Sequence

from engine.models import (
    FrontierPoint,
    FrontierResult,
    Goal,
    MetricDelta,
    OptimizationIssue,
    OptimizationStatus,
    WhatIfResult,
)
from explain.formatters import (
    format_bps,
    format_cents,
    format_days,
    format_points,
    humanize_identifier,
)
from explain.models import (
    ExplanationKind,
    ExplanationLine,
    ExplanationTone,
    ExplanationUnit,
    FailureExplanation,
    FrontierExplanation,
    FrontierPointExplanation,
    WhatIfExplanation,
)

_MINIMIZED_GOALS = {Goal.CREDIT_HEALTH, Goal.MIN_RISK}
_GOAL_NAMES = {
    Goal.MAX_CASHBACK: "cashback",
    Goal.MAX_TRAVEL: "travel value",
    Goal.CREDIT_HEALTH: "credit-health penalty",
    Goal.HIT_SIGNUP_BONUS: "bonus progress",
    Goal.MAX_CASHFLOW: "cashflow value",
    Goal.MIN_RISK: "headroom-risk penalty",
}


def _line(
    *,
    kind: ExplanationKind,
    tone: ExplanationTone,
    label: str,
    text: str,
    source_path: str,
    raw_value: int | bool | None = None,
    unit: ExplanationUnit | None = None,
    goal: Goal | None = None,
) -> ExplanationLine:
    return ExplanationLine(
        kind=kind,
        tone=tone,
        label=label,
        text=text,
        raw_value=raw_value,
        unit=unit,
        source_path=source_path,
        goal=goal,
    )


def _issue_line(issue: OptimizationIssue, source_path: str) -> ExplanationLine:
    return _line(
        kind=ExplanationKind.CONSTRAINT,
        tone=ExplanationTone.BLOCKING,
        label=issue.code.value,
        text=issue.message,
        source_path=source_path,
    )


def _metric_line(
    point: FrontierPoint,
    point_index: int,
    goal: Goal,
) -> ExplanationLine:
    value = point.frontier_metrics[goal]
    source_path = f"points[{point_index}].frontier_metrics.{goal.value}"
    if goal is Goal.MAX_CASHBACK:
        return _line(
            kind=ExplanationKind.REWARD,
            tone=ExplanationTone.NEUTRAL,
            label="frontier-cashback",
            text=f"Projected cashback: {format_cents(value)}.",
            raw_value=value,
            unit=ExplanationUnit.CENTS,
            source_path=source_path,
            goal=goal,
        )
    if goal is Goal.MAX_TRAVEL:
        return _line(
            kind=ExplanationKind.TRAVEL,
            tone=ExplanationTone.NEUTRAL,
            label="frontier-travel-value",
            text=f"Static travel-point value: {format_cents(value)}.",
            raw_value=value,
            unit=ExplanationUnit.CENTS,
            source_path=source_path,
            goal=goal,
        )
    if goal is Goal.CREDIT_HEALTH:
        return _line(
            kind=ExplanationKind.UTILIZATION,
            tone=ExplanationTone.NEUTRAL,
            label="frontier-credit-penalty",
            text=f"Credit-health penalty: {format_points(value)}; lower is better.",
            raw_value=value,
            unit=ExplanationUnit.POINTS,
            source_path=source_path,
            goal=goal,
        )
    if goal is Goal.HIT_SIGNUP_BONUS:
        return _line(
            kind=ExplanationKind.BONUS,
            tone=ExplanationTone.NEUTRAL,
            label="frontier-bonus-points",
            text=f"Bonus-goal utility: {format_points(value)}.",
            raw_value=value,
            unit=ExplanationUnit.POINTS,
            source_path=source_path,
            goal=goal,
        )
    if goal is Goal.MAX_CASHFLOW:
        return _line(
            kind=ExplanationKind.CASHFLOW,
            tone=ExplanationTone.NEUTRAL,
            label="frontier-cashflow-value",
            text=f"Configured carrying-value estimate: {format_cents(value)}.",
            raw_value=value,
            unit=ExplanationUnit.CENTS,
            source_path=source_path,
            goal=goal,
        )
    return _line(
        kind=ExplanationKind.RISK,
        tone=ExplanationTone.NEUTRAL,
        label="frontier-risk-penalty",
        text=f"Headroom-risk penalty: {format_points(value)}; lower is better.",
        raw_value=value,
        unit=ExplanationUnit.POINTS,
        source_path=source_path,
        goal=goal,
    )


def _reference_point(points: Sequence[FrontierPoint], goals: Sequence[Goal]) -> FrontierPoint:
    return min(
        points,
        key=lambda point: (
            max(point.weights_ppm[goal] for goal in goals),
            point.label,
        ),
    )


def _tradeoff_summary(
    point: FrontierPoint,
    reference: FrontierPoint,
    goals: Sequence[Goal],
) -> str:
    if point is reference:
        return f"{point.label} is the reference sampled strategy."
    changes: list[str] = []
    for goal in goals:
        delta = point.frontier_metrics[goal] - reference.frontier_metrics[goal]
        if delta == 0:
            continue
        direction = "more" if delta > 0 else "less"
        if goal in _MINIMIZED_GOALS:
            direction = "higher" if delta > 0 else "lower"
        changes.append(f"{direction} {_GOAL_NAMES[goal]}")
    if not changes:
        return f"{point.label} matches the reference on the displayed goal metrics."
    return f"Compared with {reference.label}: " + ", ".join(changes) + "."


def explain_frontier(result: FrontierResult) -> FrontierExplanation:
    reference = _reference_point(result.points, result.swept_goal_ids) if result.points else None
    points = [
        FrontierPointExplanation(
            label=point.label,
            summary=_tradeoff_summary(point, reference, result.swept_goal_ids),
            status=point.allocation.status,
            solver_method=point.allocation.solver_method,
            metric_lines=[
                _metric_line(point, point_index, goal)
                for goal in result.swept_goal_ids
            ],
        )
        for point_index, point in enumerate(result.points)
    ]
    swept_names = ", ".join(_GOAL_NAMES[goal] for goal in result.swept_goal_ids)
    disclosure_lines = [
        _line(
            kind=ExplanationKind.SOLVER,
            tone=ExplanationTone.NEUTRAL,
            label="frontier-grid-disclosure",
            text=(
                f"Attempted {result.attempted_solves} of {result.grid_size} sampled "
                f"weight settings across {swept_names}; "
                f"{result.successful_solves} produced plans."
            ),
            raw_value=result.attempted_solves,
            unit=ExplanationUnit.COUNT,
            source_path="attempted_solves",
        ),
        _line(
            kind=ExplanationKind.WARNING,
            tone=ExplanationTone.CAUTION,
            label="frontier-incomplete-disclosure",
            text=(
                "This sampled strategy frontier is not complete; other nondominated "
                "allocations may exist, including tradeoffs involving non-swept goals."
            ),
            raw_value=result.complete_frontier,
            unit=ExplanationUnit.BOOLEAN,
            source_path="complete_frontier",
        ),
    ]
    warning_lines = [
        _line(
            kind=ExplanationKind.WARNING,
            tone=ExplanationTone.CAUTION,
            label=f"frontier-warning-{index + 1}",
            text=warning,
            source_path=f"warnings[{index}]",
        )
        for index, warning in enumerate(result.warnings)
    ]
    warning_lines.extend(
        _issue_line(issue, f"issues[{index}]")
        for index, issue in enumerate(result.issues)
    )
    return FrontierExplanation(
        headline=(
            f"{len(points)} representative sampled strategies"
            if points
            else "No sampled strategy produced a successful plan"
        ),
        active_goals=result.active_goal_ids,
        swept_goals=result.swept_goal_ids,
        attempted_solves=result.attempted_solves,
        successful_solves=result.successful_solves,
        complete_frontier=result.complete_frontier,
        points=points,
        disclosure_lines=disclosure_lines,
        warning_lines=warning_lines,
    )


def _what_if_delta_lines(delta: MetricDelta) -> list[ExplanationLine]:
    definitions = [
        (
            "what-if-reward-delta",
            "projected_reward_value_cents",
            ExplanationKind.REWARD,
            delta.projected_reward_value_cents,
            ExplanationUnit.CENTS,
            "Projected reward-value change: ",
            format_cents,
            Goal.MAX_CASHBACK,
        ),
        (
            "what-if-utilization-delta",
            "max_card_utilization_bps",
            ExplanationKind.UTILIZATION,
            delta.max_card_utilization_bps,
            ExplanationUnit.BPS,
            "Maximum-utilization change: ",
            format_bps,
            Goal.CREDIT_HEALTH,
        ),
        (
            "what-if-bonus-progress-delta",
            "signup_progress_cents",
            ExplanationKind.BONUS,
            delta.signup_progress_cents,
            ExplanationUnit.CENTS,
            "Qualifying bonus-spend progress change: ",
            format_cents,
            Goal.HIT_SIGNUP_BONUS,
        ),
        (
            "what-if-bonus-earned-delta",
            "signup_bonus_earned_cents",
            ExplanationKind.BONUS,
            delta.signup_bonus_earned_cents,
            ExplanationUnit.CENTS,
            "Newly earned signup-bonus value change: ",
            format_cents,
            Goal.HIT_SIGNUP_BONUS,
        ),
        (
            "what-if-float-delta",
            "cashflow_days_total",
            ExplanationKind.CASHFLOW,
            delta.cashflow_days_total,
            ExplanationUnit.DAYS,
            "Interest-free-float change: ",
            format_days,
            Goal.MAX_CASHFLOW,
        ),
        (
            "what-if-utility-delta",
            "total_utility",
            ExplanationKind.SOLVER,
            delta.total_utility,
            ExplanationUnit.POINTS,
            "Modeled utility change: ",
            format_points,
            None,
        ),
    ]
    return [
        _line(
            kind=kind,
            tone=(
                ExplanationTone.NEUTRAL
                if value == 0
                else ExplanationTone.CAUTION
                if (
                    (kind is ExplanationKind.UTILIZATION and value > 0)
                    or (kind is not ExplanationKind.UTILIZATION and value < 0)
                )
                else ExplanationTone.POSITIVE
            ),
            label=label,
            text=prefix + formatter(value, signed=True) + ".",
            raw_value=value,
            unit=unit,
            source_path=f"deltas.{field_name} (override_result - base_result)",
            goal=goal,
        )
        for label, field_name, kind, value, unit, prefix, formatter, goal in definitions
    ]


def _comparison_failure(result: WhatIfResult) -> FailureExplanation:
    if not result.base_result.successful:
        failed_result = result.base_result
        source_prefix = "base_result"
        headline = "The base scenario did not produce a plan for comparison."
    else:
        failed_result = result.override_result
        source_prefix = "override_result"
        headline = (
            "The override heuristic did not find a complete plan."
            if failed_result.status is OptimizationStatus.UNRESOLVED
            else "The requested override is infeasible under the modeled constraints."
        )
    issues = failed_result.issues
    return FailureExplanation(
        headline=headline,
        lines=[
            _issue_line(issue, f"{source_prefix}.issues[{index}]")
            for index, issue in enumerate(issues)
        ],
        suggestions=list(dict.fromkeys(issue.suggestion for issue in issues)),
    )


def explain_what_if(result: WhatIfResult) -> WhatIfExplanation:
    warning_lines = [
        _line(
            kind=ExplanationKind.WARNING,
            tone=ExplanationTone.CAUTION,
            label=f"what-if-warning-{index + 1}",
            text=warning,
            source_path=f"warnings[{index}]",
        )
        for index, warning in enumerate(result.warnings)
    ]
    if result.deltas is None:
        return WhatIfExplanation(
            headline=(
                f"Override {humanize_identifier(result.purchase_id)} to "
                f"{humanize_identifier(result.override_card_id)} cannot be compared."
            ),
            purchase_id=result.purchase_id,
            override_card_id=result.override_card_id,
            base_status=result.base_result.status,
            override_status=result.override_result.status,
            warning_lines=warning_lines,
            failure=_comparison_failure(result),
        )
    changes = [
        _line(
            kind=ExplanationKind.SOLVER,
            tone=ExplanationTone.NEUTRAL,
            label=f"assignment-change-{index + 1}",
            text=(
                f"{humanize_identifier(change.purchase_id)} moves from "
                f"{humanize_identifier(change.base_card_id)} to "
                f"{humanize_identifier(change.override_card_id)}."
            ),
            source_path=f"changed_assignments[{index}]",
        )
        for index, change in enumerate(result.changed_assignments)
    ]
    return WhatIfExplanation(
        headline=(
            f"Lock {humanize_identifier(result.purchase_id)} to "
            f"{humanize_identifier(result.override_card_id)} and reoptimize the rest."
        ),
        purchase_id=result.purchase_id,
        override_card_id=result.override_card_id,
        base_status=result.base_result.status,
        override_status=result.override_result.status,
        delta_lines=_what_if_delta_lines(result.deltas),
        changed_assignment_lines=changes,
        warning_lines=warning_lines,
    )
