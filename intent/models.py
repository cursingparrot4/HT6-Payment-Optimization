"""Provider, parser, and intent-data contracts owned by the language layer."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from engine.models import Constraint, Goal, Identifier, Intent

ShortText = Annotated[str, Field(strict=True, min_length=1, max_length=500)]
ModelIdentifier = Annotated[str, Field(strict=True, min_length=1, max_length=160)]
MetadataValue = StrictStr | StrictInt | StrictBool
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IntentSource(StrEnum):
    FREESOLO = "freesolo"
    PROMPTED = "prompted"
    FIXTURE = "fixture"
    FALLBACK = "fallback"


class ResponseFormat(StrEnum):
    TEXT = "text"
    JSON_SCHEMA = "json_schema"


class ParseWarningCode(StrEnum):
    JSON_EXTRACTED_FROM_FENCE = "json_extracted_from_fence"
    MISSING_GOAL_FILLED_ZERO = "missing_goal_filled_zero"
    WEIGHTS_NORMALIZED = "weights_normalized"
    UNKNOWN_GOAL_REJECTED = "unknown_goal_rejected"
    UNKNOWN_CONSTRAINT_REJECTED = "unknown_constraint_rejected"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    INVALID_JSON = "invalid_json"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    FALLBACK_EQUAL_WEIGHTS = "fallback_equal_weights"


class ParseWarning(IntentModel):
    code: ParseWarningCode
    message: ShortText


class IntentCardContext(IntentModel):
    id: Identifier
    name: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    has_active_bonus: StrictBool


class ProviderResponse(IntentModel):
    allowed_metadata: ClassVar[frozenset[str]] = frozenset(
        {
            "attempt_count",
            "finish_reason",
            "prompt_version",
            "request_id",
            "status_code",
        }
    )

    raw_text: Annotated[str, Field(strict=True, max_length=100_000)]
    provider_name: ModelIdentifier
    model_id: ModelIdentifier
    latency_ms: Annotated[int, Field(strict=True, ge=0)] | None = None
    response_format: ResponseFormat = ResponseFormat.TEXT
    cached: StrictBool = False
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_uses_safe_allowlist(
        cls, value: dict[str, MetadataValue]
    ) -> dict[str, MetadataValue]:
        unknown = sorted(set(value) - cls.allowed_metadata)
        if unknown:
            raise ValueError(f"provider metadata contains unsupported keys: {unknown}")
        return dict(sorted(value.items()))


class ParsedIntentOutput(IntentModel):
    intent: Intent
    warnings: list[ParseWarning] = Field(default_factory=list)


class ParseIntentResult(IntentModel):
    intent: Intent | None
    source: IntentSource
    provider_name: ModelIdentifier | None = None
    model_id: ModelIdentifier | None = None
    used_fallback: StrictBool
    valid_model_output: StrictBool
    warnings: list[ParseWarning] = Field(default_factory=list)
    raw_output_available: StrictBool

    @model_validator(mode="after")
    def parse_state_is_consistent(self) -> ParseIntentResult:
        if self.valid_model_output:
            if self.intent is None or self.used_fallback or self.source is IntentSource.FALLBACK:
                raise ValueError("valid model output requires a non-fallback intent")
            if not self.raw_output_available:
                raise ValueError("valid model output requires raw output availability")
        elif self.used_fallback:
            if self.intent is None or self.source is not IntentSource.FALLBACK:
                raise ValueError("fallback state requires fallback source and intent")
        elif self.intent is not None or self.source is IntentSource.FALLBACK:
            raise ValueError("failed non-fallback parse cannot contain an intent")
        return self


class SamplingRegime(StrEnum):
    BALANCED = "balanced"
    SPARSE = "sparse"
    TWO_GOAL = "two_goal"
    CONSTRAINT_HEAVY = "constraint_heavy"


class LanguageStyle(StrEnum):
    CONCISE = "concise"
    CONVERSATIONAL = "conversational"
    MESSY = "messy"
    EXPLICIT_PERCENTAGES = "explicit_percentages"
    NATURAL_DEADLINE = "natural_deadline"
    CONFLICTING_PREFERENCES = "conflicting_preferences"


class DatasetSplit(StrEnum):
    TRAIN = "train"
    TEST = "test"
    ADVERSARIAL_TEST = "adversarial_test"


class LatentIntent(IntentModel):
    latent_id: Identifier
    reference_date: date
    weights_ppm: dict[Goal, StrictNonNegativeInt]
    constraints: Constraint
    card_context: tuple[IntentCardContext, ...]
    regime: SamplingRegime
    styles: tuple[LanguageStyle, ...] = Field(min_length=1, max_length=3)

    @field_validator("weights_ppm", mode="before")
    @classmethod
    def require_all_goal_keys(cls, value):
        if not isinstance(value, dict):
            raise ValueError("weights_ppm must be a mapping")
        parsed = {}
        for raw_goal, weight in value.items():
            try:
                goal = raw_goal if isinstance(raw_goal, Goal) else Goal(raw_goal)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown goal: {raw_goal!r}") from exc
            if goal in parsed:
                raise ValueError(f"duplicate goal: {goal.value}")
            parsed[goal] = weight
        if set(parsed) != set(Goal):
            missing = sorted(goal.value for goal in set(Goal) - set(parsed))
            raise ValueError(f"weights_ppm must contain every goal; missing={missing}")
        return {goal: parsed[goal] for goal in Goal}

    @model_validator(mode="after")
    def latent_contract_is_consistent(self) -> LatentIntent:
        if sum(self.weights_ppm.values()) != 1_000_000:
            raise ValueError("weights_ppm must sum exactly to 1,000,000")
        if len(self.card_context) != len({card.id for card in self.card_context}):
            raise ValueError("latent card context IDs must be unique")
        valid_bonus_ids = {
            card.id for card in self.card_context if card.has_active_bonus
        }
        if not set(self.constraints.must_hit_bonus_card_ids).issubset(valid_bonus_ids):
            raise ValueError("forced bonus IDs must reference active bonus cards")
        return self


class SftMessage(IntentModel):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(strict=True, min_length=1, max_length=20_000)]


class SftMetadata(IntentModel):
    latent_id: Identifier
    split: DatasetSplit
    prompt_version: ModelIdentifier
    regime: SamplingRegime
    style: LanguageStyle
    provider_name: ModelIdentifier
    model_id: ModelIdentifier
    synthetic_fixture: StrictBool


class SftRecord(IntentModel):
    messages: list[SftMessage] = Field(min_length=3, max_length=3)
    metadata: SftMetadata

    @model_validator(mode="after")
    def messages_follow_training_order(self) -> SftRecord:
        roles = [message.role for message in self.messages]
        if roles != ["system", "user", "assistant"]:
            raise ValueError("SFT messages must be system, user, assistant")
        return self


class GenerationManifest(IntentModel):
    schema_version: Literal["1.0"] = "1.0"
    prompt_version: ModelIdentifier
    numpy_version: ModelIdentifier
    seed: StrictNonNegativeInt
    test_fraction_bps: Annotated[int, Field(strict=True, ge=1, le=9_999)]
    paraphrases_per_latent: Annotated[int, Field(strict=True, ge=1, le=3)]
    latent_count: StrictPositiveInt
    train_latent_count: StrictNonNegativeInt
    test_latent_count: StrictNonNegativeInt
    train_record_count: StrictNonNegativeInt
    test_record_count: StrictNonNegativeInt
    accepted_record_count: StrictNonNegativeInt
    rejected_response_count: StrictNonNegativeInt
    retry_count: StrictNonNegativeInt
    duplicate_description_count: StrictNonNegativeInt
    provider_name: ModelIdentifier
    model_id: ModelIdentifier
    synthetic_fixture: StrictBool
    production_claim_allowed: StrictBool
    train_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    test_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dataset_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def fixture_cannot_claim_production(self) -> GenerationManifest:
        if self.synthetic_fixture and self.production_claim_allowed:
            raise ValueError("fixture-generated datasets cannot support production claims")
        if self.train_latent_count + self.test_latent_count != self.latent_count:
            raise ValueError("latent split counts must reconcile")
        if self.train_record_count + self.test_record_count != self.accepted_record_count:
            raise ValueError("record split counts must reconcile")
        if self.production_claim_allowed and not 800 <= self.accepted_record_count <= 2_000:
            raise ValueError("production datasets require 800 to 2,000 accepted records")
        return self


class GeneratedDataset(IntentModel):
    train_records: list[SftRecord]
    test_records: list[SftRecord]
    manifest: GenerationManifest
