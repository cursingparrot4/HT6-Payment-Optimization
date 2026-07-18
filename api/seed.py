"""Demo seed built from the committed data fixtures.

Wallet cards come from the Sarah scenario (``data/scenarios/sarah_august_2026.json``),
whose cards are validated against the sourced product catalog in ``data/cards.json``.
Public product terms are real; every account value (limits, balances, bonus progress,
payments) is synthetic. Recurring purchases in the scenario become SwitchPay's
recurring payments, ordered by priority.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from api.db import fake_token, log_event, new_id, now_iso
from data.loaders import load_scenario

SCENARIO_ID = "sarah-august-2026"

# Deliberately suboptimal initial funding so the demo opens with real switch
# recommendations (e.g. rent should move to the Amex Gold card to complete its
# $500 welcome bonus).
INITIAL_FUNDING = {
    "rent-aug": "scotia-momentum-visa-infinite",
    "insurance": "scotia-momentum-visa-infinite",
    "utilities": "rbc-avion-visa-infinite",
    "internet": "rbc-avion-visa-infinite",
    "transit-pass": "scotia-momentum-visa-infinite",
    "streaming": "scotia-momentum-visa-infinite",
}

PAYMENT_NAMES = {
    "rent-aug": "Rent",
    "insurance": "Insurance",
    "utilities": "Utilities",
    "internet": "Internet",
    "transit-pass": "Transit Pass",
    "streaming": "Streaming",
}


def seed_demo(conn: sqlite3.Connection, today: date) -> dict[str, list[str]]:
    """Reset the database to the catalog-backed Sarah scenario."""

    scenario = load_scenario(SCENARIO_ID)
    scenario = scenario.scenario if hasattr(scenario, "scenario") else scenario

    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM cards")

    card_ids: list[str] = []
    for index, card in enumerate(scenario.cards):
        bonus = card.signup_bonus
        conn.execute(
            "INSERT INTO cards (id, name, token, reward_type, reward_rate_bps,"
            " point_value_millicents, credit_limit_cents, current_balance_cents,"
            " bonus_target_cents, bonus_progress_cents, bonus_value_cents, bonus_deadline,"
            " expiry_date, status, ineligible_categories, recent_failures, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                card.id,
                card.name,
                fake_token(),
                "cashback" if card.base_reward_type.value == "cashback" else "points",
                card.base_rate_bps,
                card.point_value_millicents,
                card.credit_limit_cents,
                card.current_balance_cents,
                bonus.spend_required_cents if bonus else None,
                bonus.spend_so_far_cents if bonus else None,
                bonus.reward_value_cents if bonus else None,
                bonus.deadline_date.isoformat() if bonus else None,
                # Expiry is synthetic account state; stagger it well past the demo.
                (today + timedelta(days=540 + 180 * index)).isoformat(),
                "active",
                "",
                now_iso(),
            ),
        )
        card_ids.append(card.id)

    recurring = [p for p in scenario.purchases if p.is_recurring]
    recurring.sort(key=lambda p: -p.amount_cents)  # priority: biggest bills first
    payment_ids: list[str] = []
    for rank, purchase in enumerate(recurring):
        payment_id = new_id("pay")
        conn.execute(
            "INSERT INTO payments (id, name, category, amount_cents, due_date, frequency,"
            " processing_fee_bps, funding_card_id, priority_rank, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payment_id,
                PAYMENT_NAMES.get(purchase.id, purchase.id.replace("-", " ").title()),
                purchase.category,
                purchase.amount_cents,
                purchase.date.isoformat(),
                "monthly",
                100 if purchase.id == "rent-aug" else 0,  # card-rent processors charge a fee
                INITIAL_FUNDING.get(purchase.id, card_ids[0]),
                rank,
                now_iso(),
            ),
        )
        payment_ids.append(payment_id)

    log_event(
        conn,
        "seed",
        f"Demo loaded from scenario {SCENARIO_ID}: {len(card_ids)} catalog-backed cards "
        f"and {len(payment_ids)} recurring payments. Product terms are public issuer "
        "data; all balances, limits, and payments are synthetic.",
    )
    conn.commit()
    return {"cards": card_ids, "payments": payment_ids}
