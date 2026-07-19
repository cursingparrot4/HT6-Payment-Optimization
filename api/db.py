"""SQLite persistence for CardIQ's synthetic cards, payments, and transactions.

All monetary values are integer cents. All card records carry a fake token
(``synthetic_tok_*``) instead of a PAN; no real credentials exist anywhere.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cardiq.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token TEXT NOT NULL,
    reward_type TEXT NOT NULL CHECK (reward_type IN ('cashback', 'points')),
    reward_rate_bps INTEGER NOT NULL,
    point_value_millicents INTEGER NOT NULL DEFAULT 1000,
    credit_limit_cents INTEGER NOT NULL,
    current_balance_cents INTEGER NOT NULL,
    bonus_target_cents INTEGER,
    bonus_progress_cents INTEGER,
    bonus_value_cents INTEGER,
    bonus_deadline TEXT,
    expiry_date TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'locked')),
    ineligible_categories TEXT NOT NULL DEFAULT '',
    recent_failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    frequency TEXT NOT NULL,
    processing_fee_bps INTEGER NOT NULL DEFAULT 0,
    funding_card_id TEXT REFERENCES cards(id) ON DELETE SET NULL,
    backup_card_id TEXT REFERENCES cards(id) ON DELETE SET NULL,
    priority_rank INTEGER NOT NULL DEFAULT 0,
    last_result TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    card_id TEXT NOT NULL,
    card_name TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    fee_cents INTEGER NOT NULL,
    state TEXT NOT NULL,
    scenario TEXT NOT NULL,
    step_index INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT,
    payment_id TEXT,
    kind TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    message TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def fake_token() -> str:
    return f"synthetic_tok_{uuid.uuid4().hex[:12]}"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    payment_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(payments)").fetchall()
    }
    if "priority_rank" not in payment_columns:
        conn.execute(
            "ALTER TABLE payments ADD COLUMN priority_rank INTEGER NOT NULL DEFAULT 0"
        )
        rows = conn.execute(
            "SELECT id FROM payments ORDER BY due_date, created_at, id"
        ).fetchall()
        for index, row in enumerate(rows):
            conn.execute(
                "UPDATE payments SET priority_rank = ? WHERE id = ?",
                (index, row["id"]),
            )


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def log_event(
    conn: sqlite3.Connection,
    kind: str,
    message: str,
    *,
    transaction_id: str | None = None,
    payment_id: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events (transaction_id, payment_id, kind, from_state, to_state,"
        " message, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            transaction_id,
            payment_id,
            kind,
            from_state,
            to_state,
            message,
            json.dumps(detail) if detail else None,
            now_iso(),
        ),
    )
