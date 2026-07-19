"""Intent parser provider and result contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from engine.models import Intent


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class IntentSource(StrEnum):
    FREESOLO = "freesolo"
    GEMINI = "gemini"
    PROMPTED = "prompted"
    FIXTURE = "fixture"
    FALLBACK = "fallback"


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
    message: Annotated[str, Field(strict=True, min_length=1, max_length=500)]


class IntentCardContext(IntentModel):
    id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    has_active_bonus: StrictBool = False


class ProviderResponse(IntentModel):
    raw_text: str
    provider_name: Annotated[str, Field(strict=True, min_length=1, max_length=80)]
    model_id: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    latency_ms: StrictInt | None = Field(default=None, ge=0)
    response_format: Literal["text", "json_schema"] = "text"
    cached: StrictBool = False
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class ParseIntentResult(IntentModel):
    intent: Intent | None
    source: IntentSource
    provider_name: str | None = None
    model_id: str | None = None
    used_fallback: StrictBool
    valid_model_output: StrictBool
    warnings: list[ParseWarning] = Field(default_factory=list)
    raw_output_available: StrictBool = False


def safe_metadata(value: dict[str, Any]) -> dict[str, str | int | bool]:
    """Keep only small, non-secret scalar metadata from provider responses."""

    safe: dict[str, str | int | bool] = {}
    for key, item in value.items():
        if isinstance(item, bool | int | str):
            safe[key[:80]] = item if not isinstance(item, str) else item[:200]
    return safe
