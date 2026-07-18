from __future__ import annotations

from datetime import date

import pytest

from data.loaders import load_eval_probes, load_scenario
from engine.greedy import allocate_greedy
from engine.models import (
    AllocationResult,
    AssignmentAlternative,
    Card,
    Constraint,
    Goal,
    Intent,
    IssueCode,
    MetricDelta,
    OptimizationIssue,
    OptimizationStatus,
    Purchase,
    SolverMethod,
)
from engine.recommend import recommend_purchase
from explain.builder import explain_allocation, explain_recommendation
from explain.models import ExplanationContractError, ExplanationKind, ExplanationTone


def one_hot(goal: Goal, constraints: Constraint | None = None) -> Intent:
    return Intent(
        weights={candidate: int(candidate is goal) for candidate in Goal},
        constraints=constraints or Constraint(),
    )


def test_recommendation_uses_real_candidate_factors_and_runner_up() -> None:
    probe = next(
        probe for probe in load_eval_probes() if probe.id == "grocery-reward-vs-health"
    )
    intent = one_hot(Goal.MAX_CASHBACK)
    result = recommend_purchase(probe.cards, probe.purchase, intent)

    explanation = explain_recommendation(result, probe.cards, probe.purchase, intent)

    assert explanation.status is OptimizationStatus.OPTIMAL
    assert explanation.decision_card is not None
    assert explanation.decision_card.card_id == result.winner.card_id
    assert explanation.decision_card.alternative is not None
    assert explanation.decision_card.alternative.card_id == result.runner_up.card_id
    cashback_line = next(
        line
        for line in explanation.decision_card.factor_lines
        if line.label == "projected-cashback"
    )
    assert cashback_line.raw_value == result.winner.raw_factors.cashback_cents
    assert cashback_line.source_path == "winner.raw_factors.cashback_cents"


def test_recommendation_respects_hard_ceiling_and_explains_excluded_card() -> None:
    probe = next(
        probe
        for probe in load_eval_probes()
        if probe.id == "grocery-reward-vs-health"
    )
    intent = one_hot(
        Goal.CREDIT_HEALTH,
        Constraint(max_utilization_bps=3_000),
    )
    result = recommend_purchase(probe.cards, probe.purchase, intent)

    explanation = explain_recommendation(result, probe.cards, probe.purchase, intent)

    assert explanation.decision_card is not None
    ceiling_line = next(
        line
        for line in explanation.decision_card.factor_lines
        if line.label == "utilization-ceiling"
    )
    assert ceiling_line.raw_value == result.winner.raw_factors.utilization_after_bps
    assert all(not alternative.feasible for alternative in explanation.excluded_alternatives)


def test_infeasible_recommendation_preserves_issues_and_suggestions() -> None:
    card = Card(
        id="small",
        name="Small Card",
        credit_limit_cents=5_000,
        current_balance_cents=0,
        reward_rules=[],
        base_rate_bps=100,
        base_reward_type="cashback",
        point_value_millicents=1_000,
        annual_fee_cents=0,
        statement_day=10,
        due_day=5,
    )
    purchase = Purchase(
        id="large-purchase",
        amount_cents=10_000,
        category="other",
        date=date(2026, 8, 1),
        is_recurring=False,
    )
    intent = one_hot(Goal.MAX_CASHBACK)
    result = recommend_purchase([card], purchase, intent)

    explanation = explain_recommendation(result, [card], purchase, intent)

    assert explanation.failure is not None
    assert explanation.decision_card is None
    assert explanation.failure.lines[0].kind is ExplanationKind.SOLVER
    assert any(line.tone is ExplanationTone.BLOCKING for line in explanation.failure.lines)
    assert explanation.failure.suggestions


def test_sarah_allocation_explanation_uses_final_state_alternatives_and_slack() -> None:
    loaded = load_scenario()
    intent = loaded.demo_intents["mortgage"]
    result = allocate_greedy(
        loaded.scenario.cards,
        loaded.scenario.purchases,
        intent,
    )

    explanation = explain_allocation(
        result,
        loaded.scenario.cards,
        loaded.scenario.purchases,
        intent,
    )

    assert explanation.status is OptimizationStatus.HEURISTIC
    assert len(explanation.decision_cards) == len(loaded.scenario.purchases)
    assert explanation.highlighted_purchase_ids[0] == "rent-aug"
    assert len(explanation.highlighted_purchase_ids) == 3
    assert all(card.alternative is not None for card in explanation.decision_cards)
    assert any(
        line.label.endswith("slack")
        for card_summary in explanation.card_summaries
        for line in card_summary.lines
    )
    rent_assignment = next(
        assignment for assignment in result.assignments if assignment.purchase_id == "rent-aug"
    )
    rent_card = next(
        card for card in explanation.decision_cards if card.purchase_id == "rent-aug"
    )
    expected_alternative = next(
        (candidate for candidate in rent_assignment.alternatives if candidate.feasible),
        rent_assignment.alternatives[0],
    )
    assert rent_card.alternative.card_id == expected_alternative.card_id


def test_partial_bonus_progress_is_never_described_as_earned() -> None:
    loaded = load_scenario()
    intent = loaded.demo_intents["mortgage"]
    result = allocate_greedy(
        loaded.scenario.cards,
        loaded.scenario.purchases[:2],
        intent.model_copy(update={"constraints": Constraint()}),
    )
    explanation = explain_allocation(
        result,
        loaded.scenario.cards,
        loaded.scenario.purchases[:2],
        intent.model_copy(update={"constraints": Constraint()}),
    )

    progress_lines = [
        line
        for line in explanation.summary_lines
        if line.label == "signup-progress"
    ]
    if progress_lines and result.metrics.signup_bonus_earned_cents == 0:
        assert "qualifying signup spend progress" in progress_lines[0].text.lower()
        assert "earns the signup bonus" not in progress_lines[0].text.lower()
        assert not any(
            line.label == "signup-bonus-earned" for line in explanation.summary_lines
        )


@pytest.mark.parametrize(
    ("status", "expected_text"),
    [
        (OptimizationStatus.INFEASIBLE, "No monthly plan"),
        (OptimizationStatus.UNRESOLVED, "heuristic did not find"),
    ],
)
def test_failed_allocation_wording_distinguishes_proof(
    status: OptimizationStatus,
    expected_text: str,
) -> None:
    method = SolverMethod.ILP if status is OptimizationStatus.INFEASIBLE else SolverMethod.GREEDY
    issue = OptimizationIssue(
        code=(
            IssueCode.NO_FEASIBLE_ASSIGNMENT
            if status is OptimizationStatus.INFEASIBLE
            else IssueCode.HEURISTIC_DEAD_END
        ),
        message="Synthetic failure.",
        suggestion="Adjust a constraint.",
    )
    result = AllocationResult(
        status=status,
        solver_method=method,
        issues=[issue],
    )

    explanation = explain_allocation(result, [], [], Intent.equal_weights())

    assert expected_text.lower() in explanation.headline.lower()
    assert explanation.failure is not None
    if status is OptimizationStatus.UNRESOLVED:
        assert "infeasibility is not proven" in explanation.failure.lines[0].text.lower()


def test_fallback_allocation_discloses_solver_failure_without_claiming_optimality() -> None:
    loaded = load_scenario()
    intent = loaded.demo_intents["travel"]
    greedy = allocate_greedy(
        loaded.scenario.cards,
        loaded.scenario.purchases[:3],
        intent,
    )
    fallback = AllocationResult(
        status=OptimizationStatus.HEURISTIC_FALLBACK,
        solver_method=SolverMethod.GREEDY,
        assignments=greedy.assignments,
        card_summaries=greedy.card_summaries,
        metrics=greedy.metrics,
        warnings=["CBC exceeded the wall-clock limit."],
    )

    explanation = explain_allocation(
        fallback,
        loaded.scenario.cards,
        loaded.scenario.purchases[:3],
        intent,
    )

    solver_line = explanation.summary_lines[0]
    assert "fallback" in solver_line.text.lower()
    assert "optimal" not in solver_line.text.lower()
    assert explanation.warning_lines[0].text == "CBC exceeded the wall-clock limit."


def test_missing_card_reference_raises_contract_error() -> None:
    probe = load_eval_probes()[0]
    intent = one_hot(Goal.MAX_CASHBACK)
    result = recommend_purchase(probe.cards, probe.purchase, intent)

    with pytest.raises(ExplanationContractError, match="unknown ID"):
        explain_recommendation(result, [], probe.purchase, intent)


def test_equal_inputs_produce_deeply_equal_explanations() -> None:
    probe = load_eval_probes()[0]
    intent = one_hot(Goal.MAX_CASHBACK)
    result = recommend_purchase(probe.cards, probe.purchase, intent)

    first = explain_recommendation(result, probe.cards, probe.purchase, intent)
    second = explain_recommendation(result, probe.cards, probe.purchase, intent)

    assert first.model_dump() == second.model_dump()


def test_runner_up_reward_delta_cites_existing_factor_fields() -> None:
    probe = next(
        probe for probe in load_eval_probes() if probe.id == "grocery-reward-vs-health"
    )
    intent = one_hot(Goal.MAX_CASHBACK)
    result = recommend_purchase(probe.cards, probe.purchase, intent)

    explanation = explain_recommendation(result, probe.cards, probe.purchase, intent)
    reward_line = next(
        line
        for line in explanation.decision_card.alternative.lines
        if line.label == "alternative-reward-delta"
    )

    assert ".cashback_cents" in reward_line.source_path
    assert ".travel_value_cents" in reward_line.source_path
    assert ".reward_value" not in reward_line.source_path


def test_single_card_recommendation_has_no_alternative() -> None:
    card = Card(
        id="only-card",
        name="Only Card",
        credit_limit_cents=100_000,
        current_balance_cents=0,
        reward_rules=[],
        base_rate_bps=100,
        base_reward_type="cashback",
        point_value_millicents=1_000,
        annual_fee_cents=0,
        statement_day=10,
        due_day=5,
    )
    purchase = Purchase(
        id="only-purchase",
        amount_cents=1_000,
        category="other",
        date=date(2026, 8, 1),
        is_recurring=False,
    )
    intent = Intent.equal_weights()

    explanation = explain_recommendation(
        recommend_purchase([card], purchase, intent),
        [card],
        purchase,
        intent,
    )

    assert explanation.decision_card is not None
    assert explanation.decision_card.alternative is None


def test_card_slack_lines_exactly_match_engine_slacks() -> None:
    loaded = load_scenario()
    intent = loaded.demo_intents["mortgage"]
    result = allocate_greedy(
        loaded.scenario.cards,
        loaded.scenario.purchases,
        intent,
    )
    explanation = explain_allocation(
        result,
        loaded.scenario.cards,
        loaded.scenario.purchases,
        intent,
    )
    engine_summaries = {summary.card_id: summary for summary in result.card_summaries}

    for explained_card in explanation.card_summaries:
        engine_summary = engine_summaries[explained_card.card_id]
        expected = {
            slack.kind.value: (slack.slack_cents, slack.binding, slack.near_binding)
            for slack in engine_summary.constraint_slacks
        }
        for line in explained_card.lines:
            if not line.label.endswith("slack"):
                continue
            matching = next(
                value
                for kind, value in expected.items()
                if kind.replace("_", "-") in line.label
            )
            slack_cents, binding, near_binding = matching
            assert line.raw_value == slack_cents
            assert line.tone is (
                ExplanationTone.CAUTION
                if binding or near_binding
                else ExplanationTone.NEUTRAL
            )


def test_post_cutoff_purchase_does_not_claim_utilization_ceiling_applies() -> None:
    probe = next(
        probe for probe in load_eval_probes() if probe.id == "grocery-reward-vs-health"
    )
    purchase = probe.purchase.model_copy(update={"date": date(2026, 9, 1)})
    intent = one_hot(
        Goal.CREDIT_HEALTH,
        Constraint(
            max_utilization_bps=3_000,
            max_utilization_until=date(2026, 8, 31),
        ),
    )
    result = recommend_purchase(probe.cards, purchase, intent)

    explanation = explain_recommendation(result, probe.cards, purchase, intent)

    assert explanation.decision_card is not None
    assert not any(
        line.label == "utilization-ceiling"
        for line in explanation.decision_card.factor_lines
    )


def test_improving_alternative_warns_for_heuristic_but_fails_optimal_contract() -> None:
    probe = load_eval_probes()[0]
    intent = one_hot(Goal.MAX_CASHBACK)
    result = allocate_greedy(probe.cards, [probe.purchase], intent)
    assignment = result.assignments[0]
    other_card = next(card for card in probe.cards if card.id != assignment.card_id)
    positive = AssignmentAlternative(
        card_id=other_card.id,
        feasible=True,
        resulting_plan_utility=result.metrics.total_utility + 1,
        total_utility_delta=1,
        metric_deltas=MetricDelta(total_utility=1),
    )
    changed_assignment = assignment.model_copy(update={"alternatives": [positive]})
    heuristic = result.model_copy(update={"assignments": [changed_assignment]})

    explanation = explain_allocation(
        heuristic,
        probe.cards,
        [probe.purchase],
        intent,
    )
    assert any(
        line.label == "improving-alternative-detected"
        for line in explanation.decision_cards[0].warning_lines
    )

    optimal = AllocationResult(
        status=OptimizationStatus.OPTIMAL,
        solver_method=SolverMethod.ILP,
        assignments=[changed_assignment],
        card_summaries=result.card_summaries,
        metrics=result.metrics,
    )
    with pytest.raises(ExplanationContractError, match="optimal plan"):
        explain_allocation(optimal, probe.cards, [probe.purchase], intent)
