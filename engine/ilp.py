"""Exact PuLP/CBC monthly allocation with verified greedy fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pulp

from engine.config import DEFAULT_ENGINE_CONFIG, EngineConfig
from engine.feasibility import analyze_assignment, validate_scenario
from engine.greedy import allocate_greedy, attach_alternatives
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
)
from engine.objective import evaluate_plan, quantize_intent_weights
from engine.scoring import (
    cashflow_value_cents,
    incremental_risk_penalty_points,
    incremental_utilization_penalty_points,
    reward_values,
)


def _solver_issue(code: IssueCode, message: str) -> OptimizationIssue:
    return OptimizationIssue(
        code=code,
        message=message,
        suggestion=(
            "Use the verified greedy plan or retry the exact solver with a smaller scenario."
        ),
    )


def _fallback(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    config: EngineConfig,
    issue: OptimizationIssue,
) -> AllocationResult:
    greedy = allocate_greedy(cards, purchases, intent, config)
    if greedy.successful:
        return AllocationResult(
            status=OptimizationStatus.HEURISTIC_FALLBACK,
            solver_method=SolverMethod.GREEDY,
            assignments=greedy.assignments,
            card_summaries=greedy.card_summaries,
            metrics=greedy.metrics,
            issues=[*greedy.issues, issue],
            warnings=[*greedy.warnings, issue.message],
        )
    return AllocationResult(
        status=greedy.status,
        solver_method=greedy.solver_method,
        issues=[*greedy.issues, issue],
        warnings=[*greedy.warnings, issue.message],
    )


def _cbc_solver(config: EngineConfig) -> pulp.LpSolver:
    return pulp.COIN_CMD(
        path=pulp.PULP_CBC_CMD.pulp_cbc_path,
        msg=False,
        timeLimit=config.ilp_timeout_seconds,
        threads=1,
        options=["randomSeed 1"],
    )


def _expression_abs_bound(expression: pulp.LpAffineExpression) -> int:
    bound = abs(int(expression.constant))
    for variable, coefficient in expression.items():
        if variable.lowBound is None or variable.upBound is None:
            raise ValueError(f"variable {variable.name} lacks finite bounds")
        variable_bound = max(abs(variable.lowBound), abs(variable.upBound))
        bound += abs(int(coefficient)) * int(variable_bound)
    return bound


def _reachable_sums(
    amounts: Sequence[int],
    capacity: int,
    maximum_states: int,
) -> list[int] | None:
    reachable = {0}
    for amount in amounts:
        reachable.update(
            current + amount
            for current in tuple(reachable)
            if current + amount <= capacity
        )
        if len(reachable) > maximum_states:
            return None
    return sorted(reachable)


def _state_variables(
    problem: pulp.LpProblem,
    name: str,
    states: Sequence[int],
) -> dict[int, pulp.LpVariable]:
    variables = {
        state: problem.add_variable(
            f"{name}_{state}",
            lowBound=0,
            upBound=1,
            cat=pulp.LpBinary,
        )
        for state in states
    }
    problem += pulp.lpSum(variables.values()) == 1, f"{name}_choose_one"
    return variables


def _signup_goal_points(card: Card, eligible_spend_cents: int, config: EngineConfig) -> int:
    bonus = card.signup_bonus
    if bonus is None or bonus.remaining_spend_cents == 0:
        return 0
    remaining = bonus.remaining_spend_cents
    progress = min(remaining, eligible_spend_cents)
    progress_pool = bonus.reward_value_cents * config.signup_progress_pool_bps // 10_000
    progress_points = progress_pool * progress // remaining
    completion_points = bonus.reward_value_cents - progress_pool if progress == remaining else 0
    return progress_points + completion_points


def _solve_status(problem: pulp.LpProblem, solver: pulp.LpSolver) -> str:
    status_code = problem.solve(solver)
    return pulp.LpStatus.get(status_code, "Undefined")


def _rounded_integer_objective(expression: pulp.LpAffineExpression) -> int:
    value = pulp.value(expression)
    if value is None:
        raise ValueError("CBC returned no objective value")
    rounded = round(value)
    if abs(value - rounded) > 1e-4:
        raise ValueError(f"CBC returned a non-integral objective value: {value}")
    return int(rounded)


def _assignment_from_variables(
    purchases: Sequence[Purchase],
    cards: Sequence[Card],
    variables: Mapping[tuple[str, str], pulp.LpVariable],
) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for purchase in sorted(purchases, key=lambda item: item.id):
        selected = [
            card.id
            for card in sorted(cards, key=lambda item: item.id)
            if (pulp.value(variables[purchase.id, card.id]) or 0) > 0.5
        ]
        if len(selected) != 1:
            raise ValueError(
                f"CBC selected {len(selected)} cards for purchase {purchase.id}"
            )
        assignments[purchase.id] = selected[0]
    return assignments


def _lexicographic_secondary(
    purchases: Sequence[Purchase],
    cards: Sequence[Card],
    variables: Mapping[tuple[str, str], pulp.LpVariable],
) -> tuple[pulp.LpAffineExpression, int]:
    ordered_purchases = sorted(purchases, key=lambda item: item.id)
    ordered_cards = sorted(cards, key=lambda item: item.id)
    base = max(1, len(ordered_cards))
    expression = pulp.LpAffineExpression()
    for purchase_index, purchase in enumerate(ordered_purchases):
        place = base ** (len(ordered_purchases) - purchase_index - 1)
        for card_index, card in enumerate(ordered_cards):
            digit = len(ordered_cards) - card_index - 1
            expression += digit * place * variables[purchase.id, card.id]
    return expression, (base ** len(ordered_purchases)) - 1


def _fix_lexicographically(
    problem: pulp.LpProblem,
    purchases: Sequence[Purchase],
    cards: Sequence[Card],
    variables: Mapping[tuple[str, str], pulp.LpVariable],
    solver: pulp.LpSolver,
    config: EngineConfig,
) -> str:
    secondary, secondary_bound = _lexicographic_secondary(purchases, cards, variables)
    if secondary_bound <= config.cbc_exact_integer_limit:
        problem.setObjective(secondary)
        return _solve_status(problem, solver)

    ordered_cards = sorted(cards, key=lambda item: item.id)
    for index, purchase in enumerate(sorted(purchases, key=lambda item: item.id)):
        preference = pulp.lpSum(
            (len(ordered_cards) - card_index - 1) * variables[purchase.id, card.id]
            for card_index, card in enumerate(ordered_cards)
        )
        problem.setObjective(preference)
        status = _solve_status(problem, solver)
        if status != "Optimal":
            return status
        selected = max(
            ordered_cards,
            key=lambda card: (pulp.value(variables[purchase.id, card.id]) or 0),
        )
        problem += variables[purchase.id, selected.id] == 1, f"lex_fix_{index}"
    return "Optimal"


def allocate_ilp(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
    *,
    solver: pulp.LpSolver | None = None,
    include_alternatives: bool = True,
) -> AllocationResult:
    """Solve the monthly allocation exactly under the shared modeled objective."""

    scenario_issues = validate_scenario(cards, purchases, intent)
    if scenario_issues:
        return AllocationResult(
            status=OptimizationStatus.INFEASIBLE,
            solver_method=SolverMethod.ILP,
            issues=scenario_issues,
        )

    active_solver = solver or _cbc_solver(config)
    if not active_solver.available():
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(IssueCode.SOLVER_ERROR, "CBC is unavailable."),
        )

    ordered_cards = sorted(cards, key=lambda item: item.id)
    ordered_purchases = sorted(purchases, key=lambda item: item.id)
    card_index = {card.id: index for index, card in enumerate(ordered_cards)}
    purchase_index = {
        purchase.id: index for index, purchase in enumerate(ordered_purchases)
    }
    weights = quantize_intent_weights(intent, config)
    problem = pulp.LpProblem("payment_allocation", pulp.LpMaximize)
    variables = {
        (purchase.id, card.id): problem.add_variable(
            f"assign_p{purchase_index[purchase.id]}_c{card_index[card.id]}",
            lowBound=0,
            upBound=1,
            cat=pulp.LpBinary,
        )
        for purchase in ordered_purchases
        for card in ordered_cards
    }

    for purchase in ordered_purchases:
        problem += (
            pulp.lpSum(variables[purchase.id, card.id] for card in ordered_cards) == 1,
            f"assign_once_p{purchase_index[purchase.id]}",
        )
        if purchase.locked_card_id is not None:
            problem += (
                variables[purchase.id, purchase.locked_card_id] == 1,
                f"lock_p{purchase_index[purchase.id]}",
            )

    spend_by_card: dict[str, pulp.LpAffineExpression] = {}
    for card in ordered_cards:
        spend = pulp.lpSum(
            purchase.amount_cents * variables[purchase.id, card.id]
            for purchase in ordered_purchases
        )
        spend_by_card[card.id] = spend
        available_credit = max(
            0, card.credit_limit_cents - card.current_balance_cents
        )
        problem += spend <= available_credit, f"credit_limit_c{card_index[card.id]}"

        ceiling = intent.constraints.max_utilization_bps
        cutoff = intent.constraints.max_utilization_until
        relevant = [
            purchase
            for purchase in ordered_purchases
            if ceiling is not None and (cutoff is None or purchase.date <= cutoff)
        ]
        if relevant:
            relevant_spend = pulp.lpSum(
                purchase.amount_cents * variables[purchase.id, card.id]
                for purchase in relevant
            )
            assert ceiling is not None
            problem += (
                10_000 * (card.current_balance_cents + relevant_spend)
                <= ceiling * card.credit_limit_cents,
                f"utilization_ceiling_c{card_index[card.id]}",
            )

    primary = pulp.LpAffineExpression()
    for purchase in ordered_purchases:
        for card in ordered_cards:
            rewards = reward_values(card, purchase)
            coefficient = (
                weights[Goal.MAX_CASHBACK] * rewards.cashback_cents
                + weights[Goal.MAX_TRAVEL] * rewards.travel_value_cents
                + weights[Goal.MAX_CASHFLOW]
                * cashflow_value_cents(card, purchase, config)
            )
            primary += coefficient * variables[purchase.id, card.id]

    for card in ordered_cards:
        available_credit = max(
            0, card.credit_limit_cents - card.current_balance_cents
        )
        if weights[Goal.CREDIT_HEALTH] > 0 or weights[Goal.MIN_RISK] > 0:
            state_capacity = available_credit
            if (
                intent.constraints.max_utilization_bps is not None
                and intent.constraints.max_utilization_until is None
            ):
                maximum_balance = (
                    intent.constraints.max_utilization_bps
                    * card.credit_limit_cents
                    // 10_000
                )
                state_capacity = min(
                    state_capacity,
                    max(0, maximum_balance - card.current_balance_cents),
                )
            spend_states = _reachable_sums(
                [purchase.amount_cents for purchase in ordered_purchases],
                state_capacity,
                config.ilp_max_card_states,
            )
            if spend_states is None:
                return _fallback(
                    cards,
                    purchases,
                    intent,
                    config,
                    _solver_issue(
                        IssueCode.SOLVER_ERROR,
                        f"Card {card.id} exceeds the exact spend-state limit.",
                    ),
                )
            states = _state_variables(
                problem,
                f"spend_state_c{card_index[card.id]}",
                spend_states,
            )
            problem += (
                spend_by_card[card.id]
                == pulp.lpSum(state * variable for state, variable in states.items()),
                f"spend_state_link_c{card_index[card.id]}",
            )
            for state, variable in states.items():
                aggregate_coefficient = 0
                if weights[Goal.CREDIT_HEALTH] > 0:
                    aggregate_coefficient -= weights[Goal.CREDIT_HEALTH] * (
                        incremental_utilization_penalty_points(card, state, config)
                    )
                if weights[Goal.MIN_RISK] > 0:
                    aggregate_coefficient -= weights[Goal.MIN_RISK] * (
                        incremental_risk_penalty_points(card, state, config)
                    )
                primary += aggregate_coefficient * variable

        bonus = card.signup_bonus
        if bonus is None or bonus.remaining_spend_cents == 0:
            continue
        forced = card.id in intent.constraints.must_hit_bonus_card_ids
        if weights[Goal.HIT_SIGNUP_BONUS] == 0 and not forced:
            continue
        eligible = [
            purchase
            for purchase in ordered_purchases
            if purchase.date <= bonus.deadline_date
        ]
        eligible_spend = pulp.lpSum(
            purchase.amount_cents * variables[purchase.id, card.id]
            for purchase in eligible
        )
        remaining = bonus.remaining_spend_cents
        eligible_states = _reachable_sums(
            [purchase.amount_cents for purchase in eligible],
            available_credit,
            config.ilp_max_card_states,
        )
        if eligible_states is None:
            return _fallback(
                cards,
                purchases,
                intent,
                config,
                _solver_issue(
                    IssueCode.SOLVER_ERROR,
                    f"Card {card.id} exceeds the exact bonus-state limit.",
                ),
            )
        states = _state_variables(
            problem,
            f"bonus_state_c{card_index[card.id]}",
            eligible_states,
        )
        problem += (
            eligible_spend
            == pulp.lpSum(state * variable for state, variable in states.items()),
            f"bonus_state_link_c{card_index[card.id]}",
        )
        if forced:
            problem += (
                pulp.lpSum(variable for state, variable in states.items() if state >= remaining)
                == 1,
                f"forced_bonus_c{card_index[card.id]}",
            )
        if weights[Goal.HIT_SIGNUP_BONUS] > 0:
            primary += pulp.lpSum(
                weights[Goal.HIT_SIGNUP_BONUS]
                * _signup_goal_points(card, state, config)
                * variable
                for state, variable in states.items()
            )

    try:
        primary_bound = _expression_abs_bound(primary)
    except ValueError as exc:
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(IssueCode.SOLVER_ERROR, str(exc)),
        )
    if primary_bound > config.cbc_exact_integer_limit:
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(
                IssueCode.SOLVER_ERROR,
                "The exact objective exceeds CBC's configured exact-integer bound.",
            ),
        )

    problem.setObjective(primary)
    try:
        status = _solve_status(problem, active_solver)
    except (pulp.PulpError, OSError, ValueError) as exc:
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(IssueCode.SOLVER_ERROR, f"CBC failed: {exc}"),
        )

    if status == "Infeasible":
        return AllocationResult(
            status=OptimizationStatus.INFEASIBLE,
            solver_method=SolverMethod.ILP,
            issues=[
                _solver_issue(
                    IssueCode.NO_FEASIBLE_ASSIGNMENT,
                    "CBC proved that the combined hard constraints are infeasible.",
                )
            ],
        )
    if status != "Optimal":
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(
                IssueCode.SOLVER_TIMEOUT,
                f"CBC did not prove an optimum (status: {status}).",
            ),
        )

    try:
        primary_optimum = _rounded_integer_objective(primary)
        problem += primary == primary_optimum, "fix_primary_optimum"
        secondary_status = _fix_lexicographically(
            problem,
            ordered_purchases,
            ordered_cards,
            variables,
            active_solver,
            config,
        )
    except (pulp.PulpError, OSError, ValueError) as exc:
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(IssueCode.SOLVER_ERROR, f"CBC tie-break failed: {exc}"),
        )
    if secondary_status != "Optimal":
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(
                IssueCode.SOLVER_TIMEOUT,
                f"CBC did not finish deterministic tie-breaking (status: {secondary_status}).",
            ),
        )

    try:
        assignments = _assignment_from_variables(
            ordered_purchases, ordered_cards, variables
        )
        feasibility = analyze_assignment(cards, purchases, intent, assignments)
        if not feasibility.feasible:
            raise ValueError("CBC returned an assignment that failed hard-constraint validation")
        evaluation = evaluate_plan(
            list(cards), list(purchases), intent, assignments, config
        )
        if evaluation.objective.total_utility != primary_optimum:
            raise ValueError(
                "CBC and Python objective values differ: "
                f"{primary_optimum} != {evaluation.objective.total_utility}"
            )
    except ValueError as exc:
        return _fallback(
            cards,
            purchases,
            intent,
            config,
            _solver_issue(IssueCode.SOLVER_ERROR, str(exc)),
        )

    result_assignments = list(evaluation.assignments)
    if include_alternatives:
        result_assignments = attach_alternatives(
            cards, purchases, intent, assignments, evaluation, config
        )
    return AllocationResult(
        status=OptimizationStatus.OPTIMAL,
        solver_method=SolverMethod.ILP,
        assignments=result_assignments,
        card_summaries=list(evaluation.card_summaries),
        metrics=evaluation.metrics,
    )
