"""Data-owned contracts separating public product terms from synthetic accounts."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engine.models import (
    Card,
    Intent,
    Purchase,
    RewardRule,
    RewardType,
    Scenario,
    SignupBonus,
)

Identifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictCardDay = Annotated[int, Field(strict=True, ge=1, le=28)]
ShortText = Annotated[str, Field(strict=True, min_length=1, max_length=500)]


class DataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Issuer(StrEnum):
    RBC = "rbc"
    TD = "td"
    AMERICAN_EXPRESS = "american_express"
    SCOTIABANK = "scotiabank"
    ROGERS_BANK = "rogers_bank"


class CardNetwork(StrEnum):
    VISA = "visa"
    MASTERCARD = "mastercard"
    AMERICAN_EXPRESS = "american_express"


class SourceField(StrEnum):
    PRODUCT_NAME = "product_name"
    ANNUAL_FEE = "annual_fee"
    EARN_RATES = "earn_rates"
    POINT_VALUE = "point_value"
    CAPS_AND_CONDITIONS = "caps_and_conditions"
    PUBLIC_OFFER = "public_offer"


_OFFICIAL_HOSTS = {
    Issuer.RBC: {"rbcroyalbank.com", "www.rbcroyalbank.com"},
    Issuer.TD: {"td.com", "www.td.com"},
    Issuer.AMERICAN_EXPRESS: {"americanexpress.com", "www.americanexpress.com"},
    Issuer.SCOTIABANK: {"scotiabank.com", "www.scotiabank.com"},
    Issuer.ROGERS_BANK: {"rogersbank.com", "www.rogersbank.com"},
}


class SourceReference(DataModel):
    url: Annotated[str, Field(strict=True, min_length=10, max_length=500)]
    title: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    verified_on: date
    covers: list[SourceField] = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("source URL must be an absolute HTTPS URL")
        return value

    @field_validator("covers")
    @classmethod
    def source_fields_are_unique(cls, value: list[SourceField]) -> list[SourceField]:
        if len(value) != len(set(value)):
            raise ValueError("source coverage fields must be unique")
        return value


class ProductDefinition(DataModel):
    """Public issuer terms reduced to the engine's representable reward contract."""

    id: Identifier
    name: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    issuer: Issuer
    network: CardNetwork
    reward_program: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    annual_fee_cents: StrictNonNegativeInt
    reward_rules: list[RewardRule] = Field(default_factory=list, max_length=20)
    base_rate_bps: Annotated[int, Field(strict=True, ge=0, le=10_000)]
    base_reward_type: RewardType
    point_value_millicents: StrictNonNegativeInt
    point_value_basis: ShortText
    public_offer_summary: ShortText | None = None
    engine_assumptions: list[ShortText] = Field(default_factory=list)
    unmodeled_terms: list[ShortText] = Field(default_factory=list)
    sources: list[SourceReference] = Field(min_length=1)

    @model_validator(mode="after")
    def product_contract_is_consistent(self) -> ProductDefinition:
        categories = [rule.category for rule in self.reward_rules]
        if len(categories) != len(set(categories)):
            raise ValueError("product reward categories must be unique")
        allowed_hosts = _OFFICIAL_HOSTS[self.issuer]
        for source in self.sources:
            hostname = urlparse(source.url).hostname
            if hostname not in allowed_hosts:
                raise ValueError(
                    f"source host {hostname!r} is not official for issuer {self.issuer.value}"
                )
        covered_fields = {
            field
            for source in self.sources
            for field in source.covers
        }
        required_fields = {
            SourceField.PRODUCT_NAME,
            SourceField.ANNUAL_FEE,
            SourceField.EARN_RATES,
        }
        if not required_fields.issubset(covered_fields):
            missing = sorted(field.value for field in required_fields - covered_fields)
            raise ValueError(f"official sources must cover core product fields; missing={missing}")
        if (
            self.base_reward_type is RewardType.CASHBACK
            and self.point_value_millicents != 1_000
        ):
            raise ValueError("cashback products use a neutral 1,000 millicent point value")
        return self

    def build_card(self, account: SyntheticCardAccount) -> Card:
        if account.product_id != self.id:
            raise ValueError(
                f"account product {account.product_id!r} does not match {self.id!r}"
            )
        return Card(
            id=self.id,
            name=self.name,
            credit_limit_cents=account.credit_limit_cents,
            current_balance_cents=account.current_balance_cents,
            reward_rules=[rule.model_copy(deep=True) for rule in self.reward_rules],
            base_rate_bps=self.base_rate_bps,
            base_reward_type=self.base_reward_type,
            point_value_millicents=self.point_value_millicents,
            annual_fee_cents=self.annual_fee_cents,
            statement_day=account.statement_day,
            due_day=account.due_day,
            signup_bonus=(
                account.synthetic_signup_bonus.model_copy(deep=True)
                if account.synthetic_signup_bonus is not None
                else None
            ),
        )


class ProductCatalog(DataModel):
    schema_version: Literal["1.0"] = "1.0"
    document_type: Literal["product_catalog"] = "product_catalog"
    verified_on: date
    terms_notice: ShortText
    products: list[ProductDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def product_ids_are_unique(self) -> ProductCatalog:
        ids = [product.id for product in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError("product IDs must be unique")
        if any(
            source.verified_on > self.verified_on
            for product in self.products
            for source in product.sources
        ):
            raise ValueError("source verification dates cannot follow catalog verification")
        return self


class SyntheticCardAccount(DataModel):
    product_id: Identifier
    credit_limit_cents: StrictNonNegativeInt
    current_balance_cents: StrictNonNegativeInt
    statement_day: StrictCardDay
    due_day: StrictCardDay
    synthetic_signup_bonus: SignupBonus | None = None
    assumptions: list[ShortText] = Field(default_factory=list)


class ScenarioDocument(DataModel):
    schema_version: Literal["1.0"] = "1.0"
    document_type: Literal["scenario"] = "scenario"
    id: Identifier
    name: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    synthetic_persona: Literal[True] = True
    persona_label: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    reference_date: date
    accounts: list[SyntheticCardAccount] = Field(min_length=1, max_length=8)
    purchases: list[Purchase] = Field(min_length=1, max_length=60)
    demo_intents: dict[Identifier, Intent] = Field(min_length=1)
    notes: list[ShortText] = Field(default_factory=list)

    @model_validator(mode="after")
    def scenario_ids_are_unique(self) -> ScenarioDocument:
        product_ids = [account.product_id for account in self.accounts]
        purchase_ids = [purchase.id for purchase in self.purchases]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("scenario account product IDs must be unique")
        if len(purchase_ids) != len(set(purchase_ids)):
            raise ValueError("scenario purchase IDs must be unique")
        return self


class LoadedScenario(DataModel):
    scenario: Scenario
    products: list[ProductDefinition]
    demo_intents: dict[str, Intent]
    persona_label: str
    account_assumptions: dict[str, list[str]]
    notes: list[str]


class ScenarioMetadata(DataModel):
    id: Identifier
    name: str
    persona_label: str
    reference_date: date
    product_ids: list[Identifier]
    purchase_count: StrictNonNegativeInt


class EvalProbeDefinition(DataModel):
    id: Identifier
    name: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    accounts: list[SyntheticCardAccount] = Field(min_length=2, max_length=8)
    purchase: Purchase
    notes: list[ShortText] = Field(default_factory=list)

    @model_validator(mode="after")
    def account_products_are_unique(self) -> EvalProbeDefinition:
        ids = [account.product_id for account in self.accounts]
        if len(ids) != len(set(ids)):
            raise ValueError("probe account product IDs must be unique")
        return self


class EvalProbeDocument(DataModel):
    schema_version: Literal["1.0"] = "1.0"
    document_type: Literal["eval_probes"] = "eval_probes"
    synthetic: Literal[True] = True
    probes: list[EvalProbeDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def probe_ids_are_unique(self) -> EvalProbeDocument:
        ids = [probe.id for probe in self.probes]
        if len(ids) != len(set(ids)):
            raise ValueError("eval probe IDs must be unique")
        return self


class LoadedEvalProbe(DataModel):
    id: Identifier
    name: str
    cards: list[Card]
    purchase: Purchase
    notes: list[str]
