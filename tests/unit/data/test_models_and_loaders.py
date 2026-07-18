from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data.loaders import DataErrorCode, DataLoadError, load_product_catalog, load_scenario
from data.models import ProductDefinition, SyntheticCardAccount


def product_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "rbc-test",
        "name": "RBC Test Visa",
        "issuer": "rbc",
        "network": "visa",
        "reward_program": "Avion Rewards",
        "annual_fee_cents": 4_800,
        "reward_rules": [
            {"category": "groceries", "rate_bps": 300, "reward_type": "points"}
        ],
        "base_rate_bps": 100,
        "base_reward_type": "points",
        "point_value_millicents": 714,
        "point_value_basis": "Official gift-card example reduced to a static value.",
        "engine_assumptions": [],
        "unmodeled_terms": ["Merchant classification is not modeled."],
        "sources": [
            {
                "url": "https://www.rbcroyalbank.com/example",
                "title": "Official RBC product page",
                "verified_on": "2026-07-18",
                "covers": ["product_name", "annual_fee", "earn_rates"],
            }
        ],
    }
    payload.update(overrides)
    return payload


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def catalog_payload(products: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "document_type": "product_catalog",
        "verified_on": "2026-07-18",
        "terms_notice": "Public product terms; all account state is synthetic.",
        "products": products or [product_payload()],
    }


def scenario_payload(product_id: str = "rbc-test") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "document_type": "scenario",
        "id": "sarah-august-2026",
        "name": "Sarah August 2026",
        "synthetic_persona": True,
        "persona_label": "Sarah (synthetic)",
        "reference_date": "2026-07-18",
        "accounts": [
            {
                "product_id": product_id,
                "credit_limit_cents": 500_000,
                "current_balance_cents": 50_000,
                "statement_day": 10,
                "due_day": 5,
                "assumptions": ["Account fields are synthetic."],
            }
        ],
        "purchases": [
            {
                "id": "rent",
                "amount_cents": 220_000,
                "category": "rent",
                "date": "2026-08-01",
                "is_recurring": True,
                "locked_card_id": None,
            }
        ],
        "demo_intents": {
            "balanced": {
                "weights": {
                    "max_cashback": 1,
                    "max_travel": 1,
                    "credit_health": 1,
                    "hit_signup_bonus": 1,
                    "max_cashflow": 1,
                    "min_risk": 1,
                },
                "constraints": {},
            }
        },
        "notes": ["Synthetic fixture."],
    }


def test_product_rejects_nonofficial_source_host() -> None:
    payload = product_payload()
    payload["sources"] = [
        {
            "url": "https://example.com/card",
            "title": "Not an issuer source",
            "verified_on": "2026-07-18",
            "covers": ["earn_rates"],
        }
    ]
    with pytest.raises(ValidationError, match="not official"):
        ProductDefinition.model_validate(payload)


def test_public_product_and_synthetic_account_build_engine_card_without_aliasing() -> None:
    product = ProductDefinition.model_validate(product_payload())
    account = SyntheticCardAccount(
        product_id="rbc-test",
        credit_limit_cents=500_000,
        current_balance_cents=50_000,
        statement_day=10,
        due_day=5,
    )

    card = product.build_card(account)
    card.reward_rules.append(card.reward_rules[0].model_copy(update={"category": "dining"}))

    assert card.name == "RBC Test Visa"
    assert card.credit_limit_cents == 500_000
    assert len(product.reward_rules) == 1


def test_loader_resolves_data_root_independently_of_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_json(tmp_path / "cards.json", catalog_payload())
    write_json(tmp_path / "scenarios" / "sarah_august_2026.json", scenario_payload())
    monkeypatch.chdir(tmp_path.parent)

    loaded = load_scenario(data_root=tmp_path)

    assert loaded.scenario.cards[0].id == "rbc-test"
    assert loaded.scenario.purchases[0].id == "rent"
    assert loaded.persona_label == "Sarah (synthetic)"


def test_unknown_product_reference_is_structured(tmp_path: Path) -> None:
    write_json(tmp_path / "cards.json", catalog_payload())
    write_json(
        tmp_path / "scenarios" / "sarah_august_2026.json",
        scenario_payload("missing-product"),
    )

    with pytest.raises(DataLoadError) as error:
        load_scenario(data_root=tmp_path)

    assert error.value.code is DataErrorCode.UNKNOWN_PRODUCT
    assert "missing-product" in error.value.detail


def test_invalid_json_and_schema_have_distinct_error_codes(tmp_path: Path) -> None:
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(DataLoadError) as json_error:
        load_product_catalog(invalid_json)
    assert json_error.value.code is DataErrorCode.INVALID_JSON

    invalid_schema = tmp_path / "schema.json"
    write_json(invalid_schema, {"schema_version": "1.0"})
    with pytest.raises(DataLoadError) as schema_error:
        load_product_catalog(invalid_schema)
    assert schema_error.value.code is DataErrorCode.INVALID_SCHEMA


def test_strict_account_money_fields_reject_float_and_bool() -> None:
    with pytest.raises(ValidationError):
        SyntheticCardAccount(
            product_id="rbc-test",
            credit_limit_cents=500_000.0,
            current_balance_cents=0,
            statement_day=10,
            due_day=5,
        )
    with pytest.raises(ValidationError):
        SyntheticCardAccount(
            product_id="rbc-test",
            credit_limit_cents=500_000,
            current_balance_cents=True,
            statement_day=10,
            due_day=5,
        )
