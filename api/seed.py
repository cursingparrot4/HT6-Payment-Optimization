"""Synthetic demo scenario for SwitchPay. All people, cards, and values are fake."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from api.db import fake_token, log_event, new_id, now_iso


def seed_demo(conn: sqlite3.Connection, today: date) -> dict[str, list[str]]:
    """Reset the database to the canonical Card A/B/C rent scenario."""

    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM cards")

    cards = [
        {
            "id": "card_aeroplan",
            "name": "Aeroplan Voyager (synthetic)",
            "reward_type": "points",
            "reward_rate_bps": 100,  # 1 point per dollar
            "point_value_millicents": 1_000,  # 1 point = 1 cent
            "credit_limit_cents": 600_000,
            "current_balance_cents": 200_000,
            "bonus_target_cents": 300_000,
            "bonus_progress_cents": 120_000,  # $1,800 remaining
            "bonus_value_cents": 60_000,  # 60,000 points at 1c/pt = $600
            "bonus_deadline": (today + timedelta(days=12)).isoformat(),
            "expiry_date": (today + timedelta(days=900)).isoformat(),
            "status": "active",
            "ineligible_categories": "",
        },
        {
            "id": "card_cascade",
            "name": "Cascade Cashback (synthetic)",
            "reward_type": "cashback",
            "reward_rate_bps": 200,  # 2% cashback
            "point_value_millicents": 1_000,
            "credit_limit_cents": 1_000_000,
            "current_balance_cents": 50_000,
            "bonus_target_cents": None,
            "bonus_progress_cents": None,
            "bonus_value_cents": None,
            "bonus_deadline": None,
            "expiry_date": (today + timedelta(days=700)).isoformat(),
            "status": "active",
            "ineligible_categories": "",
        },
        {
            "id": "card_maple",
            "name": "Maple Lite Cashback (synthetic)",
            "reward_type": "cashback",
            "reward_rate_bps": 150,  # 1.5% cashback
            "point_value_millicents": 1_000,
            "credit_limit_cents": 600_000,
            "current_balance_cents": 120_000,
            "bonus_target_cents": None,
            "bonus_progress_cents": None,
            "bonus_value_cents": None,
            "bonus_deadline": None,
            "expiry_date": (today + timedelta(days=400)).isoformat(),
            "status": "active",
            "ineligible_categories": "",
        },
    ]
    for card in cards:
        conn.execute(
            "INSERT INTO cards (id, name, token, reward_type, reward_rate_bps,"
            " point_value_millicents, credit_limit_cents, current_balance_cents,"
            " bonus_target_cents, bonus_progress_cents, bonus_value_cents, bonus_deadline,"
            " expiry_date, status, ineligible_categories, recent_failures, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                card["id"],
                card["name"],
                fake_token(),
                card["reward_type"],
                card["reward_rate_bps"],
                card["point_value_millicents"],
                card["credit_limit_cents"],
                card["current_balance_cents"],
                card["bonus_target_cents"],
                card["bonus_progress_cents"],
                card["bonus_value_cents"],
                card["bonus_deadline"],
                card["expiry_date"],
                card["status"],
                card["ineligible_categories"],
                now_iso(),
            ),
        )

    payments = [
        {
            "id": new_id("pay"),
            "name": "Rent",
            "category": "rent",
            "amount_cents": 240_000,
            "due_date": (today + timedelta(days=5)).isoformat(),
            "frequency": "monthly",
            "processing_fee_bps": 0,
            "funding_card_id": "card_cascade",
            "priority_rank": 0,
        },
        {
            "id": new_id("pay"),
            "name": "Car Insurance",
            "category": "insurance",
            "amount_cents": 32_000,
            "due_date": (today + timedelta(days=9)).isoformat(),
            "frequency": "monthly",
            "processing_fee_bps": 100,
            "funding_card_id": "card_maple",
            "priority_rank": 1,
        },
        {
            "id": new_id("pay"),
            "name": "Hydro & Utilities",
            "category": "utilities",
            "amount_cents": 18_500,
            "due_date": (today + timedelta(days=14)).isoformat(),
            "frequency": "monthly",
            "processing_fee_bps": 0,
            "funding_card_id": "card_cascade",
            "priority_rank": 2,
        },
    ]
    for payment in payments:
        conn.execute(
            "INSERT INTO payments (id, name, category, amount_cents, due_date, frequency,"
            " processing_fee_bps, funding_card_id, priority_rank, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payment["id"],
                payment["name"],
                payment["category"],
                payment["amount_cents"],
                payment["due_date"],
                payment["frequency"],
                payment["processing_fee_bps"],
                payment["funding_card_id"],
                payment["priority_rank"],
                now_iso(),
            ),
        )

    log_event(
        conn,
        "seed",
        "Demo scenario loaded: 3 synthetic cards and 3 recurring payments. No real "
        "accounts, credentials, or money are involved.",
    )
    conn.commit()
    return {
        "cards": [c["id"] for c in cards],
        "payments": [p["id"] for p in payments],
    }
