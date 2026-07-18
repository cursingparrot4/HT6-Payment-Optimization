"""Validated UTF-8 JSON loaders for product, scenario, and eval data."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from data.models import (
    EvalProbeDocument,
    LoadedEvalProbe,
    LoadedScenario,
    ProductCatalog,
    ProductDefinition,
    ScenarioDocument,
    ScenarioMetadata,
)
from engine.models import Scenario

DATA_ROOT = Path(__file__).resolve().parent


class DataErrorCode(StrEnum):
    FILE_NOT_FOUND = "file_not_found"
    INVALID_JSON = "invalid_json"
    INVALID_SCHEMA = "invalid_schema"
    UNKNOWN_PRODUCT = "unknown_product"
    INVALID_REFERENCE = "invalid_reference"


class DataLoadError(RuntimeError):
    def __init__(self, code: DataErrorCode, path: Path, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code.value}: {path}: {detail}")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataLoadError(DataErrorCode.FILE_NOT_FOUND, path, "file does not exist") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataLoadError(DataErrorCode.INVALID_JSON, path, str(exc)) from exc


def _validate(model_type, payload: Any, path: Path):
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise DataLoadError(DataErrorCode.INVALID_SCHEMA, path, str(exc)) from exc


def load_product_catalog(path: Path | None = None) -> ProductCatalog:
    catalog_path = path or DATA_ROOT / "cards.json"
    return _validate(ProductCatalog, _read_json(catalog_path), catalog_path)


def load_card_catalog(path: Path | None = None) -> list[ProductDefinition]:
    """Return fresh public product definitions for callers needing a simple list."""

    catalog = load_product_catalog(path)
    return [product.model_copy(deep=True) for product in catalog.products]


def _scenario_path(scenario_id: str, data_root: Path) -> Path:
    filename = scenario_id.replace("-", "_") + ".json"
    return data_root / "scenarios" / filename


def _assemble_scenario(
    document: ScenarioDocument,
    catalog: ProductCatalog,
    path: Path,
) -> LoadedScenario:
    products_by_id = {product.id: product for product in catalog.products}
    cards = []
    selected_products = []
    account_assumptions: dict[str, list[str]] = {}
    for account in document.accounts:
        product = products_by_id.get(account.product_id)
        if product is None:
            raise DataLoadError(
                DataErrorCode.UNKNOWN_PRODUCT,
                path,
                f"unknown account product ID: {account.product_id}",
            )
        cards.append(product.build_card(account))
        selected_products.append(product.model_copy(deep=True))
        account_assumptions[account.product_id] = list(account.assumptions)

    cards_by_id = {card.id: card for card in cards}
    for preset_name, intent in document.demo_intents.items():
        for card_id in intent.constraints.must_hit_bonus_card_ids:
            card = cards_by_id.get(card_id)
            if card is None or card.signup_bonus is None:
                raise DataLoadError(
                    DataErrorCode.INVALID_REFERENCE,
                    path,
                    f"preset {preset_name} requires unavailable bonus card {card_id}",
                )

    scenario = Scenario(
        id=document.id,
        name=document.name,
        synthetic=True,
        reference_date=document.reference_date,
        cards=cards,
        purchases=[purchase.model_copy(deep=True) for purchase in document.purchases],
    )
    return LoadedScenario(
        scenario=scenario,
        products=selected_products,
        demo_intents={
            name: intent.model_copy(deep=True)
            for name, intent in document.demo_intents.items()
        },
        persona_label=document.persona_label,
        account_assumptions=account_assumptions,
        notes=list(document.notes),
    )


def load_scenario(
    scenario_id: str = "sarah-august-2026",
    data_root: Path | None = None,
) -> LoadedScenario:
    root = data_root or DATA_ROOT
    path = _scenario_path(scenario_id, root)
    document = _validate(ScenarioDocument, _read_json(path), path)
    catalog = load_product_catalog(root / "cards.json")
    return _assemble_scenario(document, catalog, path)


def list_scenarios(data_root: Path | None = None) -> list[ScenarioMetadata]:
    root = data_root or DATA_ROOT
    scenario_dir = root / "scenarios"
    metadata: list[ScenarioMetadata] = []
    if not scenario_dir.exists():
        return metadata
    for path in sorted(scenario_dir.glob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict) or payload.get("document_type") != "scenario":
            continue
        document = _validate(ScenarioDocument, payload, path)
        metadata.append(
            ScenarioMetadata(
                id=document.id,
                name=document.name,
                persona_label=document.persona_label,
                reference_date=document.reference_date,
                product_ids=[account.product_id for account in document.accounts],
                purchase_count=len(document.purchases),
            )
        )
    return metadata


def load_eval_probes(data_root: Path | None = None) -> list[LoadedEvalProbe]:
    root = data_root or DATA_ROOT
    path = root / "scenarios" / "eval_probes.json"
    document = _validate(EvalProbeDocument, _read_json(path), path)
    catalog = load_product_catalog(root / "cards.json")
    products_by_id = {product.id: product for product in catalog.products}
    loaded: list[LoadedEvalProbe] = []
    for probe in document.probes:
        cards = []
        for account in probe.accounts:
            product = products_by_id.get(account.product_id)
            if product is None:
                raise DataLoadError(
                    DataErrorCode.UNKNOWN_PRODUCT,
                    path,
                    f"probe {probe.id} references unknown product {account.product_id}",
                )
            cards.append(product.build_card(account))
        loaded.append(
            LoadedEvalProbe(
                id=probe.id,
                name=probe.name,
                cards=cards,
                purchase=probe.purchase.model_copy(deep=True),
                notes=list(probe.notes),
            )
        )
    return loaded
