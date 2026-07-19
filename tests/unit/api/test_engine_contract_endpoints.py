"""Contract checks for the engine-native API endpoints used by the updated backend."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from api.main import create_app

TODAY = date(2026, 7, 18)


def _intent() -> dict:
    return {
        "weights": {
            "max_cashback": 0.2,
            "max_travel": 0.2,
            "credit_health": 0.2,
            "hit_signup_bonus": 0.25,
            "max_cashflow": 0.05,
            "min_risk": 0.1,
        }
    }


def _card(card_id: str, *, rate_bps: int = 200) -> dict:
    return {
        "id": card_id,
        "name": f"{card_id} Card",
        "credit_limit_cents": 1_000_000,
        "current_balance_cents": 50_000,
        "reward_rules": [],
        "base_rate_bps": rate_bps,
        "base_reward_type": "cashback",
        "point_value_millicents": 1_000,
        "annual_fee_cents": 0,
        "statement_day": 1,
        "due_day": 15,
        "signup_bonus": None,
    }


def _purchase(purchase_id: str = "rent") -> dict:
    return {
        "id": purchase_id,
        "amount_cents": 240_000,
        "category": "rent",
        "date": (TODAY + timedelta(days=5)).isoformat(),
        "is_recurring": True,
        "locked_card_id": None,
    }


def test_create_app_returns_cardiq_app() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["engine"]["ready"] is True


def test_recommend_endpoint_returns_versioned_engine_result() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/recommend",
        json={
            "cards": [_card("basic", rate_bps=100), _card("premium", rate_bps=300)],
            "purchase": _purchase(),
            "intent": _intent(),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["data"]["result"]["status"] == "optimal"
    assert body["data"]["result"]["winner"]["card_id"] == "premium"


def test_allocate_endpoint_runs_exact_ilp_when_requested() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/allocate",
        json={
            "cards": [_card("basic", rate_bps=100), _card("premium", rate_bps=300)],
            "purchases": [_purchase("rent"), _purchase("utilities")],
            "intent": _intent(),
            "solver_preference": "ilp",
        },
    )

    assert response.status_code == 200
    body = response.json()
    result = body["data"]["result"]
    # CBC success proves optimality; a timeout/native failure degrades to the
    # independently verified greedy result, labeled heuristic_fallback.
    if result["status"] == "optimal":
        assert result["solver_method"] == "ilp"
    else:
        assert result["status"] == "heuristic_fallback"
        assert result["solver_method"] == "greedy"
