from __future__ import annotations

from datetime import date

from engine.feasibility import analyze_assignment, validate_scenario
from engine.models import Card, Constraint, Goal, Intent, IssueCode, Purchase, SignupBonus


def intent(constraints: Constraint | None = None) -> Intent:
    return Intent(weights={goal: 1 for goal in Goal}, constraints=constraints or Constraint())


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


def codes(report: list | tuple) -> list[IssueCode]:
    return [problem.code for problem in report]


def test_reference_checks_are_deterministic() -> None:
    cards = [card("same"), card("same")]
    problems = validate_scenario(cards, [purchase("p1", 1_000)], intent())
    assert codes(problems) == [IssueCode.DUPLICATE_ID]

    unknown_lock = validate_scenario(
        [card("known")],
        [purchase("p1", 1_000, locked_card_id="missing")],
        intent(),
    )
    assert codes(unknown_lock) == [IssueCode.UNKNOWN_LOCKED_CARD]


def test_unknown_and_nonbonus_forced_cards_are_rejected() -> None:
    constraints = Constraint(must_hit_bonus_card_ids=["missing", "plain"])
    problems = validate_scenario(
        [card("plain")],
        [purchase("p1", 1_000)],
        intent(constraints),
    )
    assert codes(problems) == [IssueCode.UNKNOWN_BONUS_CARD, IssueCode.CARD_HAS_NO_BONUS]


def test_over_limit_unused_card_does_not_block_another_card() -> None:
    cards = [
        card("over", credit_limit_cents=10_000, current_balance_cents=11_000),
        card("usable", credit_limit_cents=20_000),
    ]
    item = purchase("p1", 5_000)

    report = analyze_assignment(cards, [item], intent(), {item.id: "usable"})
    assert report.feasible

    over_report = analyze_assignment(cards, [item], intent(), {item.id: "over"})
    assert IssueCode.CARD_ALREADY_OVER_LIMIT in codes(over_report.issues)


def test_utilization_ceiling_uses_exact_inclusive_cutoff() -> None:
    constraints = Constraint(
        max_utilization_bps=3_000,
        max_utilization_until=date(2026, 8, 31),
    )
    target = card("target", credit_limit_cents=100_000, current_balance_cents=20_000)
    on_cutoff = purchase("on", 10_000, date=date(2026, 8, 31))
    after_cutoff = purchase("after", 70_000, date=date(2026, 9, 1))

    report = analyze_assignment(
        [target],
        [on_cutoff, after_cutoff],
        intent(constraints),
        {"on": "target", "after": "target"},
    )
    assert report.feasible
    assert report.utilization_slack_by_card["target"] == 0

    breached = analyze_assignment(
        [target],
        [purchase("over", 10_001, date=date(2026, 8, 31))],
        intent(constraints),
        {"over": "target"},
    )
    assert IssueCode.UTILIZATION_CEILING_EXCEEDED in codes(breached.issues)


def test_missing_unknown_and_lock_mismatch_assignments_are_structured() -> None:
    cards = [card("a"), card("b")]
    locked = purchase("locked", 1_000, locked_card_id="a")

    missing = analyze_assignment(cards, [locked], intent(), {})
    assert IssueCode.MISSING_ASSIGNMENT in codes(missing.issues)

    unknown = analyze_assignment(cards, [locked], intent(), {"locked": "missing"})
    assert IssueCode.UNKNOWN_ASSIGNED_CARD in codes(unknown.issues)

    mismatch = analyze_assignment(cards, [locked], intent(), {"locked": "b"})
    assert IssueCode.PURCHASE_LOCKED_TO_OTHER_CARD in codes(mismatch.issues)


def test_aggregate_credit_capacity_can_prove_infeasibility() -> None:
    cards = [card("a", credit_limit_cents=5_000), card("b", credit_limit_cents=5_000)]
    purchases = [purchase("p1", 6_000), purchase("p2", 6_000)]
    problems = validate_scenario(cards, purchases, intent())

    assert IssueCode.NO_FEASIBLE_ASSIGNMENT in codes(problems)


def test_forced_bonus_checks_deadline_capacity_and_final_assignment() -> None:
    bonus_card = card(
        "bonus",
        credit_limit_cents=100_000,
        signup_bonus=SignupBonus(
            spend_required_cents=50_000,
            spend_so_far_cents=10_000,
            reward_value_cents=20_000,
            deadline_date=date(2026, 8, 31),
        ),
    )
    other = card("other")
    item = purchase("p1", 40_000)
    required = intent(Constraint(must_hit_bonus_card_ids=["bonus"]))

    assert validate_scenario([bonus_card, other], [item], required) == []
    misses = analyze_assignment(
        [bonus_card, other], [item], required, {"p1": "other"}
    )
    assert IssueCode.BONUS_TARGET_UNREACHABLE in codes(misses.issues)
    hit = analyze_assignment([bonus_card, other], [item], required, {"p1": "bonus"})
    assert hit.feasible


def test_forced_bonus_with_no_deadline_eligible_spend_is_proven_infeasible() -> None:
    bonus_card = card(
        "bonus",
        signup_bonus=SignupBonus(
            spend_required_cents=10_000,
            spend_so_far_cents=0,
            reward_value_cents=5_000,
            deadline_date=date(2026, 7, 31),
        ),
    )
    problems = validate_scenario(
        [bonus_card],
        [purchase("late", 10_000, date=date(2026, 8, 1))],
        intent(Constraint(must_hit_bonus_card_ids=["bonus"])),
    )
    assert IssueCode.BONUS_DEADLINE_PASSED in codes(problems)
