"""Deterministic SwitchPay card-selection layer.

Ranks a user's synthetic cards for one recurring payment. All reward, bonus,
utilization, and headroom arithmetic is delegated to the tested integer-cent
functions in ``engine.scoring``; this module adds the SwitchPay-specific hard
eligibility rules (locked, expired, insufficient credit, category exclusions),
processing fees, payment-failure history, and templated explanations.

Score calibration (documented, deterministic):
- rewards, fees, and welcome-bonus utility are integer cents
- 10 engine utilization-penalty points == 1 cent of score penalty
- 100 engine risk-headroom points == 1 cent of score penalty
- each recent payment failure on a card costs 500 cents of score
No language model participates in any calculation or decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from engine.models import Card as EngineCard
from engine.models import Purchase as EnginePurchase
from engine.models import RewardType, SignupBonus
from engine.scoring import (
    incremental_risk_penalty_points,
    incremental_utilization_penalty_points,
    reward_values,
    signup_bonus_factors,
    utilization_after,
)

UTIL_POINTS_PER_CENT = 10
RISK_POINTS_PER_CENT = 100
FAILURE_PENALTY_CENTS = 500
HEALTHY_UTILIZATION_BPS = 3_000


def _dollars(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def _pct(bps: int) -> str:
    return f"{bps / 100:.0f}%"


def _to_engine_card(card: dict[str, Any]) -> EngineCard:
    """Project a SwitchPay card row onto the engine's Card contract."""

    return EngineCard(
        id=card["id"],
        name=card["name"],
        credit_limit_cents=card["credit_limit_cents"],
        current_balance_cents=min(card["current_balance_cents"], card["credit_limit_cents"]),
        base_rate_bps=card["reward_rate_bps"],
        base_reward_type=(
            RewardType.CASHBACK if card["reward_type"] == "cashback" else RewardType.POINTS
        ),
        point_value_millicents=card["point_value_millicents"],
        annual_fee_cents=0,
        statement_day=1,
        due_day=15,
        signup_bonus=_to_engine_bonus(card),
    )


def _to_engine_bonus(card: dict[str, Any]) -> SignupBonus | None:
    if not card.get("bonus_target_cents") or not card.get("bonus_deadline"):
        return None
    return SignupBonus(
        spend_required_cents=card["bonus_target_cents"],
        spend_so_far_cents=min(card.get("bonus_progress_cents") or 0, card["bonus_target_cents"]),
        reward_value_cents=card.get("bonus_value_cents") or 0,
        deadline_date=date.fromisoformat(card["bonus_deadline"]),
    )


@dataclass
class CardEvaluation:
    card_id: str
    card_name: str
    eligible: bool
    exclusion_reasons: list[str] = field(default_factory=list)
    rank: int | None = None
    reward_cents: int = 0
    fee_cents: int = 0
    net_reward_cents: int = 0
    bonus_score_cents: int = 0
    bonus_completes: bool = False
    bonus_remaining_before_cents: int = 0
    utilization_before_bps: int = 0
    utilization_after_bps: int = 0
    utilization_penalty_cents: int = 0
    risk_penalty_cents: int = 0
    failure_penalty_cents: int = 0
    available_credit_cents: int = 0
    score_cents: int = 0
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def evaluate_card(
    card: dict[str, Any],
    payment: dict[str, Any],
    on_date: date,
    *,
    exclude_card_ids: set[str] | None = None,
) -> CardEvaluation:
    amount = payment["amount_cents"]
    available = card["credit_limit_cents"] - card["current_balance_cents"]
    evaluation = CardEvaluation(
        card_id=card["id"],
        card_name=card["name"],
        eligible=True,
        available_credit_cents=available,
    )

    reasons: list[str] = []
    if exclude_card_ids and card["id"] in exclude_card_ids:
        reasons.append("Card was declined for this payment and is excluded from this attempt.")
    if card["status"] == "locked":
        reasons.append("Card is locked.")
    if card.get("expiry_date") and date.fromisoformat(card["expiry_date"]) < on_date:
        reasons.append(f"Card expired on {card['expiry_date']}.")
    if available < amount:
        reasons.append(
            f"Insufficient available credit: {_dollars(available)} available, "
            f"{_dollars(amount)} required."
        )
    ineligible = {
        c.strip() for c in (card.get("ineligible_categories") or "").split(",") if c.strip()
    }
    if payment["category"] in ineligible:
        reasons.append(f"Card does not support the {payment['category']} payment category.")

    if reasons:
        evaluation.eligible = False
        evaluation.exclusion_reasons = reasons
        return evaluation

    engine_card = _to_engine_card(card)
    purchase = EnginePurchase(
        id=f"pmt-{payment['id']}",
        amount_cents=amount,
        category=payment["category"],
        date=on_date,
        is_recurring=True,
    )

    rewards = reward_values(engine_card, purchase)
    evaluation.reward_cents = rewards.cashback_cents + rewards.travel_value_cents
    evaluation.fee_cents = amount * payment["processing_fee_bps"] // 10_000
    evaluation.net_reward_cents = evaluation.reward_cents - evaluation.fee_cents

    bonus = signup_bonus_factors(engine_card, (purchase,))
    engine_bonus = engine_card.signup_bonus
    evaluation.bonus_remaining_before_cents = (
        engine_bonus.remaining_spend_cents if engine_bonus else 0
    )
    evaluation.bonus_score_cents = bonus.goal_points
    evaluation.bonus_completes = (
        bool(bonus.bonus_hit) and evaluation.bonus_remaining_before_cents > 0
    )

    evaluation.utilization_before_bps = utilization_after(engine_card, 0)
    evaluation.utilization_after_bps = utilization_after(engine_card, amount)
    evaluation.utilization_penalty_cents = (
        incremental_utilization_penalty_points(engine_card, amount) // UTIL_POINTS_PER_CENT
    )
    evaluation.risk_penalty_cents = (
        incremental_risk_penalty_points(engine_card, amount) // RISK_POINTS_PER_CENT
    )
    evaluation.failure_penalty_cents = card["recent_failures"] * FAILURE_PENALTY_CENTS

    evaluation.score_cents = (
        evaluation.net_reward_cents
        + evaluation.bonus_score_cents
        - evaluation.utilization_penalty_cents
        - evaluation.risk_penalty_cents
        - evaluation.failure_penalty_cents
    )
    return evaluation


def _winner_reasons(winner: CardEvaluation, others: list[CardEvaluation]) -> list[str]:
    reasons: list[str] = []
    if winner.bonus_completes:
        remaining = _dollars(winner.bonus_remaining_before_cents)
        reasons.append(
            f"This payment completes the welcome bonus ({remaining} of qualifying spend "
            f"remained), unlocking {_dollars(winner.bonus_score_cents)} of value."
        )
    elif winner.bonus_score_cents > 0:
        reasons.append(
            f"This payment advances the welcome bonus, contributing "
            f"{_dollars(winner.bonus_score_cents)} of progress value."
        )
    reasons.append(
        f"Net recurring value of {_dollars(winner.net_reward_cents)} per cycle "
        f"({_dollars(winner.reward_cents)} rewards minus {_dollars(winner.fee_cents)} fees)."
    )
    if winner.utilization_after_bps <= HEALTHY_UTILIZATION_BPS:
        reasons.append(
            f"Utilization stays healthy at {_pct(winner.utilization_after_bps)} after the charge."
        )
    else:
        reasons.append(
            f"Utilization rises to {_pct(winner.utilization_after_bps)} after the charge "
            f"(scored as a {_dollars(winner.utilization_penalty_cents)} penalty), but the card "
            "still wins on total value."
        )
    best_other = max((o.score_cents for o in others), default=None)
    if best_other is not None:
        reasons.append(
            f"Overall score {_dollars(winner.score_cents)} beats the next-best card by "
            f"{_dollars(winner.score_cents - best_other)}."
        )
    return reasons


def _loss_reason(winner: CardEvaluation, loser: CardEvaluation) -> str:
    gap = winner.score_cents - loser.score_cents
    parts: list[str] = []
    reward_gap = winner.net_reward_cents - loser.net_reward_cents
    bonus_gap = winner.bonus_score_cents - loser.bonus_score_cents
    penalty_gap = (
        (loser.utilization_penalty_cents + loser.risk_penalty_cents + loser.failure_penalty_cents)
        - (
            winner.utilization_penalty_cents
            + winner.risk_penalty_cents
            + winner.failure_penalty_cents
        )
    )
    if bonus_gap > 0:
        parts.append(f"misses {_dollars(bonus_gap)} of welcome-bonus value")
    if reward_gap > 0:
        parts.append(f"earns {_dollars(reward_gap)} less in net rewards per cycle")
    if penalty_gap > 0:
        parts.append(
            f"carries {_dollars(penalty_gap)} more in utilization, headroom, or "
            "reliability penalties"
        )
    if loser.failure_penalty_cents > 0:
        failure_count = loser.failure_penalty_cents // FAILURE_PENALTY_CENTS
        parts.append(f"has {failure_count} recent failed charge(s)")
    if not parts:
        parts.append("scores marginally lower on the same factors")
    return f"Trails by {_dollars(gap)}: " + "; ".join(parts) + "."


def _change_conditions(
    ranked: list[CardEvaluation],
    excluded: list[CardEvaluation],
    payment: dict[str, Any],
    cards_by_id: dict[str, dict[str, Any]],
    on_date: date,
) -> list[str]:
    conditions: list[str] = []
    if not ranked:
        return conditions
    winner = ranked[0]

    if winner.bonus_completes and len(ranked) > 1:
        future_cards = []
        for evaluation in ranked:
            card = dict(cards_by_id[evaluation.card_id])
            if card["id"] == winner.card_id and card.get("bonus_target_cents"):
                card["bonus_progress_cents"] = card["bonus_target_cents"]
            future_cards.append(card)
        future = rank_cards(future_cards, payment, on_date)["ranked"]
        if future and future[0]["card_id"] != winner.card_id:
            next_name = future[0]["card_name"]
            delta = future[0]["score_cents"] - next(
                e["score_cents"] for e in future if e["card_id"] == winner.card_id
            )
            conditions.append(
                f"Once the welcome bonus is complete, {next_name} is projected to take over "
                f"as the best card ({_dollars(delta)} more value per cycle)."
            )
    if len(ranked) > 1:
        runner = ranked[1]
        gap = _dollars(winner.score_cents - runner.score_cents)
        conditions.append(
            f"{runner.card_name} would take the top spot if {winner.card_name}'s score "
            f"dropped by more than {gap} — for example through a declined charge, a lock, "
            "or higher utilization."
        )
        conditions.append(
            f"{winner.card_name} would become ineligible if its available credit fell below "
            f"{_dollars(payment['amount_cents'])}."
        )
    for evaluation in excluded:
        if any("Insufficient available credit" in r for r in evaluation.exclusion_reasons):
            conditions.append(
                f"{evaluation.card_name} would re-enter the ranking if its available credit "
                f"rose to at least {_dollars(payment['amount_cents'])}."
            )
    return conditions


def rank_cards(
    cards: list[dict[str, Any]],
    payment: dict[str, Any],
    on_date: date,
    *,
    exclude_card_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Deterministically rank every card for one payment and explain the outcome."""

    evaluations = [
        evaluate_card(card, payment, on_date, exclude_card_ids=exclude_card_ids)
        for card in cards
    ]
    eligible = [e for e in evaluations if e.eligible]
    excluded = [e for e in evaluations if not e.eligible]
    eligible.sort(key=lambda e: (-e.score_cents, e.card_id))
    for index, evaluation in enumerate(eligible, start=1):
        evaluation.rank = index

    cards_by_id = {card["id"]: card for card in cards}
    primary = eligible[0] if eligible else None
    backup = eligible[1] if len(eligible) > 1 else None

    winner_reasons = _winner_reasons(primary, eligible[1:]) if primary else []
    rejected = {e.card_id: _loss_reason(primary, e) for e in eligible[1:]} if primary else {}
    for evaluation in excluded:
        rejected[evaluation.card_id] = " ".join(evaluation.exclusion_reasons)

    return {
        "payment_id": payment["id"],
        "evaluated_on": on_date.isoformat(),
        "ranked": [e.as_dict() for e in eligible],
        "excluded": [e.as_dict() for e in excluded],
        "primary_card_id": primary.card_id if primary else None,
        "backup_card_id": backup.card_id if backup else None,
        "winner_reasons": winner_reasons,
        "rejected_reasons": rejected,
        "change_conditions": _change_conditions(
            eligible, excluded, payment, cards_by_id, on_date
        ),
    }


def _apply_projected_payment(card: dict[str, Any], payment: dict[str, Any], on_date: date) -> None:
    fee_cents = payment["amount_cents"] * payment["processing_fee_bps"] // 10_000
    card["current_balance_cents"] += payment["amount_cents"] + fee_cents

    bonus_active = card.get("bonus_target_cents") and card.get("bonus_deadline")
    if bonus_active and on_date <= date.fromisoformat(card["bonus_deadline"]):
        card["bonus_progress_cents"] = min(
            card["bonus_target_cents"],
            (card.get("bonus_progress_cents") or 0) + payment["amount_cents"],
        )


def build_priority_plan(
    cards: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    today: date,
) -> dict[str, dict[str, Any]]:
    """Route payments in priority order and compare each route to its independent best.

    Earlier payments reserve credit headroom and advance welcome-bonus progress before lower
    priority payments are evaluated. The weighted loss shows how far the priority-aware route is
    from the independent per-payment optimum.
    """

    total = len(payments)
    projected_cards = [dict(card) for card in cards]
    original_cards = [dict(card) for card in cards]
    plan: dict[str, dict[str, Any]] = {}

    for index, payment in enumerate(payments):
        on_date = max(date.fromisoformat(payment["due_date"]), today)
        weight = total - index
        priority_ranking = rank_cards(projected_cards, payment, on_date)
        independent_ranking = rank_cards(original_cards, payment, on_date)
        priority_choice = priority_ranking["ranked"][0] if priority_ranking["ranked"] else None
        independent_choice = (
            independent_ranking["ranked"][0] if independent_ranking["ranked"] else None
        )

        priority_score = priority_choice["score_cents"] if priority_choice else None
        independent_score = independent_choice["score_cents"] if independent_choice else None
        weighted_priority = priority_score * weight if priority_score is not None else None
        weighted_optimal = independent_score * weight if independent_score is not None else None
        off_optimal = (
            max(0, weighted_optimal - weighted_priority)
            if weighted_priority is not None and weighted_optimal is not None
            else None
        )

        if priority_choice is None:
            status = "infeasible"
            reason = (
                "No eligible card can fund this payment after higher-priority bills "
                "reserve capacity."
            )
        elif off_optimal and off_optimal > 0:
            status = "off_optimal"
            if independent_choice and independent_choice["card_id"] != priority_choice["card_id"]:
                reason = (
                    f"Priority order routes this to {priority_choice['card_name']} after "
                    f"{independent_choice['card_name']} is reserved or weakened by earlier bills."
                )
            else:
                reason = (
                    f"Earlier bills reduce {priority_choice['card_name']}'s remaining headroom "
                    "or bonus value before this payment is scored."
                )
        else:
            status = "optimal"
            reason = "The priority-aware route matches the independent best decision."

        plan[payment["id"]] = {
            "priority_rank": index,
            "priority_weight": weight,
            "priority_card_id": priority_choice["card_id"] if priority_choice else None,
            "priority_card_name": priority_choice["card_name"] if priority_choice else None,
            "priority_score_cents": priority_score,
            "weighted_priority_score_cents": weighted_priority,
            "independent_best_card_id": (
                independent_choice["card_id"] if independent_choice else None
            ),
            "independent_best_card_name": (
                independent_choice["card_name"] if independent_choice else None
            ),
            "independent_best_score_cents": independent_score,
            "optimal_priority_score_cents": weighted_optimal,
            "off_optimal_cents": off_optimal,
            "priority_status": status,
            "priority_reason": reason,
        }

        if priority_choice is not None:
            for projected_card in projected_cards:
                if projected_card["id"] == priority_choice["card_id"]:
                    _apply_projected_payment(projected_card, payment, on_date)
                    break

    return plan


def build_switch_recommendation(
    ranking: dict[str, Any],
    payment: dict[str, Any],
) -> dict[str, Any] | None:
    """Compare the current funding card against the ranked winner."""

    primary_id = ranking["primary_card_id"]
    if primary_id is None:
        return None
    current_id = payment.get("funding_card_id")
    if current_id == primary_id:
        return None

    ranked = {e["card_id"]: e for e in ranking["ranked"]}
    excluded = {e["card_id"]: e for e in ranking["excluded"]}
    winner = ranked[primary_id]
    current = ranked.get(current_id) or excluded.get(current_id)

    reasons: list[str] = []
    risks: list[str] = []
    delta = None
    if current is None:
        headline = (
            f"Assign {winner['card_name']} to fund {payment['name']} — no funding card is set."
        )
    elif current.get("eligible"):
        delta = winner["score_cents"] - current["score_cents"]
        headline = (
            f"Switch from {current['card_name']} to {winner['card_name']} before your next "
            f"{payment['name'].lower()} payment for {_dollars(delta)} more value per cycle."
        )
        if winner["bonus_completes"]:
            reasons.append(
                f"{winner['card_name']} completes its welcome bonus with this payment, worth "
                f"{_dollars(winner['bonus_score_cents'])}."
            )
        reward_gap = winner["net_reward_cents"] - current["net_reward_cents"]
        if reward_gap > 0:
            reasons.append(
                f"{winner['card_name']} nets {_dollars(reward_gap)} more per cycle in rewards "
                "after fees."
            )
        if current.get("bonus_remaining_before_cents") == 0 and not winner["bonus_completes"]:
            reasons.append(
                f"{current['card_name']} has no welcome bonus left to earn, so its edge is gone."
            )
        if winner["utilization_after_bps"] <= HEALTHY_UTILIZATION_BPS:
            reasons.append(
                f"Utilization on {winner['card_name']} stays at "
                f"{_pct(winner['utilization_after_bps'])} — below the 30% health threshold."
            )
    else:
        headline = (
            f"{current['card_name']} can no longer fund {payment['name']} — switch to "
            f"{winner['card_name']}."
        )
        reasons.extend(current["exclusion_reasons"])

    if winner["utilization_after_bps"] > HEALTHY_UTILIZATION_BPS:
        risks.append(
            f"This charge pushes {winner['card_name']} to "
            f"{_pct(winner['utilization_after_bps'])} utilization."
        )
    if winner["available_credit_cents"] - payment["amount_cents"] < payment["amount_cents"]:
        risks.append(
            f"After this charge, {winner['card_name']} would not have room for a second payment "
            "of the same size until a balance payment posts."
        )
    if not risks:
        risks.append(
            "No material risks identified: limits, expiry, and utilization all stay healthy."
        )

    return {
        "from_card_id": current_id,
        "from_card_name": current["card_name"] if current else None,
        "to_card_id": primary_id,
        "to_card_name": winner["card_name"],
        "delta_cents": delta,
        "headline": headline,
        "reasons": reasons,
        "risks": risks,
    }
