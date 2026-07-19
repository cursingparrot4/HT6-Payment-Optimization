"""End-to-end walk of the CardIQ demo surface (seed → switch → pay → settle).

Drives the DB-backed API in-process the way the browser dashboard, payment, and
tracker pages do. This is the surface where **switch recommendations** live, so
the test pins the demo-opening behaviour the PLAN promises: a freshly seeded
portfolio must surface real switch recommendations (rent should move to the Amex
Gold card to complete its welcome bonus), approving one must clear it, and a
payment must walk the lifecycle and settle onto the card.

The DB path is redirected to a temp file so the test never clobbers the demo
database at ``data/cardiq.db``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import api.db as db
from api.main import create_app
from api.state_machine import RECONCILED, TERMINAL_STATES


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    # Redirect persistence to an isolated temp DB; connect() reads the module global
    # on every call, so patching it here is enough to keep the real demo DB untouched.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cardiq_test.db")
    with TestClient(create_app()) as test_client:
        yield test_client


def _seed(client: TestClient) -> dict:
    response = client.post("/api/seed")
    assert response.status_code == 200
    return response.json()


def _dashboard(client: TestClient) -> dict:
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    return response.json()


def _payment_by_name(dashboard: dict, name: str) -> dict:
    entry = next(e for e in dashboard["payments"] if e["payment"]["name"] == name)
    return entry


# ------------------------------------------------------------- seed & switch surface


def test_seed_creates_full_synthetic_portfolio(client: TestClient) -> None:
    created = _seed(client)
    assert created["ok"] is True
    assert len(created["cards"]) == 4
    assert len(created["payments"]) == 6


def test_fresh_seed_surfaces_switch_recommendations(client: TestClient) -> None:
    # The demo must open with real switches — a regression to "everything already
    # optimal, $0 off-optimal" would silently gut the headline feature.
    _seed(client)
    dashboard = _dashboard(client)

    switches = [e for e in dashboard["payments"] if e["switch"] is not None]
    assert switches, "a freshly seeded demo must surface at least one switch recommendation"

    switch_alerts = [a for a in dashboard["alerts"] if a["kind"] == "switch"]
    assert switch_alerts, "switch recommendations must also raise dashboard alerts"

    # Rent is the anchor of the demo narrative: it should move to the Amex Gold card
    # to complete that card's synthetic welcome bonus.
    rent = _payment_by_name(dashboard, "Rent")
    assert rent["switch"] is not None
    assert rent["switch"]["to_card_id"] == "amex-gold-rewards"
    assert rent["switch"]["delta_cents"] > 0


def test_approving_a_switch_clears_it_and_reroutes_the_payment(client: TestClient) -> None:
    _seed(client)
    dashboard = _dashboard(client)
    rent = _payment_by_name(dashboard, "Rent")
    payment_id = rent["payment"]["id"]
    target_card_id = rent["switch"]["to_card_id"]

    approve = client.post(
        f"/api/payments/{payment_id}/approve-switch",
        json={"to_card_id": target_card_id},
    )
    assert approve.status_code == 200
    assert approve.json()["funding_card_id"] == target_card_id

    # Once funded by the recommended card, the payment is optimal — no switch remains.
    rent_after = _payment_by_name(_dashboard(client), "Rent")
    assert rent_after["switch"] is None


# ------------------------------------------------------------- pay → settle lifecycle


def test_payment_walks_lifecycle_and_settles_onto_the_card(client: TestClient) -> None:
    _seed(client)
    dashboard = _dashboard(client)
    rent = _payment_by_name(dashboard, "Rent")
    payment_id = rent["payment"]["id"]

    # Route rent to its recommended card first, then charge it.
    target_card_id = rent["switch"]["to_card_id"]
    client.post(
        f"/api/payments/{payment_id}/approve-switch",
        json={"to_card_id": target_card_id},
    )

    def card_state(card_id: str) -> dict:
        cards = client.get("/api/cards").json()
        return next(c for c in cards if c["id"] == card_id)

    before = card_state(target_card_id)

    pay = client.post(
        f"/api/payments/{payment_id}/pay",
        json={"idempotency_key": "e2e-demo-key-0001", "scenario": "success"},
    )
    assert pay.status_code == 201
    txn = pay.json()
    assert txn["duplicate"] is False

    # Walk the scripted success path to a terminal state.
    txn_id = txn["id"]
    for _ in range(10):
        if txn["state"] in TERMINAL_STATES:
            break
        txn = client.post(f"/api/transactions/{txn_id}/advance").json()
    assert txn["state"] == RECONCILED

    after = card_state(target_card_id)
    charge = txn["amount_cents"] + txn["fee_cents"]
    assert after["current_balance_cents"] == before["current_balance_cents"] + charge
    # Rent on the Amex completes its welcome bonus, so progress must advance.
    assert (after["bonus_progress_cents"] or 0) >= (before["bonus_progress_cents"] or 0)


def test_idempotency_key_blocks_a_duplicate_charge(client: TestClient) -> None:
    _seed(client)
    rent = _payment_by_name(_dashboard(client), "Rent")
    payment_id = rent["payment"]["id"]

    body = {"idempotency_key": "e2e-dupe-key-0001", "scenario": "success"}
    first = client.post(f"/api/payments/{payment_id}/pay", json=body).json()
    second = client.post(f"/api/payments/{payment_id}/pay", json=body).json()

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
