"""SwitchPay HTTP API.

FastAPI application exposing synthetic cards, recurring payments, the
deterministic switch recommendation, and the simulated payment lifecycle.
Run with: .venv/bin/uvicorn api.main:app --port 8000
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api import state_machine as sm
from api.db import connect, fake_token, log_event, new_id, now_iso, rows_to_dicts
from api.recommender import (
    build_priority_plan,
    build_switch_recommendation,
    evaluate_card,
    rank_cards,
)
from api.seed import seed_demo
from data.loaders import DataLoadError, load_product_catalog
from engine.config import DEFAULT_ENGINE_CONFIG, engine_config_hash
from engine.models import (
    Card as EngineCard,
)
from engine.models import (
    Intent as EngineIntent,
)
from engine.models import (
    Purchase as EnginePurchase,
)
from engine.models import (
    Scenario as EngineScenario,
)
from engine.models import (
    SolverMethod,
)
from engine.optimize import (
    allocate_month,
    recommend_purchase,
    run_what_if,
    sample_frontier,
)
from explain import (
    explain_allocation,
    explain_frontier,
    explain_recommendation,
    explain_what_if,
)
from intent import build_intent_card_context, parse_intent
from intent.providers import FixtureIntentProvider, IntentProvider


def create_app() -> FastAPI:
    """Return the SwitchPay FastAPI app.

    Route handlers are module-level for the hackathon demo, but the exported factory gives
    tests and future deployment code a stable construction point.
    """

    return app


app = FastAPI(title="SwitchPay API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SIMULATOR_SCENARIOS = sorted(sm.SCENARIOS)


# ---------------------------------------------------------------- request models


class CardBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    reward_type: Literal["cashback", "points"]
    reward_rate_bps: int = Field(ge=0, le=10_000)
    point_value_millicents: int = Field(default=1_000, ge=0)
    credit_limit_cents: int = Field(ge=0)
    current_balance_cents: int = Field(ge=0)
    bonus_target_cents: int | None = Field(default=None, ge=1)
    bonus_progress_cents: int | None = Field(default=None, ge=0)
    bonus_value_cents: int | None = Field(default=None, ge=0)
    bonus_deadline: date | None = None
    expiry_date: date | None = None
    status: Literal["active", "locked"] = "active"
    ineligible_categories: str = ""


class PaymentBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(gt=0)
    due_date: date
    frequency: Literal["monthly", "weekly", "biweekly", "yearly", "once"] = "monthly"
    processing_fee_bps: int = Field(default=0, ge=0, le=10_000)
    funding_card_id: str | None = None


class PriorityBody(BaseModel):
    payment_ids: list[str] = Field(max_length=60)


class SwitchBody(BaseModel):
    to_card_id: str


class PayBody(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=80)
    scenario: str = "success"


class VerifyBody(BaseModel):
    confirmed: bool


class ApiWarning(BaseModel):
    code: str
    message: str


class ApiResponse(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    data: dict[str, Any]
    warnings: list[ApiWarning] = Field(default_factory=list)


class RecommendBody(BaseModel):
    cards: list[EngineCard] = Field(min_length=1, max_length=8)
    purchase: EnginePurchase
    intent: EngineIntent


class AllocateBody(BaseModel):
    cards: list[EngineCard] = Field(min_length=1, max_length=8)
    purchases: list[EnginePurchase] = Field(min_length=1, max_length=60)
    intent: EngineIntent
    solver_preference: Literal["greedy", "ilp"] = "greedy"


class ParseIntentBody(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    cards: list[EngineCard] = Field(min_length=1, max_length=8)
    reference_date: date | None = None


class FrontierBody(BaseModel):
    cards: list[EngineCard] = Field(min_length=1, max_length=8)
    purchases: list[EnginePurchase] = Field(min_length=1, max_length=60)
    intent: EngineIntent
    solver_preference: Literal["greedy", "ilp"] = "ilp"
    max_points: int = Field(default=5, ge=1, le=5)


class WhatIfBody(BaseModel):
    cards: list[EngineCard] = Field(min_length=1, max_length=8)
    purchases: list[EnginePurchase] = Field(min_length=1, max_length=60)
    intent: EngineIntent
    purchase_id: str = Field(min_length=1)
    override_card_id: str = Field(min_length=1)
    solver_preference: Literal["greedy", "ilp"] = "ilp"


# The live money path never trusts a language model. With no Freesolo/general-model
# endpoint configured, the default provider is deliberately unavailable so
# ``parse_intent`` returns its visibly-labeled equal-weight fallback (PLAN §7/§8d).
# Tests and future deployment code swap in a real provider by reassigning this.
_INTENT_PROVIDER: IntentProvider = FixtureIntentProvider(responses={})


def _intent_provider() -> IntentProvider:
    return _INTENT_PROVIDER


def _solver_method(preference: str) -> SolverMethod:
    return SolverMethod.ILP if preference == "ilp" else SolverMethod.GREEDY


def _api_response(data: dict[str, Any], warnings: list[ApiWarning] | None = None) -> ApiResponse:
    return ApiResponse(data=data, warnings=warnings or [])


def _engine_config_meta() -> dict[str, Any]:
    return {
        "version": DEFAULT_ENGINE_CONFIG.config_version,
        "hash": engine_config_hash(DEFAULT_ENGINE_CONFIG),
    }


# ---------------------------------------------------------------- helpers


def _get_card(conn: sqlite3.Connection, card_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Card {card_id} not found")
    return dict(row)


def _get_payment(conn: sqlite3.Connection, payment_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Payment {payment_id} not found")
    return dict(row)


def _all_cards(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute("SELECT * FROM cards ORDER BY created_at").fetchall())


def _ordered_payments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            "SELECT * FROM payments ORDER BY priority_rank, due_date, created_at, id"
        ).fetchall()
    )


def _compact_payment_priorities(conn: sqlite3.Connection) -> None:
    for index, payment in enumerate(_ordered_payments(conn)):
        conn.execute(
            "UPDATE payments SET priority_rank = ? WHERE id = ?",
            (index, payment["id"]),
        )


def _payment_with_names(conn: sqlite3.Connection, payment: dict[str, Any]) -> dict[str, Any]:
    names = {c["id"]: c["name"] for c in _all_cards(conn)}
    payment = dict(payment)
    payment["funding_card_name"] = names.get(payment.get("funding_card_id"))
    payment["backup_card_name"] = names.get(payment.get("backup_card_id"))
    return payment


def _engine_card_from_row(card: dict[str, Any]) -> EngineCard:
    from api.recommender import _to_engine_card

    return _to_engine_card(card)


def _engine_purchase_from_payment(payment: dict[str, Any]) -> EnginePurchase:
    return EnginePurchase(
        id=payment["id"],
        amount_cents=payment["amount_cents"],
        category=payment["category"],
        date=date.fromisoformat(payment["due_date"]),
        is_recurring=payment["frequency"] != "once",
        locked_card_id=payment.get("funding_card_id"),
    )


def _default_engine_intent() -> EngineIntent:
    return EngineIntent(
        weights={
            "max_cashback": 0.2,
            "max_travel": 0.2,
            "credit_health": 0.2,
            "hit_signup_bonus": 0.25,
            "max_cashflow": 0.05,
            "min_risk": 0.1,
        }
    )


def _recommendation_for(
    conn: sqlite3.Connection,
    payment: dict[str, Any],
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    cards = _all_cards(conn)
    on_date = max(date.fromisoformat(payment["due_date"]), date.today())
    ranking = rank_cards(cards, payment, on_date, exclude_card_ids=exclude)
    ranking["switch"] = build_switch_recommendation(ranking, payment)
    ranking["payment"] = _payment_with_names(conn, payment)
    return ranking


def _get_script(scenario: str) -> list[sm.Step]:
    if scenario in sm.SCENARIOS:
        return sm.SCENARIOS[scenario]
    if scenario == "verified_success":
        return sm.verification_steps(confirmed=True)
    if scenario == "verified_failed":
        return sm.verification_steps(confirmed=False)
    raise HTTPException(400, f"Unknown scenario {scenario}")


def _txn_response(conn: sqlite3.Connection, txn_id: str) -> dict[str, Any]:
    txn = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if txn is None:
        raise HTTPException(404, f"Transaction {txn_id} not found")
    events = rows_to_dicts(
        conn.execute(
            "SELECT * FROM events WHERE transaction_id = ? ORDER BY id", (txn_id,)
        ).fetchall()
    )
    result = dict(txn)
    result["events"] = events
    result["is_terminal"] = result["state"] in sm.TERMINAL_STATES
    return result


def _apply_settlement(conn: sqlite3.Connection, txn: dict[str, Any]) -> None:
    """Post the synthetic charge to the card once the recipient is paid."""

    card = _get_card(conn, txn["card_id"])
    payment = _get_payment(conn, txn["payment_id"])
    total = txn["amount_cents"] + txn["fee_cents"]
    new_balance = card["current_balance_cents"] + total
    updates = {"current_balance_cents": new_balance}
    bonus_active = card.get("bonus_target_cents") and card.get("bonus_deadline")
    if bonus_active and date.today() <= date.fromisoformat(card["bonus_deadline"]):
        progress = min(
            card["bonus_target_cents"],
            (card.get("bonus_progress_cents") or 0) + txn["amount_cents"],
        )
        updates["bonus_progress_cents"] = progress
        if progress >= card["bonus_target_cents"]:
            log_event(
                conn,
                "bonus_complete",
                f"{card['name']} welcome bonus requirement is now fully met.",
                transaction_id=txn["id"],
                payment_id=payment["id"],
            )
    conn.execute(
        "UPDATE cards SET current_balance_cents = ?, bonus_progress_cents ="
        " COALESCE(?, bonus_progress_cents) WHERE id = ?",
        (updates["current_balance_cents"], updates.get("bonus_progress_cents"), card["id"]),
    )
    conn.execute(
        "UPDATE payments SET last_result = 'paid' WHERE id = ?", (payment["id"],)
    )


def _apply_decline(conn: sqlite3.Connection, txn: dict[str, Any], reason: str) -> dict[str, Any]:
    """Record the failure, then rerun card selection excluding the declined card."""

    conn.execute(
        "UPDATE cards SET recent_failures = recent_failures + 1 WHERE id = ?",
        (txn["card_id"],),
    )
    conn.execute(
        "UPDATE payments SET last_result = ? WHERE id = ?",
        (f"failed: {reason}", txn["payment_id"]),
    )
    payment = _get_payment(conn, txn["payment_id"])
    failover = _recommendation_for(conn, payment, exclude={txn["card_id"]})
    primary = failover["primary_card_id"]
    if primary:
        conn.execute(
            "UPDATE payments SET backup_card_id = ? WHERE id = ?", (primary, payment["id"])
        )
        log_event(
            conn,
            "failover_recommended",
            f"Primary card failed; SwitchPay recommends the backup card "
            f"({failover['ranked'][0]['card_name']}). No duplicate charge was created — "
            "approve the switch and retry with a new idempotency key.",
            transaction_id=txn["id"],
            payment_id=payment["id"],
        )
    return failover


# ---------------------------------------------------------------- meta


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "switchpay",
        "synthetic_only": True,
        "schema_version": "1.0",
        "engine": {
            "ready": True,
            "config_version": DEFAULT_ENGINE_CONFIG.config_version,
            "config_hash": engine_config_hash(DEFAULT_ENGINE_CONFIG),
            "solvers": {"single_purchase": True, "greedy": True, "ilp": True},
        },
    }


@app.get("/api/catalog")
def product_catalog() -> ApiResponse:
    """Sourced public card-product terms from data/cards.json (no account data)."""

    try:
        catalog = load_product_catalog()
    except DataLoadError as exc:
        raise HTTPException(500, f"Product catalog failed to load: {exc}") from exc
    return _api_response({"catalog": catalog.model_dump(mode="json")})


@app.get("/api/demo-scenario")
def demo_scenario() -> ApiResponse:
    conn = connect()
    try:
        cards = _all_cards(conn)
        payments = _ordered_payments(conn)
        if not cards or not payments:
            seed_demo(conn, date.today())
            cards = _all_cards(conn)
            payments = _ordered_payments(conn)
        scenario = EngineScenario(
            id="switchpay-local",
            name="SwitchPay local synthetic scenario",
            reference_date=date.today(),
            cards=[_engine_card_from_row(card) for card in cards],
            purchases=[_engine_purchase_from_payment(payment) for payment in payments],
            intent=_default_engine_intent(),
        )
        return _api_response(
            {
                "scenario": scenario.model_dump(mode="json"),
                "engine_config": {
                    "version": DEFAULT_ENGINE_CONFIG.config_version,
                    "hash": engine_config_hash(DEFAULT_ENGINE_CONFIG),
                },
            }
        )
    finally:
        conn.close()


@app.post("/api/parse-intent")
async def parse_intent_endpoint(body: ParseIntentBody) -> ApiResponse:
    """Parse a natural-language goal into a validated ``Intent``.

    Language is the only ML surface. Invalid or unavailable model output yields a
    visibly-labeled equal-weight fallback (``used_fallback=true``) — the deterministic
    money path never trusts raw model output.
    """

    reference_date = body.reference_date or date.today()
    card_context = build_intent_card_context(body.cards, reference_date)
    result = await parse_intent(
        body.text,
        reference_date,
        card_context,
        _intent_provider(),
        allow_fallback=True,
    )
    return _api_response({"result": result.model_dump(mode="json")})


@app.post("/api/recommend")
def recommend(body: RecommendBody) -> ApiResponse:
    result = recommend_purchase(
        body.cards,
        body.purchase,
        body.intent,
        config=DEFAULT_ENGINE_CONFIG,
    )
    explanation = explain_recommendation(result, body.cards, body.purchase, body.intent)
    return _api_response(
        {
            "result": result.model_dump(mode="json"),
            "explanation": explanation.model_dump(mode="json"),
            "engine_config": _engine_config_meta(),
        }
    )


@app.post("/api/allocate")
def allocate(body: AllocateBody) -> ApiResponse:
    result = allocate_month(
        body.cards,
        body.purchases,
        body.intent,
        method=_solver_method(body.solver_preference),
        config=DEFAULT_ENGINE_CONFIG,
    )
    # CBC timeout/failure is handled inside the engine: the result comes back
    # honestly labeled heuristic_fallback rather than raising here.
    explanation = explain_allocation(result, body.cards, body.purchases, body.intent)
    return _api_response(
        {
            "result": result.model_dump(mode="json"),
            "explanation": explanation.model_dump(mode="json"),
            "engine_config": _engine_config_meta(),
        }
    )


@app.post("/api/frontier")
def frontier(body: FrontierBody) -> ApiResponse:
    result = sample_frontier(
        body.cards,
        body.purchases,
        body.intent,
        method=_solver_method(body.solver_preference),
        max_points=body.max_points,
        config=DEFAULT_ENGINE_CONFIG,
    )
    explanation = explain_frontier(result)
    return _api_response(
        {
            "result": result.model_dump(mode="json"),
            "explanation": explanation.model_dump(mode="json"),
            "engine_config": _engine_config_meta(),
        }
    )


@app.post("/api/what-if")
def what_if(body: WhatIfBody) -> ApiResponse:
    result = run_what_if(
        body.cards,
        body.purchases,
        body.intent,
        body.purchase_id,
        body.override_card_id,
        method=_solver_method(body.solver_preference),
        config=DEFAULT_ENGINE_CONFIG,
    )
    explanation = explain_what_if(result)
    return _api_response(
        {
            "result": result.model_dump(mode="json"),
            "explanation": explanation.model_dump(mode="json"),
            "engine_config": _engine_config_meta(),
        }
    )


@app.post("/api/seed")
def reset_demo() -> dict[str, Any]:
    conn = connect()
    try:
        created = seed_demo(conn, date.today())
        return {"ok": True, **created}
    finally:
        conn.close()


@app.get("/api/scenarios")
def scenarios() -> list[str]:
    return SIMULATOR_SCENARIOS


@app.get("/api/events")
def events(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return rows_to_dicts(
            conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        )
    finally:
        conn.close()


# ---------------------------------------------------------------- cards


@app.get("/api/cards")
def list_cards() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return _all_cards(conn)
    finally:
        conn.close()


@app.post("/api/cards", status_code=201)
def create_card(body: CardBody) -> dict[str, Any]:
    conn = connect()
    try:
        card_id = new_id("card")
        conn.execute(
            "INSERT INTO cards (id, name, token, reward_type, reward_rate_bps,"
            " point_value_millicents, credit_limit_cents, current_balance_cents,"
            " bonus_target_cents, bonus_progress_cents, bonus_value_cents, bonus_deadline,"
            " expiry_date, status, ineligible_categories, recent_failures, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                card_id,
                body.name,
                fake_token(),
                body.reward_type,
                body.reward_rate_bps,
                body.point_value_millicents,
                body.credit_limit_cents,
                body.current_balance_cents,
                body.bonus_target_cents,
                body.bonus_progress_cents,
                body.bonus_value_cents,
                body.bonus_deadline.isoformat() if body.bonus_deadline else None,
                body.expiry_date.isoformat() if body.expiry_date else None,
                body.status,
                body.ineligible_categories,
                now_iso(),
            ),
        )
        log_event(conn, "card_added", f"Synthetic card added: {body.name}.")
        conn.commit()
        return _get_card(conn, card_id)
    finally:
        conn.close()


@app.put("/api/cards/{card_id}")
def update_card(card_id: str, body: CardBody) -> dict[str, Any]:
    conn = connect()
    try:
        _get_card(conn, card_id)
        conn.execute(
            "UPDATE cards SET name = ?, reward_type = ?, reward_rate_bps = ?,"
            " point_value_millicents = ?, credit_limit_cents = ?, current_balance_cents = ?,"
            " bonus_target_cents = ?, bonus_progress_cents = ?, bonus_value_cents = ?,"
            " bonus_deadline = ?, expiry_date = ?, status = ?, ineligible_categories = ?"
            " WHERE id = ?",
            (
                body.name,
                body.reward_type,
                body.reward_rate_bps,
                body.point_value_millicents,
                body.credit_limit_cents,
                body.current_balance_cents,
                body.bonus_target_cents,
                body.bonus_progress_cents,
                body.bonus_value_cents,
                body.bonus_deadline.isoformat() if body.bonus_deadline else None,
                body.expiry_date.isoformat() if body.expiry_date else None,
                body.status,
                body.ineligible_categories,
                card_id,
            ),
        )
        log_event(conn, "card_updated", f"Card updated: {body.name}.")
        conn.commit()
        return _get_card(conn, card_id)
    finally:
        conn.close()


@app.delete("/api/cards/{card_id}", status_code=204)
def delete_card(card_id: str) -> None:
    conn = connect()
    try:
        card = _get_card(conn, card_id)
        conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        log_event(conn, "card_removed", f"Card removed: {card['name']}.")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- payments


@app.get("/api/payments")
def list_payments() -> list[dict[str, Any]]:
    conn = connect()
    try:
        payments = _ordered_payments(conn)
        return [_payment_with_names(conn, p) for p in payments]
    finally:
        conn.close()


@app.post("/api/payments", status_code=201)
def create_payment(body: PaymentBody) -> dict[str, Any]:
    conn = connect()
    try:
        if body.funding_card_id:
            _get_card(conn, body.funding_card_id)
        payment_id = new_id("pay")
        next_rank = conn.execute(
            "SELECT COALESCE(MAX(priority_rank), -1) + 1 AS next_rank FROM payments"
        ).fetchone()["next_rank"]
        conn.execute(
            "INSERT INTO payments (id, name, category, amount_cents, due_date, frequency,"
            " processing_fee_bps, funding_card_id, priority_rank, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payment_id,
                body.name,
                body.category.lower(),
                body.amount_cents,
                body.due_date.isoformat(),
                body.frequency,
                body.processing_fee_bps,
                body.funding_card_id,
                next_rank,
                now_iso(),
            ),
        )
        log_event(conn, "payment_added", f"Recurring payment added: {body.name}.")
        conn.commit()
        return _payment_with_names(conn, _get_payment(conn, payment_id))
    finally:
        conn.close()


@app.put("/api/payments/{payment_id}")
def update_payment(payment_id: str, body: PaymentBody) -> dict[str, Any]:
    conn = connect()
    try:
        _get_payment(conn, payment_id)
        if body.funding_card_id:
            _get_card(conn, body.funding_card_id)
        conn.execute(
            "UPDATE payments SET name = ?, category = ?, amount_cents = ?, due_date = ?,"
            " frequency = ?, processing_fee_bps = ?, funding_card_id = ? WHERE id = ?",
            (
                body.name,
                body.category.lower(),
                body.amount_cents,
                body.due_date.isoformat(),
                body.frequency,
                body.processing_fee_bps,
                body.funding_card_id,
                payment_id,
            ),
        )
        log_event(conn, "payment_updated", f"Payment updated: {body.name}.")
        conn.commit()
        return _payment_with_names(conn, _get_payment(conn, payment_id))
    finally:
        conn.close()


@app.delete("/api/payments/{payment_id}", status_code=204)
def delete_payment(payment_id: str) -> None:
    conn = connect()
    try:
        payment = _get_payment(conn, payment_id)
        conn.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
        _compact_payment_priorities(conn)
        log_event(conn, "payment_removed", f"Payment removed: {payment['name']}.")
        conn.commit()
    finally:
        conn.close()


@app.put("/api/payment-priorities")
def update_payment_priorities(body: PriorityBody) -> list[dict[str, Any]]:
    conn = connect()
    try:
        existing = _ordered_payments(conn)
        existing_ids = {payment["id"] for payment in existing}
        submitted_ids = body.payment_ids
        submitted_set = set(submitted_ids)

        if len(submitted_ids) != len(submitted_set):
            raise HTTPException(400, "Priority list contains duplicate payments.")
        if submitted_set != existing_ids:
            missing = sorted(existing_ids - submitted_set)
            unknown = sorted(submitted_set - existing_ids)
            detail = "Priority list must include every payment exactly once."
            if missing:
                detail += f" Missing: {', '.join(missing)}."
            if unknown:
                detail += f" Unknown: {', '.join(unknown)}."
            raise HTTPException(400, detail)

        for index, payment_id in enumerate(submitted_ids):
            conn.execute(
                "UPDATE payments SET priority_rank = ? WHERE id = ?",
                (index, payment_id),
            )
        log_event(
            conn,
            "priority_updated",
            "Payment priority order updated.",
            detail={"payment_ids": submitted_ids},
        )
        conn.commit()
        return [_payment_with_names(conn, p) for p in _ordered_payments(conn)]
    finally:
        conn.close()


# ---------------------------------------------------------------- recommendation


@app.get("/api/payments/{payment_id}/recommendation")
def payment_recommendation(payment_id: str, exclude: str | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        payment = _get_payment(conn, payment_id)
        exclude_ids = {exclude} if exclude else None
        return _recommendation_for(conn, payment, exclude_ids)
    finally:
        conn.close()


@app.post("/api/payments/{payment_id}/approve-switch")
def approve_switch(payment_id: str, body: SwitchBody) -> dict[str, Any]:
    conn = connect()
    try:
        payment = _get_payment(conn, payment_id)
        card = _get_card(conn, body.to_card_id)
        previous = payment.get("funding_card_id")
        ranking = _recommendation_for(conn, payment)
        backup = ranking["backup_card_id"]
        conn.execute(
            "UPDATE payments SET funding_card_id = ?, backup_card_id = ? WHERE id = ?",
            (body.to_card_id, backup, payment_id),
        )
        log_event(
            conn,
            "switch_approved",
            f"User approved switching {payment['name']} to {card['name']}.",
            payment_id=payment_id,
            detail={"from": previous, "to": body.to_card_id},
        )
        conn.commit()
        return _payment_with_names(conn, _get_payment(conn, payment_id))
    finally:
        conn.close()


# ---------------------------------------------------------------- payment execution


@app.post("/api/payments/{payment_id}/pay", status_code=201)
def pay(payment_id: str, body: PayBody) -> dict[str, Any]:
    conn = connect()
    try:
        payment = _get_payment(conn, payment_id)
        existing = conn.execute(
            "SELECT id FROM transactions WHERE idempotency_key = ?", (body.idempotency_key,)
        ).fetchone()
        if existing is not None:
            log_event(
                conn,
                "duplicate_blocked",
                "Duplicate payment request blocked by idempotency key — the original "
                "transaction is returned instead of creating a second charge.",
                transaction_id=existing["id"],
                payment_id=payment_id,
            )
            conn.commit()
            result = _txn_response(conn, existing["id"])
            result["duplicate"] = True
            return result

        if not payment.get("funding_card_id"):
            raise HTTPException(400, "Payment has no funding card assigned.")
        card = _get_card(conn, payment["funding_card_id"])

        scenario = body.scenario
        if scenario not in sm.SCENARIOS:
            raise HTTPException(400, f"Unknown scenario {scenario}")
        # Defensive pre-checks: real card state overrides an optimistic scenario.
        available = card["credit_limit_cents"] - card["current_balance_cents"]
        if scenario == "success":
            if card["status"] == "locked":
                scenario = "card_locked"
            elif card.get("expiry_date") and date.fromisoformat(card["expiry_date"]) < date.today():
                scenario = "card_expired"
            elif available < payment["amount_cents"]:
                scenario = "insufficient_credit"

        txn_id = new_id("txn")
        fee = payment["amount_cents"] * payment["processing_fee_bps"] // 10_000
        conn.execute(
            "INSERT INTO transactions (id, payment_id, card_id, card_name, amount_cents,"
            " fee_cents, state, scenario, step_index, idempotency_key, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (
                txn_id,
                payment_id,
                card["id"],
                card["name"],
                payment["amount_cents"],
                fee,
                sm.SCHEDULED,
                scenario,
                body.idempotency_key,
                now_iso(),
                now_iso(),
            ),
        )
        log_event(
            conn,
            "state",
            f"Payment scheduled on {card['name']} with idempotency key "
            f"{body.idempotency_key[:18]}….",
            transaction_id=txn_id,
            payment_id=payment_id,
            to_state=sm.SCHEDULED,
        )
        conn.commit()
        result = _txn_response(conn, txn_id)
        result["duplicate"] = False
        return result
    finally:
        conn.close()


@app.post("/api/transactions/{txn_id}/advance")
def advance(txn_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        txn_row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if txn_row is None:
            raise HTTPException(404, f"Transaction {txn_id} not found")
        txn = dict(txn_row)

        if txn["state"] in sm.TERMINAL_STATES:
            return _txn_response(conn, txn_id)
        if txn["state"] == sm.UNCERTAIN and not txn["scenario"].startswith("verified_"):
            result = _txn_response(conn, txn_id)
            result["needs_verification"] = True
            return result

        script = _get_script(txn["scenario"])
        step = script[txn["step_index"]] if txn["step_index"] < len(script) else None
        if step is None:
            return _txn_response(conn, txn_id)

        sm.validate_transition(txn["state"], step.state)
        conn.execute(
            "UPDATE transactions SET state = ?, step_index = ?, failure_reason = ?,"
            " updated_at = ? WHERE id = ?",
            (
                step.state,
                txn["step_index"] + 1,
                step.message if step.state == sm.FAILED else txn["failure_reason"],
                now_iso(),
                txn_id,
            ),
        )
        log_event(
            conn,
            "state",
            step.message,
            transaction_id=txn_id,
            payment_id=txn["payment_id"],
            from_state=txn["state"],
            to_state=step.state,
        )

        failover = None
        if step.state == sm.RECIPIENT_PAID:
            _apply_settlement(conn, txn)
        if step.state == sm.FAILED and txn["scenario"] in sm.DECLINE_SCENARIOS:
            failover = _apply_decline(conn, txn, step.message)
        conn.commit()

        result = _txn_response(conn, txn_id)
        if failover is not None:
            result["failover_recommendation"] = failover
        if result["state"] == sm.UNCERTAIN:
            result["needs_verification"] = True
        return result
    finally:
        conn.close()


@app.post("/api/transactions/{txn_id}/verify")
def verify(txn_id: str, body: VerifyBody) -> dict[str, Any]:
    conn = connect()
    try:
        txn_row = conn.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
        if txn_row is None:
            raise HTTPException(404, f"Transaction {txn_id} not found")
        txn = dict(txn_row)
        if txn["state"] != sm.UNCERTAIN:
            raise HTTPException(400, "Only uncertain transactions can be verified.")

        scenario = "verified_success" if body.confirmed else "verified_failed"
        conn.execute(
            "UPDATE transactions SET scenario = ?, step_index = 0, updated_at = ? WHERE id = ?",
            (scenario, now_iso(), txn_id),
        )
        log_event(
            conn,
            "verification",
            "Verification of the original transaction "
            + ("found a completed authorization." if body.confirmed else "found no charge."),
            transaction_id=txn_id,
            payment_id=txn["payment_id"],
        )
        conn.commit()
        return advance(txn_id)
    finally:
        conn.close()


@app.get("/api/transactions")
def list_transactions(payment_id: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        if payment_id:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE payment_id = ? ORDER BY created_at DESC",
                (payment_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM transactions ORDER BY created_at DESC"
            ).fetchall()
        names = {
            p["id"]: p["name"]
            for p in rows_to_dicts(conn.execute("SELECT id, name FROM payments").fetchall())
        }
        results = []
        for row in rows_to_dicts(rows):
            row["payment_name"] = names.get(row["payment_id"], "(deleted)")
            row["is_terminal"] = row["state"] in sm.TERMINAL_STATES
            results.append(row)
        return results
    finally:
        conn.close()


@app.get("/api/transactions/{txn_id}")
def get_transaction(txn_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        return _txn_response(conn, txn_id)
    finally:
        conn.close()


# ---------------------------------------------------------------- dashboard


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    conn = connect()
    try:
        cards = _all_cards(conn)
        payments = _ordered_payments(conn)
        today = date.today()
        priority_plan = build_priority_plan(cards, payments, today)

        total_rewards = 0
        total_fees = 0
        payment_summaries = []
        alerts: list[dict[str, str]] = []

        for payment in payments:
            ranking = _recommendation_for(conn, payment)
            switch = ranking["switch"]
            funding_eval = None
            if payment.get("funding_card_id"):
                card = next(
                    (c for c in cards if c["id"] == payment["funding_card_id"]), None
                )
                if card is not None:
                    on_date = max(date.fromisoformat(payment["due_date"]), today)
                    funding_eval = evaluate_card(card, payment, on_date).as_dict()
            if funding_eval and funding_eval["eligible"]:
                total_rewards += funding_eval["reward_cents"]
                total_fees += funding_eval["fee_cents"]
            payment_summaries.append(
                {
                    "payment": ranking["payment"],
                    "primary_card_id": ranking["primary_card_id"],
                    "primary_card_name": (
                        ranking["ranked"][0]["card_name"] if ranking["ranked"] else None
                    ),
                    "backup_card_id": ranking["backup_card_id"],
                    "funding_eval": funding_eval,
                    "switch": switch,
                    **priority_plan.get(payment["id"], {}),
                }
            )
            if switch is not None:
                alerts.append(
                    {"kind": "switch", "message": switch["headline"], "payment_id": payment["id"]}
                )

        for card in cards:
            if card.get("bonus_target_cents") and card.get("bonus_deadline"):
                remaining = card["bonus_target_cents"] - (card.get("bonus_progress_cents") or 0)
                deadline = date.fromisoformat(card["bonus_deadline"])
                days = (deadline - today).days
                if remaining > 0 and 0 <= days <= 21:
                    alerts.append(
                        {
                            "kind": "bonus_deadline",
                            "message": (
                                f"{card['name']}: ${remaining / 100:,.0f} of spend still needed "
                                f"for the welcome bonus — deadline in {days} day(s)."
                            ),
                        }
                    )
            if card["status"] == "locked":
                alerts.append(
                    {"kind": "card_locked", "message": f"{card['name']} is locked."}
                )
            if card.get("expiry_date") and date.fromisoformat(card["expiry_date"]) < today:
                alerts.append(
                    {"kind": "card_expired", "message": f"{card['name']} has expired."}
                )

        uncertain = rows_to_dicts(
            conn.execute(
                "SELECT * FROM transactions WHERE state = ?", (sm.UNCERTAIN,)
            ).fetchall()
        )
        for txn in uncertain:
            alerts.append(
                {
                    "kind": "uncertain",
                    "message": (
                        "Payment status is uncertain. SwitchPay will verify the original "
                        "transaction before attempting another charge."
                    ),
                    "transaction_id": txn["id"],
                }
            )

        return {
            "payments": payment_summaries,
            "cards": cards,
            "alerts": alerts,
            "totals": {
                "estimated_reward_cents": total_rewards,
                "estimated_fee_cents": total_fees,
                "payment_count": len(payments),
                "card_count": len(cards),
            },
        }
    finally:
        conn.close()
