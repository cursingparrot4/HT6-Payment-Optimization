"""Faithful recommendation and allocation explanation builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from engine.models import (
    AllocationResult,
    AssignmentAlternative,
    CandidateDecision,
    Card,
    CardPlanSummary,
    ConstraintKind,
    Goal,
    Intent,
    MetricDelta,
    OptimizationIssue,
    OptimizationStatus,
    Purchase,
    PurchaseAssignment,
    RecommendationResult,
    SolverMethod,
)
from explain.formatters import (
    format_bps,
    format_cents,
    format_days,
    format_points,
    humanize_identifier,
)
from explain.models import (
    AllocationExplanation,
    AlternativeExplanation,
    CardSummaryExplanation,
    DecisionCard,
    ExplanationContractError,
    ExplanationKind,
    ExplanationLine,
    ExplanationTone,
    ExplanationUnit,
    FailureExplanation,
    RecommendationExplanation,
)


def _unique_lookup(items: Sequence, *, kind: str) -> dict[str, object]:
    lookup: dict[str, object] = {}
    for item in items:
        item_id = item.id
        if item_id in lookup:
            raise ExplanationContractError(kind, f"duplicate ID {item_id!r}")
        lookup[item_id] = item
    return lookup


def _require(lookup: Mapping[str, object], item_id: str, source_path: str):
    item = lookup.get(item_id)
    if item is None:
        raise ExplanationContractError(source_path, f"unknown ID {item_id!r}")
    return item


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


def _solver_line(
    status: OptimizationStatus,
    method: SolverMethod,
    source_path: str = "status",
) -> ExplanationLine:
    if status is OptimizationStatus.OPTIMAL:
        text = "Optimal under the modeled inputs; the exact solver proved this result."
        tone = ExplanationTone.POSITIVE
    elif status is OptimizationStatus.HEURISTIC:
        text = "Feasible heuristic plan; optimality is not claimed."
        tone = ExplanationTone.NEUTRAL
    elif status is OptimizationStatus.HEURISTIC_FALLBACK:
        text = "Verified heuristic fallback; the exact solver did not complete reliably."
        tone = ExplanationTone.CAUTION
    elif status is OptimizationStatus.UNRESOLVED:
        text = "The heuristic did not find a complete plan; infeasibility is not proven."
        tone = ExplanationTone.CAUTION
    else:
        text = "No assignment satisfies the modeled hard constraints."
        tone = ExplanationTone.BLOCKING
    return _line(
        kind=ExplanationKind.SOLVER,
        tone=tone,
        label="solver-status",
        text=f"{text} Method: {method.value}.",
        source_path=source_path,
    )


def _issue_line(issue: OptimizationIssue, source_path: str) -> ExplanationLine:
    return _line(
        kind=ExplanationKind.CONSTRAINT,
        tone=ExplanationTone.BLOCKING,
        label=issue.code.value,
        text=issue.message,
        source_path=source_path,
    )


def _failure(
    status: OptimizationStatus,
    method: SolverMethod,
    issues: Sequence[OptimizationIssue],
    source_path: str,
) -> FailureExplanation:
    if status is OptimizationStatus.UNRESOLVED:
        headline = "The heuristic did not find a complete plan."
    else:
        headline = "No plan satisfies all modeled hard constraints."
    lines = [_solver_line(status, method, f"{source_path}.status")]
    lines.extend(
        _issue_line(issue, f"{source_path}.issues[{index}]")
        for index, issue in enumerate(issues)
    )
    return FailureExplanation(
        headline=headline,
        lines=lines,
        suggestions=list(dict.fromkeys(issue.suggestion for issue in issues)),
    )


def _candidate_factor_lines(
    candidate: CandidateDecision,
    intent: Intent,
    purchase: Purchase,
    source_path: str,
) -> list[ExplanationLine]:
    factors = candidate.raw_factors
    objective = candidate.objective
    if factors is None or objective is None:
        raise ExplanationContractError(source_path, "feasible candidate lacks factors")
    dominant_goals = {
        goal
        for goal, value in sorted(
            objective.utility_by_goal.items(),
            key=lambda item: (-abs(item[1]), list(Goal).index(item[0])),
        )[:3]
        if value != 0
    }
    lines: list[ExplanationLine] = []
    ceiling_active = (
        intent.constraints.max_utilization_bps is not None
        and (
            intent.constraints.max_utilization_until is None
            or purchase.date <= intent.constraints.max_utilization_until
        )
    )
    if ceiling_active:
        ceiling = intent.constraints.max_utilization_bps
        lines.append(
            _line(
                kind=ExplanationKind.CONSTRAINT,
                tone=ExplanationTone.POSITIVE,
                label="utilization-ceiling",
                text=(
                    f"Ending utilization is {format_bps(factors.utilization_after_bps)}, "
                    f"within the {format_bps(ceiling)} hard ceiling."
                ),
                raw_value=factors.utilization_after_bps,
                unit=ExplanationUnit.BPS,
                source_path=f"{source_path}.raw_factors.utilization_after_bps",
                goal=Goal.CREDIT_HEALTH,
            )
        )
    if factors.cashback_cents:
        lines.append(
            _line(
                kind=ExplanationKind.REWARD,
                tone=(
                    ExplanationTone.POSITIVE
                    if Goal.MAX_CASHBACK in dominant_goals
                    else ExplanationTone.NEUTRAL
                ),
                label="projected-cashback",
                text=f"Projected cashback: {format_cents(factors.cashback_cents)}.",
                raw_value=factors.cashback_cents,
                unit=ExplanationUnit.CENTS,
                source_path=f"{source_path}.raw_factors.cashback_cents",
                goal=Goal.MAX_CASHBACK,
            )
        )
    if factors.travel_value_cents:
        lines.append(
            _line(
                kind=ExplanationKind.TRAVEL,
                tone=(
                    ExplanationTone.POSITIVE
                    if Goal.MAX_TRAVEL in dominant_goals
                    else ExplanationTone.NEUTRAL
                ),
                label="static-travel-value",
                text=(
                    "Static travel-point value: "
                    f"{format_cents(factors.travel_value_cents)}."
                ),
                raw_value=factors.travel_value_cents,
                unit=ExplanationUnit.CENTS,
                source_path=f"{source_path}.raw_factors.travel_value_cents",
                goal=Goal.MAX_TRAVEL,
            )
        )
    if factors.signup_progress_cents:
        earned = factors.signup_bonus_earned_cents > 0
        lines.append(
            _line(
                kind=ExplanationKind.BONUS,
                tone=ExplanationTone.POSITIVE if earned else ExplanationTone.NEUTRAL,
                label="signup-progress",
                text=(
                    f"Qualifying signup spend progress: "
                    f"{format_cents(factors.signup_progress_cents)}"
                    + ("; the threshold is reached." if earned else ".")
                ),
                raw_value=factors.signup_progress_cents,
                unit=ExplanationUnit.CENTS,
                source_path=f"{source_path}.raw_factors.signup_progress_cents",
                goal=Goal.HIT_SIGNUP_BONUS,
            )
        )
    if factors.signup_bonus_earned_cents:
        lines.append(
            _line(
                kind=ExplanationKind.BONUS,
                tone=ExplanationTone.POSITIVE,
                label="signup-bonus-earned",
                text=(
                    "Projected newly earned signup-bonus value: "
                    f"{format_cents(factors.signup_bonus_earned_cents)}."
                ),
                raw_value=factors.signup_bonus_earned_cents,
                unit=ExplanationUnit.CENTS,
                source_path=f"{source_path}.raw_factors.signup_bonus_earned_cents",
                goal=Goal.HIT_SIGNUP_BONUS,
            )
        )
    lines.append(
        _line(
            kind=ExplanationKind.CASHFLOW,
            tone=(
                ExplanationTone.POSITIVE
                if Goal.MAX_CASHFLOW in dominant_goals
                else ExplanationTone.NEUTRAL
            ),
            label="interest-free-float",
            text=f"Interest-free float: {format_days(factors.cashflow_days)}.",
            raw_value=factors.cashflow_days,
            unit=ExplanationUnit.DAYS,
            source_path=f"{source_path}.raw_factors.cashflow_days",
            goal=Goal.MAX_CASHFLOW,
        )
    )
    if factors.cashflow_value_cents:
        lines.append(
            _line(
                kind=ExplanationKind.CASHFLOW,
                tone=ExplanationTone.NEUTRAL,
                label="carrying-value-estimate",
                text=(
                    "Configured carrying-value estimate: "
                    f"{format_cents(factors.cashflow_value_cents)}."
                ),
                raw_value=factors.cashflow_value_cents,
                unit=ExplanationUnit.CENTS,
                source_path=f"{source_path}.raw_factors.cashflow_value_cents",
                goal=Goal.MAX_CASHFLOW,
            )
        )
    lines.append(
        _line(
            kind=ExplanationKind.UTILIZATION,
            tone=(
                ExplanationTone.CAUTION
                if factors.credit_penalty_points
                else ExplanationTone.NEUTRAL
            ),
            label="utilization-after",
            text=(
                f"Utilization moves from {format_bps(factors.utilization_before_bps)} "
                f"to {format_bps(factors.utilization_after_bps)}."
            ),
            raw_value=factors.utilization_after_bps,
            unit=ExplanationUnit.BPS,
            source_path=f"{source_path}.raw_factors.utilization_after_bps",
            goal=Goal.CREDIT_HEALTH,
        )
    )
    if factors.risk_penalty_points:
        lines.append(
            _line(
                kind=ExplanationKind.RISK,
                tone=ExplanationTone.CAUTION,
                label="headroom-risk",
                text=(
                    "This choice adds "
                    f"{format_points(factors.risk_penalty_points)} of headroom penalty."
                ),
                raw_value=factors.risk_penalty_points,
                unit=ExplanationUnit.POINTS,
                source_path=f"{source_path}.raw_factors.risk_penalty_points",
                goal=Goal.MIN_RISK,
            )
        )
    return lines


def _candidate_alternative(
    alternative: CandidateDecision,
    card: Card,
    source_path: str,
    winner: CandidateDecision | None = None,
) -> AlternativeExplanation:
    if not alternative.feasible:
        lines = [
            _issue_line(issue, f"{source_path}.issues[{index}]")
            for index, issue in enumerate(alternative.issues)
        ]
        return AlternativeExplanation(
            card_id=card.id,
            card_name=card.name,
            feasible=False,
            summary=f"{card.name} is excluded by a hard constraint.",
            lines=lines,
        )
    if alternative.raw_factors is None or alternative.objective is None or winner is None:
        raise ExplanationContractError(source_path, "feasible comparison lacks factors")
    if winner.raw_factors is None or winner.objective is None:
        raise ExplanationContractError("winner", "winner lacks factors")
    utility_delta = alternative.objective.total_utility - winner.objective.total_utility
    if utility_delta > 0:
        raise ExplanationContractError(
            source_path,
            "runner-up has greater utility than the winner",
        )
    reward_delta = (
        alternative.raw_factors.cashback_cents
        + alternative.raw_factors.travel_value_cents
        - winner.raw_factors.cashback_cents
        - winner.raw_factors.travel_value_cents
    )
    utilization_delta = (
        alternative.raw_factors.utilization_after_bps
        - winner.raw_factors.utilization_after_bps
    )
    lines = [
        _line(
            kind=ExplanationKind.REWARD,
            tone=(
                ExplanationTone.POSITIVE
                if reward_delta > 0
                else ExplanationTone.CAUTION
                if reward_delta < 0
                else ExplanationTone.NEUTRAL
            ),
            label="alternative-reward-delta",
            text=f"Reward-value difference: {format_cents(reward_delta, signed=True)}.",
            raw_value=reward_delta,
            unit=ExplanationUnit.CENTS,
            source_path=(
                f"({source_path}.raw_factors.cashback_cents + "
                f"{source_path}.raw_factors.travel_value_cents) - "
                "(winner.raw_factors.cashback_cents + "
                "winner.raw_factors.travel_value_cents)"
            ),
        ),
        _line(
            kind=ExplanationKind.UTILIZATION,
            tone=(
                ExplanationTone.CAUTION
                if utilization_delta > 0
                else ExplanationTone.POSITIVE
                if utilization_delta < 0
                else ExplanationTone.NEUTRAL
            ),
            label="alternative-utilization-delta",
            text=(
                "Ending-utilization difference: "
                f"{format_bps(utilization_delta, signed=True)}."
            ),
            raw_value=utilization_delta,
            unit=ExplanationUnit.BPS,
            source_path=(
                f"{source_path}.raw_factors.utilization_after_bps - "
                "winner.raw_factors.utilization_after_bps"
            ),
        ),
    ]
    # Phrase the comparison in raw measurements (dollars, percent), never the internal
    # utility-point scale — that number runs into the millions (weights are quantized to
    # sum to 1,000,000) and reads as a glitch to a user. The signed integer delta is still
    # carried on ``utility_delta_points`` for machine consumers.
    if utility_delta == 0:
        summary = (
            f"{card.name} ties on modeled utility; the deterministic card-ID "
            "tie-break selected the winner."
        )
    else:
        offers: list[str] = []
        if reward_delta != 0:
            direction = "more" if reward_delta > 0 else "less"
            offers.append(f"{format_cents(abs(reward_delta))} {direction} in reward value")
        if utilization_delta != 0:
            offers.append(
                f"ending utilization of "
                f"{format_bps(alternative.raw_factors.utilization_after_bps)} "
                f"({format_bps(utilization_delta, signed=True)} vs the winner)"
            )
        if offers:
            summary = (
                f"{card.name} offers " + " and ".join(offers) + ", but ranks lower once "
                "every weighted goal is combined."
            )
        else:
            summary = f"{card.name} ranks lower once every weighted goal is combined."
    return AlternativeExplanation(
        card_id=card.id,
        card_name=card.name,
        feasible=True,
        summary=summary,
        utility_delta_points=utility_delta,
        lines=lines,
    )


def explain_recommendation(
    result: RecommendationResult,
    cards: Sequence[Card],
    purchase: Purchase,
    intent: Intent,
) -> RecommendationExplanation:
    cards_by_id = _unique_lookup(cards, kind="cards")
    if result.status is not OptimizationStatus.OPTIMAL:
        failure = _failure(
            result.status,
            result.solver_method,
            result.issues,
            "recommendation",
        )
        excluded = [
            _candidate_alternative(
                candidate,
                _require(cards_by_id, candidate.card_id, f"excluded_cards[{index}].card_id"),
                f"excluded_cards[{index}]",
            )
            for index, candidate in enumerate(result.excluded_cards)
        ]
        return RecommendationExplanation(
            status=result.status,
            headline=f"No feasible card recommendation for {humanize_identifier(purchase.id)}.",
            excluded_alternatives=excluded,
            failure=failure,
        )

    winner = result.winner
    if winner is None:
        raise ExplanationContractError("winner", "optimal result has no winner")
    winner_card = _require(cards_by_id, winner.card_id, "winner.card_id")
    alternative = None
    if result.runner_up is not None:
        runner_card = _require(cards_by_id, result.runner_up.card_id, "runner_up.card_id")
        alternative = _candidate_alternative(
            result.runner_up,
            runner_card,
            "runner_up",
            winner,
        )
    elif result.excluded_cards:
        excluded = min(result.excluded_cards, key=lambda candidate: candidate.card_id)
        excluded_card = _require(cards_by_id, excluded.card_id, "excluded_cards[0].card_id")
        alternative = _candidate_alternative(
            excluded,
            excluded_card,
            "excluded_cards[0]",
        )
    warning_lines = [
        _line(
            kind=ExplanationKind.WARNING,
            tone=ExplanationTone.CAUTION,
            label=f"recommendation-warning-{index + 1}",
            text=warning,
            source_path=f"warnings[{index}]",
        )
        for index, warning in enumerate(result.warnings)
    ]
    if winner.raw_factors is not None and winner.raw_factors.travel_value_cents:
        warning_lines.append(
            _line(
                kind=ExplanationKind.WARNING,
                tone=ExplanationTone.NEUTRAL,
                label="static-point-value-assumption",
                text="Travel-point value uses a documented static product assumption.",
                source_path="winner.raw_factors.travel_value_cents",
            )
        )
    card = DecisionCard(
        card_id=winner_card.id,
        card_name=winner_card.name,
        purchase_id=purchase.id,
        purchase_label=humanize_identifier(purchase.id),
        headline=f"Use {winner_card.name} for {humanize_identifier(purchase.id)}.",
        status=result.status,
        solver_method=result.solver_method,
        factor_lines=_candidate_factor_lines(winner, intent, purchase, "winner"),
        constraint_lines=[_solver_line(result.status, result.solver_method)],
        alternative=alternative,
        warning_lines=warning_lines,
    )
    excluded_alternatives = [
        _candidate_alternative(
            candidate,
            _require(cards_by_id, candidate.card_id, f"excluded_cards[{index}].card_id"),
            f"excluded_cards[{index}]",
        )
        for index, candidate in enumerate(result.excluded_cards)
        if alternative is None or candidate.card_id != alternative.card_id
    ]
    return RecommendationExplanation(
        status=result.status,
        headline=card.headline,
        decision_card=card,
        excluded_alternatives=excluded_alternatives,
    )


def _delta_lines(delta: MetricDelta, source_path: str) -> list[ExplanationLine]:
    values = [
        (
            "alternative-reward-delta",
            "projected_reward_value_cents",
            ExplanationKind.REWARD,
            delta.projected_reward_value_cents,
            ExplanationUnit.CENTS,
            "Projected reward-value difference: ",
            format_cents,
            Goal.MAX_CASHBACK,
        ),
        (
            "alternative-utilization-delta",
            "max_card_utilization_bps",
            ExplanationKind.UTILIZATION,
            delta.max_card_utilization_bps,
            ExplanationUnit.BPS,
            "Maximum-utilization difference: ",
            format_bps,
            Goal.CREDIT_HEALTH,
        ),
        (
            "alternative-bonus-progress-delta",
            "signup_progress_cents",
            ExplanationKind.BONUS,
            delta.signup_progress_cents,
            ExplanationUnit.CENTS,
            "Qualifying bonus-spend progress difference: ",
            format_cents,
            Goal.HIT_SIGNUP_BONUS,
        ),
        (
            "alternative-float-delta",
            "cashflow_days_total",
            ExplanationKind.CASHFLOW,
            delta.cashflow_days_total,
            ExplanationUnit.DAYS,
            "Interest-free-float difference: ",
            format_days,
            Goal.MAX_CASHFLOW,
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
            source_path=(
                f"{source_path}.{field_name} (alternative_plan - selected_plan)"
            ),
            goal=goal,
        )
        for label, field_name, kind, value, unit, prefix, formatter, goal in values
    ]


def _assignment_alternative(
    alternative: AssignmentAlternative,
    card: Card,
    source_path: str,
) -> AlternativeExplanation:
    if not alternative.feasible:
        return AlternativeExplanation(
            card_id=card.id,
            card_name=card.name,
            feasible=False,
            summary=f"{card.name} is blocked for this final-plan move.",
            lines=[
                _issue_line(issue, f"{source_path}.issues[{index}]")
                for index, issue in enumerate(alternative.issues)
            ],
        )
    if alternative.metric_deltas is None or alternative.total_utility_delta is None:
        raise ExplanationContractError(source_path, "feasible alternative lacks deltas")
    return AlternativeExplanation(
        card_id=card.id,
        card_name=card.name,
        feasible=True,
        summary=(
            f"Moving only this purchase to {card.name} "
            + (
                "improves overall plan value"
                if alternative.total_utility_delta > 0
                else "reduces overall plan value"
                if alternative.total_utility_delta < 0
                else "leaves overall plan value unchanged"
            )
            + " once every weighted goal is combined; see the metric changes below."
        ),
        utility_delta_points=alternative.total_utility_delta,
        lines=_delta_lines(alternative.metric_deltas, f"{source_path}.metric_deltas"),
    )


def _card_constraint_lines(
    summary: CardPlanSummary,
    source_path: str,
) -> list[ExplanationLine]:
    lines: list[ExplanationLine] = []
    for index, slack in enumerate(summary.constraint_slacks):
        if slack.binding:
            tone = ExplanationTone.CAUTION
            state = "binding"
        elif slack.near_binding:
            tone = ExplanationTone.CAUTION
            state = "near binding"
        else:
            tone = ExplanationTone.NEUTRAL
            state = "not binding"
        label = {
            ConstraintKind.CREDIT_LIMIT: "credit-limit-slack",
            ConstraintKind.UTILIZATION_CEILING: "utilization-ceiling-slack",
            ConstraintKind.SIGNUP_BONUS: "signup-bonus-slack",
        }[slack.kind]
        lines.append(
            _line(
                kind=ExplanationKind.CONSTRAINT,
                tone=tone,
                label=label,
                text=(
                    f"{humanize_identifier(slack.kind.value)} is {state}; "
                    f"slack is {format_cents(slack.slack_cents)}."
                ),
                raw_value=slack.slack_cents,
                unit=ExplanationUnit.CENTS,
                source_path=f"{source_path}.constraint_slacks[{index}].slack_cents",
            )
        )
    return lines


def _card_summary(
    summary: CardPlanSummary,
    card: Card,
    source_path: str,
) -> CardSummaryExplanation:
    lines = [
        _line(
            kind=ExplanationKind.REWARD,
            tone=ExplanationTone.NEUTRAL,
            label="assigned-spend",
            text=f"Assigned spend: {format_cents(summary.assigned_spend_cents)}.",
            raw_value=summary.assigned_spend_cents,
            unit=ExplanationUnit.CENTS,
            source_path=f"{source_path}.assigned_spend_cents",
        ),
        _line(
            kind=ExplanationKind.UTILIZATION,
            tone=ExplanationTone.NEUTRAL,
            label="ending-balance",
            text=f"Ending balance: {format_cents(summary.ending_balance_cents)}.",
            raw_value=summary.ending_balance_cents,
            unit=ExplanationUnit.CENTS,
            source_path=f"{source_path}.ending_balance_cents",
        ),
        _line(
            kind=ExplanationKind.UTILIZATION,
            tone=ExplanationTone.NEUTRAL,
            label="ending-utilization",
            text=f"Ending utilization: {format_bps(summary.ending_utilization_bps)}.",
            raw_value=summary.ending_utilization_bps,
            unit=ExplanationUnit.BPS,
            source_path=f"{source_path}.ending_utilization_bps",
            goal=Goal.CREDIT_HEALTH,
        ),
    ]
    if summary.bonus_remaining_cents is not None:
        if summary.bonus_hit:
            text = "The synthetic signup threshold is reached."
            tone = ExplanationTone.POSITIVE
        else:
            text = (
                "Synthetic signup threshold remaining: "
                f"{format_cents(summary.bonus_remaining_cents)}."
            )
            tone = ExplanationTone.NEUTRAL
        lines.append(
            _line(
                kind=ExplanationKind.BONUS,
                tone=tone,
                label="bonus-remaining",
                text=text,
                raw_value=summary.bonus_remaining_cents,
                unit=ExplanationUnit.CENTS,
                source_path=f"{source_path}.bonus_remaining_cents",
                goal=Goal.HIT_SIGNUP_BONUS,
            )
        )
    lines.extend(_card_constraint_lines(summary, source_path))
    return CardSummaryExplanation(
        card_id=card.id,
        card_name=card.name,
        headline=f"{card.name} account summary",
        lines=lines,
    )


def _assignment_card(
    assignment: PurchaseAssignment,
    assignment_index: int,
    card: Card,
    purchase: Purchase,
    summary: CardPlanSummary,
    cards_by_id: Mapping[str, Card],
    result: AllocationResult,
    intent: Intent,
) -> DecisionCard:
    source_path = f"assignments[{assignment_index}]"
    alternative = None
    warning_lines: list[ExplanationLine] = []
    if assignment.alternatives:
        selected = next(
            (candidate for candidate in assignment.alternatives if candidate.feasible),
            assignment.alternatives[0],
        )
        alternative_card = _require(
            cards_by_id,
            selected.card_id,
            f"{source_path}.alternatives.card_id",
        )
        alternative = _assignment_alternative(
            selected,
            alternative_card,
            f"{source_path}.alternatives[{assignment.alternatives.index(selected)}]",
        )
        improving_alternative = (
            selected.feasible
            and selected.total_utility_delta is not None
            and selected.total_utility_delta > 0
        )
        if improving_alternative and result.status is OptimizationStatus.OPTIMAL:
            raise ExplanationContractError(
                f"{source_path}.alternatives",
                "optimal plan contains an improving one-purchase move",
            )
        if improving_alternative:
            warning_lines.append(
                _line(
                    kind=ExplanationKind.WARNING,
                    tone=ExplanationTone.CAUTION,
                    label="improving-alternative-detected",
                    text=(
                        "The heuristic trace contains an improving one-purchase move; "
                        "optimality is not claimed."
                    ),
                    source_path=f"{source_path}.alternatives",
                )
            )
    factors = CandidateDecision(
        card_id=assignment.card_id,
        feasible=True,
        raw_factors=assignment.raw_factors,
        objective=assignment.objective,
    )
    if assignment.raw_factors.travel_value_cents:
        warning_lines.append(
            _line(
                kind=ExplanationKind.WARNING,
                tone=ExplanationTone.NEUTRAL,
                label="static-point-value-assumption",
                text="Travel-point value uses a documented static product assumption.",
                source_path=f"{source_path}.raw_factors.travel_value_cents",
            )
        )
    return DecisionCard(
        card_id=card.id,
        card_name=card.name,
        purchase_id=purchase.id,
        purchase_label=humanize_identifier(purchase.id),
        headline=f"Route {humanize_identifier(purchase.id)} to {card.name}.",
        status=result.status,
        solver_method=result.solver_method,
        factor_lines=_candidate_factor_lines(factors, intent, purchase, source_path),
        constraint_lines=_card_constraint_lines(
            summary,
            f"card_summaries[{result.card_summaries.index(summary)}]",
        ),
        alternative=alternative,
        warning_lines=warning_lines,
    )


def explain_allocation(
    result: AllocationResult,
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
) -> AllocationExplanation:
    cards_by_id = _unique_lookup(cards, kind="cards")
    purchases_by_id = _unique_lookup(purchases, kind="purchases")
    if not result.successful:
        return AllocationExplanation(
            status=result.status,
            solver_method=result.solver_method,
            headline=(
                "The heuristic did not find a complete monthly plan."
                if result.status is OptimizationStatus.UNRESOLVED
                else "No monthly plan satisfies all modeled hard constraints."
            ),
            failure=_failure(result.status, result.solver_method, result.issues, "allocation"),
        )
    metrics = result.metrics
    if metrics is None:
        raise ExplanationContractError("metrics", "successful allocation has no metrics")
    summary_by_card = {summary.card_id: summary for summary in result.card_summaries}
    if len(summary_by_card) != len(result.card_summaries):
        raise ExplanationContractError("card_summaries", "duplicate card IDs")
    max_summary = max(
        result.card_summaries,
        key=lambda summary: (summary.ending_utilization_bps, summary.card_id),
    )
    max_card = _require(cards_by_id, max_summary.card_id, "card_summaries.max.card_id")
    summary_lines = [
        _solver_line(result.status, result.solver_method, "status"),
        _line(
            kind=ExplanationKind.REWARD,
            tone=ExplanationTone.POSITIVE,
            label="projected-reward-value",
            text=(
                "Projected reward value: "
                f"{format_cents(metrics.projected_reward_value_cents)}."
            ),
            raw_value=metrics.projected_reward_value_cents,
            unit=ExplanationUnit.CENTS,
            source_path="metrics.projected_reward_value_cents",
        ),
        _line(
            kind=ExplanationKind.UTILIZATION,
            tone=ExplanationTone.NEUTRAL,
            label="maximum-utilization",
            text=(
                f"Highest ending utilization is {format_bps(metrics.max_card_utilization_bps)} "
                f"on {max_card.name}."
            ),
            raw_value=metrics.max_card_utilization_bps,
            unit=ExplanationUnit.BPS,
            source_path="metrics.max_card_utilization_bps",
            goal=Goal.CREDIT_HEALTH,
        ),
        _line(
            kind=ExplanationKind.CASHFLOW,
            tone=ExplanationTone.NEUTRAL,
            label="total-float-days",
            text=f"Total modeled interest-free float: {format_days(metrics.cashflow_days_total)}.",
            raw_value=metrics.cashflow_days_total,
            unit=ExplanationUnit.DAYS,
            source_path="metrics.cashflow_days_total",
            goal=Goal.MAX_CASHFLOW,
        ),
    ]
    if metrics.cashflow_value_cents:
        summary_lines.append(
            _line(
                kind=ExplanationKind.CASHFLOW,
                tone=ExplanationTone.NEUTRAL,
                label="total-carrying-value",
                text=(
                    "Configured carrying-value estimate: "
                    f"{format_cents(metrics.cashflow_value_cents)}."
                ),
                raw_value=metrics.cashflow_value_cents,
                unit=ExplanationUnit.CENTS,
                source_path="metrics.cashflow_value_cents",
                goal=Goal.MAX_CASHFLOW,
            )
        )
    if metrics.cashback_cents:
        summary_lines.append(
            _line(
                kind=ExplanationKind.REWARD,
                tone=ExplanationTone.NEUTRAL,
                label="projected-cashback",
                text=f"Projected cashback: {format_cents(metrics.cashback_cents)}.",
                raw_value=metrics.cashback_cents,
                unit=ExplanationUnit.CENTS,
                source_path="metrics.cashback_cents",
                goal=Goal.MAX_CASHBACK,
            )
        )
    if metrics.travel_value_cents:
        summary_lines.append(
            _line(
                kind=ExplanationKind.TRAVEL,
                tone=ExplanationTone.NEUTRAL,
                label="static-travel-value",
                text=f"Static travel-point value: {format_cents(metrics.travel_value_cents)}.",
                raw_value=metrics.travel_value_cents,
                unit=ExplanationUnit.CENTS,
                source_path="metrics.travel_value_cents",
                goal=Goal.MAX_TRAVEL,
            )
        )
    if metrics.signup_progress_cents:
        summary_lines.append(
            _line(
                kind=ExplanationKind.BONUS,
                tone=(
                    ExplanationTone.POSITIVE
                    if metrics.signup_bonus_earned_cents
                    else ExplanationTone.NEUTRAL
                ),
                label="signup-progress",
                text=(
                    f"Qualifying signup spend progress: "
                    f"{format_cents(metrics.signup_progress_cents)}."
                ),
                raw_value=metrics.signup_progress_cents,
                unit=ExplanationUnit.CENTS,
                source_path="metrics.signup_progress_cents",
                goal=Goal.HIT_SIGNUP_BONUS,
            )
        )
    if metrics.signup_bonus_earned_cents:
        summary_lines.append(
            _line(
                kind=ExplanationKind.BONUS,
                tone=ExplanationTone.POSITIVE,
                label="signup-bonus-earned",
                text=(
                    "Projected newly earned signup-bonus value: "
                    f"{format_cents(metrics.signup_bonus_earned_cents)}."
                ),
                raw_value=metrics.signup_bonus_earned_cents,
                unit=ExplanationUnit.CENTS,
                source_path="metrics.signup_bonus_earned_cents",
                goal=Goal.HIT_SIGNUP_BONUS,
            )
        )
    card_summaries = []
    for index, summary in enumerate(result.card_summaries):
        card = _require(
            cards_by_id,
            summary.card_id,
            f"card_summaries[{index}].card_id",
        )
        if summary.assigned_spend_cents > 0 or card.current_balance_cents > 0:
            card_summaries.append(
                _card_summary(summary, card, f"card_summaries[{index}]")
            )
    assignments_by_purchase = {
        assignment.purchase_id: assignment for assignment in result.assignments
    }
    if len(assignments_by_purchase) != len(result.assignments):
        raise ExplanationContractError("assignments", "duplicate purchase IDs")
    decision_cards = []
    for purchase in sorted(purchases, key=lambda item: (item.date, item.id)):
        assignment = assignments_by_purchase.get(purchase.id)
        if assignment is None:
            raise ExplanationContractError(
                "assignments",
                f"missing assignment for purchase {purchase.id!r}",
            )
        card = _require(cards_by_id, assignment.card_id, "assignments.card_id")
        summary = summary_by_card.get(card.id)
        if summary is None:
            raise ExplanationContractError(
                "card_summaries",
                f"missing summary for assigned card {card.id!r}",
            )
        decision_cards.append(
            _assignment_card(
                assignment,
                result.assignments.index(assignment),
                card,
                _require(purchases_by_id, assignment.purchase_id, "assignments.purchase_id"),
                summary,
                cards_by_id,
                result,
                intent,
            )
        )
    highlighted = [
        purchase.id
        for purchase in sorted(purchases, key=lambda item: item.id)
        if purchase.is_recurring and purchase.category == "rent"
    ]
    for purchase in sorted(purchases, key=lambda item: (-item.amount_cents, item.id)):
        if purchase.id not in highlighted:
            highlighted.append(purchase.id)
        if len(highlighted) >= 3:
            break
    warning_lines = [
        _line(
            kind=ExplanationKind.WARNING,
            tone=ExplanationTone.CAUTION,
            label=f"allocation-warning-{index + 1}",
            text=warning,
            source_path=f"warnings[{index}]",
        )
        for index, warning in enumerate(result.warnings)
    ]
    if metrics.travel_value_cents:
        warning_lines.append(
            _line(
                kind=ExplanationKind.WARNING,
                tone=ExplanationTone.NEUTRAL,
                label="static-point-value-assumption",
                text="Travel-point values use documented static product assumptions.",
                source_path="metrics.travel_value_cents",
            )
        )
    return AllocationExplanation(
        status=result.status,
        solver_method=result.solver_method,
        headline="Monthly payment plan",
        summary_lines=summary_lines,
        card_summaries=card_summaries,
        decision_cards=decision_cards,
        highlighted_purchase_ids=highlighted,
        warning_lines=warning_lines,
    )
