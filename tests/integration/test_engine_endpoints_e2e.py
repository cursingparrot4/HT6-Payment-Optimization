"""End-to-end HTTP flow across the engine-native API endpoints.

These drive the full request path in-process (no live server) to verify that the
deterministic engine, the explanation layer, and the intent parser are actually
wired together behind FastAPI — the integration gap unit tests cannot catch.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import api.main as main
from api.main import create_app
from intent.providers import FixtureIntentProvider

TODAY = date(2026, 7, 18)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


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


def _purchase(purchase_id: str = "rent", *, amount_cents: int = 240_000) -> dict:
    return {
        "id": purchase_id,
        "amount_cents": amount_cents,
        "category": "rent",
        "date": (TODAY + timedelta(days=5)).isoformat(),
        "is_recurring": True,
        "locked_card_id": None,
    }


def _cards() -> list[dict]:
    return [_card("basic", rate_bps=100), _card("premium", rate_bps=300)]


def _purchases() -> list[dict]:
    return [_purchase("rent"), _purchase("utilities", amount_cents=30_000)]


# --------------------------------------------------------------- explanation attach


def test_recommend_attaches_faithful_explanation(client: TestClient) -> None:
    response = client.post(
        "/api/recommend",
        json={"cards": _cards(), "purchase": _purchase(), "intent": _intent()},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result"]["winner"]["card_id"] == "premium"
    explanation = data["explanation"]
    # The explanation is built from the same solver output it accompanies.
    assert explanation["status"] == data["result"]["status"]
    assert explanation["decision_card"]["card_id"] == "premium"


def test_allocate_attaches_explanation_and_stays_consistent(client: TestClient) -> None:
    response = client.post(
        "/api/allocate",
        json={
            "cards": _cards(),
            "purchases": _purchases(),
            "intent": _intent(),
            "solver_preference": "ilp",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    result, explanation = data["result"], data["explanation"]
    assert explanation["status"] == result["status"]
    assert explanation["solver_method"] == result["solver_method"]
    # Every purchase is assigned exactly once and no card exceeds its limit.
    assigned = {a["purchase_id"] for a in result["assignments"]}
    assert assigned == {"rent", "utilities"}


# ------------------------------------------------------------------------- frontier


def test_frontier_returns_labeled_sampled_plans_with_disclosure(client: TestClient) -> None:
    response = client.post(
        "/api/frontier",
        json={
            "cards": _cards(),
            "purchases": _purchases(),
            "intent": _intent(),
            "solver_preference": "ilp",
            "max_points": 3,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    result = data["result"]
    assert 1 <= len(result["points"]) <= 3
    assert result["grid_size"] >= 1
    # Sampled-frontier incompleteness must always be disclosed (invariant #10).
    assert any("sampled" in warning.lower() for warning in result["warnings"])
    assert len(data["explanation"]["points"]) == len(result["points"])


# -------------------------------------------------------------------------- what-if


def test_what_if_reoptimizes_with_override(client: TestClient) -> None:
    response = client.post(
        "/api/what-if",
        json={
            "cards": _cards(),
            "purchases": _purchases(),
            "intent": _intent(),
            "purchase_id": "rent",
            "override_card_id": "basic",
            "solver_preference": "ilp",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    result = data["result"]
    assert result["purchase_id"] == "rent"
    assert result["override_card_id"] == "basic"
    assert "explanation" in data


def test_what_if_unknown_card_is_structured_not_a_crash(client: TestClient) -> None:
    response = client.post(
        "/api/what-if",
        json={
            "cards": _cards(),
            "purchases": _purchases(),
            "intent": _intent(),
            "purchase_id": "rent",
            "override_card_id": "does-not-exist",
            "solver_preference": "ilp",
        },
    )
    assert response.status_code == 200
    override = response.json()["data"]["result"]["override_result"]
    assert override["status"] == "infeasible"


# --------------------------------------------------------------------- parse-intent


def test_parse_intent_returns_visible_fallback_without_a_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force the unavailable fixture provider so this stays hermetic regardless of any
    # CARDIQ_INTENT_PROVIDER set in a local .env: the money path must never fabricate
    # weights, so parse falls back visibly to equal weights.
    monkeypatch.setattr(main, "_INTENT_PROVIDER", FixtureIntentProvider(responses={}))
    response = client.post(
        "/api/parse-intent",
        json={"text": "keep utilization low for my mortgage", "cards": _cards()},
    )
    assert response.status_code == 200
    result = response.json()["data"]["result"]
    assert result["used_fallback"] is True
    assert result["source"] == "fallback"


def test_parse_intent_success_path_with_injected_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "weights": {
            "max_cashback": 0.1,
            "max_travel": 0.1,
            "credit_health": 0.5,
            "hit_signup_bonus": 0.2,
            "max_cashflow": 0.05,
            "min_risk": 0.05,
        },
        "constraints": {
            "max_utilization_bps": 2000,
            "max_utilization_until": None,
            "must_hit_bonus_card_ids": [],
        },
    }
    provider = FixtureIntentProvider({"lower my utilization": json.dumps(payload)})
    monkeypatch.setattr(main, "_INTENT_PROVIDER", provider)

    response = client.post(
        "/api/parse-intent",
        json={
            "text": "lower my utilization",
            "cards": _cards(),
            "reference_date": TODAY.isoformat(),
        },
    )
    assert response.status_code == 200
    result = response.json()["data"]["result"]
    assert result["used_fallback"] is False
    assert result["intent"] is not None
    assert result["intent"]["constraints"]["max_utilization_bps"] == 2000
