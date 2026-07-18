"""Shared hard-constraint validation and exact assignment feasibility."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from engine.models import (
    Card,
    Constraint,
    Intent,
    IssueCode,
    OptimizationIssue,
    Purchase,
)


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    issues: tuple[OptimizationIssue, ...]
    assigned_spend_by_card: dict[str, int]
    credit_limit_slack_by_card: dict[str, int]
    utilization_slack_by_card: dict[str, int | None]

    @property
    def feasible(self) -> bool:
        return not self.issues


def _issue(
    code: IssueCode,
    message: str,
    suggestion: str,
    *,
    card_ids: Sequence[str] = (),
    purchase_ids: Sequence[str] = (),
    actual: int | None = None,
    required: int | None = None,
) -> OptimizationIssue:
    return OptimizationIssue(
        code=code,
        message=message,
        suggestion=suggestion,
        card_ids=list(card_ids),
        purchase_ids=list(purchase_ids),
        actual=actual,
        required=required,
    )


def _duplicates(values: Sequence[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _credit_capacity(card: Card) -> int:
    return max(0, card.credit_limit_cents - card.current_balance_cents)


def _ceiling_active_for_purchase(purchase: Purchase, constraint: Constraint) -> bool:
    if constraint.max_utilization_bps is None:
        return False
    cutoff = constraint.max_utilization_until
    return cutoff is None or purchase.date <= cutoff


def _ceiling_active_for_horizon(
    purchases: Sequence[Purchase],
    constraint: Constraint,
) -> bool:
    if constraint.max_utilization_bps is None:
        return False
    cutoff = constraint.max_utilization_until
    return cutoff is None or any(purchase.date <= cutoff for purchase in purchases)


def _utilization_capacity(card: Card, constraint: Constraint) -> int | None:
    ceiling = constraint.max_utilization_bps
    if ceiling is None:
        return None
    maximum_balance_cents = ceiling * card.credit_limit_cents // 10_000
    return max(0, maximum_balance_cents - card.current_balance_cents)


def _candidate_capacity(card: Card, purchase: Purchase, constraint: Constraint) -> int:
    capacity = _credit_capacity(card)
    if _ceiling_active_for_purchase(purchase, constraint):
        utilization_capacity = _utilization_capacity(card, constraint)
        if utilization_capacity is not None:
            capacity = min(capacity, utilization_capacity)
    return capacity


def _reference_issues(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
) -> list[OptimizationIssue]:
    card_ids = [card.id for card in cards]
    purchase_ids = [purchase.id for purchase in purchases]
    duplicate_cards = _duplicates(card_ids)
    duplicate_purchases = _duplicates(purchase_ids)
    issues: list[OptimizationIssue] = []
    if duplicate_cards or duplicate_purchases:
        issues.append(
            _issue(
                IssueCode.DUPLICATE_ID,
                "Card and purchase IDs must be unique within a scenario.",
                "Rename the duplicated synthetic IDs.",
                card_ids=duplicate_cards,
                purchase_ids=duplicate_purchases,
            )
        )
        return issues

    cards_by_id = {card.id: card for card in cards}
    for purchase in sorted(purchases, key=lambda item: item.id):
        if purchase.locked_card_id is not None and purchase.locked_card_id not in cards_by_id:
            issues.append(
                _issue(
                    IssueCode.UNKNOWN_LOCKED_CARD,
                    f"Purchase {purchase.id} is locked to an unknown card.",
                    "Unlock the purchase or select a card in this portfolio.",
                    card_ids=[purchase.locked_card_id],
                    purchase_ids=[purchase.id],
                )
            )

    for card_id in intent.constraints.must_hit_bonus_card_ids:
        card = cards_by_id.get(card_id)
        if card is None:
            issues.append(
                _issue(
                    IssueCode.UNKNOWN_BONUS_CARD,
                    f"The required bonus card {card_id} is not in the portfolio.",
                    "Remove the requirement or add the referenced synthetic card.",
                    card_ids=[card_id],
                )
            )
        elif card.signup_bonus is None:
            issues.append(
                _issue(
                    IssueCode.CARD_HAS_NO_BONUS,
                    f"Card {card_id} has no signup bonus to require.",
                    "Remove the must-hit requirement or choose a card with an active bonus.",
                    card_ids=[card_id],
                )
            )
    return issues


def validate_scenario(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
) -> list[OptimizationIssue]:
    """Return deterministic analytical contradictions known before assignment."""

    issues = _reference_issues(cards, purchases, intent)
    if issues:
        return issues

    cards_by_id = {card.id: card for card in cards}
    constraints = intent.constraints

    if _ceiling_active_for_horizon(purchases, constraints):
        ceiling = constraints.max_utilization_bps
        assert ceiling is not None
        for card in sorted(cards, key=lambda item: item.id):
            if card.current_balance_cents * 10_000 > ceiling * card.credit_limit_cents:
                issues.append(
                    _issue(
                        IssueCode.UTILIZATION_CEILING_EXCEEDED,
                        f"Card {card.id} already exceeds the active utilization ceiling.",
                        "Raise or remove the ceiling, or adjust the synthetic current balance.",
                        card_ids=[card.id],
                        actual=card.current_balance_cents,
                        required=ceiling * card.credit_limit_cents // 10_000,
                    )
                )

    for purchase in sorted(purchases, key=lambda item: item.id):
        candidate_cards = list(cards)
        if purchase.locked_card_id is not None:
            candidate_cards = [cards_by_id[purchase.locked_card_id]]
        if not any(
            purchase.amount_cents <= _candidate_capacity(card, purchase, constraints)
            for card in candidate_cards
        ):
            issues.append(
                _issue(
                    IssueCode.PURCHASE_EXCEEDS_CAPACITY,
                    f"Purchase {purchase.id} does not fit on any permitted card.",
                    "Reduce the amount, unlock the purchase, or relax a capacity constraint.",
                    card_ids=[card.id for card in candidate_cards],
                    purchase_ids=[purchase.id],
                    actual=purchase.amount_cents,
                    required=max(
                        (
                            _candidate_capacity(card, purchase, constraints)
                            for card in candidate_cards
                        ),
                        default=0,
                    ),
                )
            )

    total_spend = sum(purchase.amount_cents for purchase in purchases)
    total_credit_capacity = sum(_credit_capacity(card) for card in cards)
    if total_spend > total_credit_capacity:
        issues.append(
            _issue(
                IssueCode.NO_FEASIBLE_ASSIGNMENT,
                "Total planned spend exceeds aggregate available credit.",
                "Reduce planned spend or add synthetic card capacity.",
                card_ids=[card.id for card in cards],
                purchase_ids=[purchase.id for purchase in purchases],
                actual=total_spend,
                required=total_credit_capacity,
            )
        )

    if constraints.max_utilization_bps is not None and constraints.max_utilization_until is None:
        total_ceiling_capacity = sum(
            min(_credit_capacity(card), _utilization_capacity(card, constraints) or 0)
            for card in cards
        )
        if total_spend > total_ceiling_capacity:
            issues.append(
                _issue(
                    IssueCode.UTILIZATION_CEILING_EXCEEDED,
                    "Planned spend exceeds aggregate capacity under the utilization ceiling.",
                    "Raise the ceiling or reduce planned spend.",
                    card_ids=[card.id for card in cards],
                    purchase_ids=[purchase.id for purchase in purchases],
                    actual=total_spend,
                    required=total_ceiling_capacity,
                )
            )

    for card_id in constraints.must_hit_bonus_card_ids:
        card = cards_by_id[card_id]
        bonus = card.signup_bonus
        assert bonus is not None
        remaining = bonus.remaining_spend_cents
        if remaining == 0:
            continue
        eligible = [
            purchase
            for purchase in purchases
            if purchase.date <= bonus.deadline_date
            and purchase.locked_card_id in (None, card_id)
            and purchase.amount_cents <= _candidate_capacity(card, purchase, constraints)
        ]
        eligible_total = sum(purchase.amount_cents for purchase in eligible)
        available = min(_credit_capacity(card), eligible_total)
        if not eligible:
            issues.append(
                _issue(
                    IssueCode.BONUS_DEADLINE_PASSED,
                    f"No planned purchase can count toward the required bonus on {card_id}.",
                    "Remove the hard bonus requirement or add eligible spend before the deadline.",
                    card_ids=[card_id],
                    actual=0,
                    required=remaining,
                )
            )
        elif available < remaining:
            issues.append(
                _issue(
                    IssueCode.BONUS_TARGET_UNREACHABLE,
                    f"The required bonus target on {card_id} is unreachable in this horizon.",
                    "Remove the hard requirement or add eligible spend and card capacity.",
                    card_ids=[card_id],
                    purchase_ids=[purchase.id for purchase in eligible],
                    actual=available,
                    required=remaining,
                )
            )
    return issues


def analyze_assignment(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    assignments: Mapping[str, str],
) -> FeasibilityReport:
    """Validate one complete indivisible assignment under all hard constraints."""

    issues = validate_scenario(cards, purchases, intent)
    invalid_references = {IssueCode.DUPLICATE_ID, IssueCode.UNKNOWN_LOCKED_CARD}
    if any(issue.code in invalid_references for issue in issues):
        return FeasibilityReport(tuple(issues), {}, {}, {})

    cards_by_id = {card.id: card for card in cards}
    purchases_by_id = {purchase.id: purchase for purchase in purchases}
    assigned_spend = {card.id: 0 for card in cards}
    dated_spend = {card.id: 0 for card in cards}

    for purchase in sorted(purchases, key=lambda item: item.id):
        assigned_card_id = assignments.get(purchase.id)
        if assigned_card_id is None:
            issues.append(
                _issue(
                    IssueCode.MISSING_ASSIGNMENT,
                    f"Purchase {purchase.id} has no assigned card.",
                    "Assign every purchase to exactly one card.",
                    purchase_ids=[purchase.id],
                )
            )
            continue
        card = cards_by_id.get(assigned_card_id)
        if card is None:
            issues.append(
                _issue(
                    IssueCode.UNKNOWN_ASSIGNED_CARD,
                    f"Purchase {purchase.id} is assigned to an unknown card.",
                    "Assign the purchase to a card in this portfolio.",
                    card_ids=[assigned_card_id],
                    purchase_ids=[purchase.id],
                )
            )
            continue
        if purchase.locked_card_id is not None and assigned_card_id != purchase.locked_card_id:
            issues.append(
                _issue(
                    IssueCode.PURCHASE_LOCKED_TO_OTHER_CARD,
                    f"Purchase {purchase.id} must remain on {purchase.locked_card_id}.",
                    "Use the locked card or explicitly unlock the purchase.",
                    card_ids=[assigned_card_id, purchase.locked_card_id],
                    purchase_ids=[purchase.id],
                )
            )
        assigned_spend[assigned_card_id] += purchase.amount_cents
        if _ceiling_active_for_purchase(purchase, intent.constraints):
            dated_spend[assigned_card_id] += purchase.amount_cents

    unknown_purchase_ids = sorted(set(assignments) - set(purchases_by_id))
    if unknown_purchase_ids:
        issues.append(
            _issue(
                IssueCode.DUPLICATE_ID,
                "The assignment map contains unknown purchase IDs.",
                "Remove assignment entries that are not in the scenario.",
                purchase_ids=unknown_purchase_ids,
            )
        )

    credit_slack: dict[str, int] = {}
    utilization_slack: dict[str, int | None] = {}
    ceiling_active = _ceiling_active_for_horizon(purchases, intent.constraints)
    for card in sorted(cards, key=lambda item: item.id):
        spend = assigned_spend[card.id]
        capacity = _credit_capacity(card)
        credit_slack[card.id] = max(0, capacity - spend)
        if spend > 0 and card.credit_limit_cents == 0:
            issues.append(
                _issue(
                    IssueCode.ZERO_CREDIT_LIMIT,
                    f"Card {card.id} has no credit capacity.",
                    "Move assigned purchases to another card.",
                    card_ids=[card.id],
                    actual=spend,
                    required=0,
                )
            )
        elif spend > 0 and card.current_balance_cents > card.credit_limit_cents:
            issues.append(
                _issue(
                    IssueCode.CARD_ALREADY_OVER_LIMIT,
                    f"Card {card.id} is already over its credit limit.",
                    "Do not assign new spend to this card.",
                    card_ids=[card.id],
                    actual=card.current_balance_cents,
                    required=card.credit_limit_cents,
                )
            )
        elif spend > capacity:
            issues.append(
                _issue(
                    IssueCode.CREDIT_LIMIT_EXCEEDED,
                    f"Assigned spend exceeds the available credit on {card.id}.",
                    "Move one or more purchases to another card.",
                    card_ids=[card.id],
                    actual=spend,
                    required=capacity,
                )
            )

        if ceiling_active:
            utilization_capacity = _utilization_capacity(card, intent.constraints)
            assert utilization_capacity is not None
            used = dated_spend[card.id]
            utilization_slack[card.id] = max(0, utilization_capacity - used)
            ceiling = intent.constraints.max_utilization_bps
            assert ceiling is not None
            ending_relevant_balance = card.current_balance_cents + used
            if ending_relevant_balance * 10_000 > ceiling * card.credit_limit_cents:
                issues.append(
                    _issue(
                        IssueCode.UTILIZATION_CEILING_EXCEEDED,
                        f"Card {card.id} exceeds the active utilization ceiling.",
                        "Move eligible spend, raise the ceiling, or change the cutoff.",
                        card_ids=[card.id],
                        actual=ending_relevant_balance,
                        required=ceiling * card.credit_limit_cents // 10_000,
                    )
                )
        else:
            utilization_slack[card.id] = None

    for card_id in intent.constraints.must_hit_bonus_card_ids:
        card = cards_by_id.get(card_id)
        if card is None or card.signup_bonus is None:
            continue
        remaining = card.signup_bonus.remaining_spend_cents
        eligible_assigned = sum(
            purchase.amount_cents
            for purchase in purchases
            if assignments.get(purchase.id) == card_id
            and purchase.date <= card.signup_bonus.deadline_date
        )
        if eligible_assigned < remaining:
            issues.append(
                _issue(
                    IssueCode.BONUS_TARGET_UNREACHABLE,
                    f"The assignment does not hit the required bonus on {card_id}.",
                    "Move enough deadline-eligible spend to the required bonus card.",
                    card_ids=[card_id],
                    actual=eligible_assigned,
                    required=remaining,
                )
            )

    return FeasibilityReport(
        issues=tuple(issues),
        assigned_spend_by_card=assigned_spend,
        credit_limit_slack_by_card=credit_slack,
        utilization_slack_by_card=utilization_slack,
    )


def candidate_assignment_issues(
    cards: Sequence[Card],
    purchases: Sequence[Purchase],
    intent: Intent,
    current_assignments: Mapping[str, str],
    purchase: Purchase,
    card: Card,
) -> list[OptimizationIssue]:
    """Check hard card-state constraints for one addition to a partial plan."""

    if purchase.locked_card_id is not None and purchase.locked_card_id != card.id:
        return [
            _issue(
                IssueCode.PURCHASE_LOCKED_TO_OTHER_CARD,
                f"Purchase {purchase.id} must remain on {purchase.locked_card_id}.",
                "Use the locked card or explicitly unlock the purchase.",
                card_ids=[card.id, purchase.locked_card_id],
                purchase_ids=[purchase.id],
            )
        ]

    purchases_by_id = {item.id: item for item in purchases}
    assigned_to_card = [
        purchases_by_id[purchase_id]
        for purchase_id, assigned_card_id in current_assignments.items()
        if assigned_card_id == card.id and purchase_id in purchases_by_id
    ]
    assigned_spend = sum(item.amount_cents for item in assigned_to_card)
    resulting_spend = assigned_spend + purchase.amount_cents
    capacity = _credit_capacity(card)
    if card.credit_limit_cents == 0:
        return [
            _issue(
                IssueCode.ZERO_CREDIT_LIMIT,
                f"Card {card.id} has no credit capacity.",
                "Use another card.",
                card_ids=[card.id],
                purchase_ids=[purchase.id],
                actual=purchase.amount_cents,
                required=0,
            )
        ]
    if card.current_balance_cents > card.credit_limit_cents:
        return [
            _issue(
                IssueCode.CARD_ALREADY_OVER_LIMIT,
                f"Card {card.id} is already over its credit limit.",
                "Use another card or adjust the synthetic current balance.",
                card_ids=[card.id],
                purchase_ids=[purchase.id],
                actual=card.current_balance_cents,
                required=card.credit_limit_cents,
            )
        ]
    if resulting_spend > capacity:
        return [
            _issue(
                IssueCode.CREDIT_LIMIT_EXCEEDED,
                f"Purchase {purchase.id} would exceed available credit on {card.id}.",
                "Use another card or move existing planned spend.",
                card_ids=[card.id],
                purchase_ids=[purchase.id],
                actual=resulting_spend,
                required=capacity,
            )
        ]

    if _ceiling_active_for_purchase(purchase, intent.constraints):
        existing_dated_spend = sum(
            item.amount_cents
            for item in assigned_to_card
            if _ceiling_active_for_purchase(item, intent.constraints)
        )
        resulting_dated_spend = existing_dated_spend + purchase.amount_cents
        utilization_capacity = _utilization_capacity(card, intent.constraints)
        assert utilization_capacity is not None
        if resulting_dated_spend > utilization_capacity:
            return [
                _issue(
                    IssueCode.UTILIZATION_CEILING_EXCEEDED,
                    f"Purchase {purchase.id} would breach the utilization ceiling on {card.id}.",
                    "Use another card, raise the ceiling, or change the cutoff.",
                    card_ids=[card.id],
                    purchase_ids=[purchase.id],
                    actual=resulting_dated_spend,
                    required=utilization_capacity,
                )
            ]
    return []
