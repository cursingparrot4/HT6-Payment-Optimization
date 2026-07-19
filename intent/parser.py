"""Strict JSON extraction, validation, normalization, and runtime fallback."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from pydantic import ValidationError

from engine.models import Constraint, Goal, Intent
from intent.models import (
    IntentCardContext,
    IntentSource,
    ParseIntentResult,
    ParseWarning,
    ParseWarningCode,
    ProviderResponse,
)
from intent.providers import (
    FixtureIntentProvider,
    FreesoloIntentProvider,
    GeminiIntentProvider,
    IntentProvider,
    ProviderFailure,
)

_FENCED_JSON = re.compile(r"^\s*```(?:json|JSON)?\s*(\{.*\})\s*```\s*$", re.DOTALL)
_ALLOWED_ROOT_KEYS = {"weights", "constraints"}
_ALLOWED_CONSTRAINT_KEYS = {
    "max_utilization_bps",
    "max_utilization_until",
    "must_hit_bonus_card_ids",
}


async def parse_intent(
    text: str,
    reference_date: date,
    card_context: tuple[IntentCardContext, ...] = (),
    *,
    provider: IntentProvider | None = None,
    allow_fallback: bool = True,
) -> ParseIntentResult:
    active_provider = provider or _default_provider()
    warnings: list[ParseWarning] = []
    try:
        response = await active_provider.generate_intent(text, reference_date, card_context)
    except ProviderFailure as exc:
        warnings.append(
            ParseWarning(
                code=(
                    ParseWarningCode.PROVIDER_TIMEOUT
                    if exc.timeout
                    else ParseWarningCode.PROVIDER_UNAVAILABLE
                ),
                message=str(exc),
            )
        )
        return _fallback_result(warnings) if allow_fallback else _failed_result(warnings)

    return parse_provider_response(
        response,
        card_context=card_context,
        allow_fallback=allow_fallback,
    )


def parse_provider_response(
    response: ProviderResponse,
    *,
    card_context: tuple[IntentCardContext, ...] = (),
    allow_fallback: bool,
) -> ParseIntentResult:
    warnings: list[ParseWarning] = []
    try:
        extracted = _extract_json_text(response.raw_text, warnings)
        payload = json.loads(extracted)
        normalized = _normalize_payload(payload, card_context, warnings)
        intent = Intent.model_validate(normalized)
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        warnings.append(
            ParseWarning(
                code=(
                    ParseWarningCode.INVALID_JSON
                    if isinstance(exc, json.JSONDecodeError)
                    else ParseWarningCode.SCHEMA_VALIDATION_FAILED
                ),
                message=str(exc)[:500],
            )
        )
        if allow_fallback:
            return _fallback_result(
                warnings,
                provider_name=response.provider_name,
                model_id=response.model_id,
                raw_output_available=bool(response.raw_text),
            )
        return ParseIntentResult(
            intent=None,
            source=_source_for_provider(response.provider_name),
            provider_name=response.provider_name,
            model_id=response.model_id,
            used_fallback=False,
            valid_model_output=False,
            warnings=warnings,
            raw_output_available=bool(response.raw_text),
        )

    if _weights_were_normalized(normalized["weights"], intent):
        warnings.append(
            ParseWarning(
                code=ParseWarningCode.WEIGHTS_NORMALIZED,
                message="Intent weights were normalized before optimization.",
            )
        )
    return ParseIntentResult(
        intent=intent,
        source=_source_for_provider(response.provider_name),
        provider_name=response.provider_name,
        model_id=response.model_id,
        used_fallback=False,
        valid_model_output=True,
        warnings=warnings,
        raw_output_available=bool(response.raw_text),
    )


def _default_provider() -> IntentProvider:
    freesolo = FreesoloIntentProvider()
    if freesolo.api_key and freesolo.base_url:
        return freesolo
    gemini = GeminiIntentProvider()
    if gemini.api_key:
        return gemini
    return FixtureIntentProvider()


def _extract_json_text(raw_text: str, warnings: list[ParseWarning]) -> str:
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("model output is empty")
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = _FENCED_JSON.match(stripped)
    if match:
        warnings.append(
            ParseWarning(
                code=ParseWarningCode.JSON_EXTRACTED_FROM_FENCE,
                message="JSON object was extracted from a Markdown JSON fence.",
            )
        )
        return match.group(1).strip()
    raise ValueError("model output must be a single JSON object")


def _normalize_payload(
    payload: Any,
    card_context: tuple[IntentCardContext, ...],
    warnings: list[ParseWarning],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("intent output root must be an object")
    unknown_root = sorted(set(payload) - _ALLOWED_ROOT_KEYS)
    if unknown_root:
        raise ValueError(f"unknown root keys: {unknown_root}")

    weights = payload.get("weights")
    if not isinstance(weights, Mapping):
        raise ValueError("weights must be an object")
    normalized_weights: dict[str, Any] = {}
    for raw_goal, value in weights.items():
        try:
            goal = Goal(raw_goal)
        except (TypeError, ValueError) as exc:
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.UNKNOWN_GOAL_REJECTED,
                    message=f"Unknown goal was rejected: {raw_goal!r}.",
                )
            )
            raise ValueError(f"unknown goal: {raw_goal!r}") from exc
        normalized_weights[goal.value] = value
    for goal in Goal:
        if goal.value not in normalized_weights:
            normalized_weights[goal.value] = 0
            warnings.append(
                ParseWarning(
                    code=ParseWarningCode.MISSING_GOAL_FILLED_ZERO,
                    message=f"Missing goal {goal.value} was filled with zero.",
                )
            )

    constraints = payload.get("constraints") or {}
    if not isinstance(constraints, Mapping):
        raise ValueError("constraints must be an object")
    unknown_constraints = sorted(set(constraints) - _ALLOWED_CONSTRAINT_KEYS)
    if unknown_constraints:
        warnings.append(
            ParseWarning(
                code=ParseWarningCode.UNKNOWN_CONSTRAINT_REJECTED,
                message=f"Unknown constraints were rejected: {unknown_constraints}.",
            )
        )
        raise ValueError(f"unknown constraint keys: {unknown_constraints}")

    normalized_constraints = {
        "max_utilization_bps": constraints.get("max_utilization_bps"),
        "max_utilization_until": constraints.get("max_utilization_until"),
        "must_hit_bonus_card_ids": list(constraints.get("must_hit_bonus_card_ids") or []),
    }
    allowed_bonus_ids = {card.id for card in card_context if card.has_active_bonus}
    invalid_bonus_ids = sorted(
        set(normalized_constraints["must_hit_bonus_card_ids"]) - allowed_bonus_ids
    )
    if invalid_bonus_ids:
        raise ValueError(f"unknown or inactive bonus card IDs: {invalid_bonus_ids}")

    return {
        "weights": normalized_weights,
        "constraints": Constraint.model_validate(normalized_constraints).model_dump(mode="json"),
    }


def _weights_were_normalized(raw_weights: Mapping[str, Any], intent: Intent) -> bool:
    raw_sum = 0.0
    for value in raw_weights.values():
        if isinstance(value, bool) or not isinstance(value, int | float):
            return False
        raw_sum += float(value)
    if raw_sum <= 0:
        return False
    normalized = {goal.value: intent.weights[goal] for goal in Goal}
    return any(abs(float(raw_weights[goal]) - normalized[goal]) > 1e-9 for goal in normalized)


def _fallback_result(
    warnings: list[ParseWarning],
    *,
    provider_name: str | None = None,
    model_id: str | None = None,
    raw_output_available: bool = False,
) -> ParseIntentResult:
    return ParseIntentResult(
        intent=Intent.equal_weights(),
        source=IntentSource.FALLBACK,
        provider_name=provider_name,
        model_id=model_id,
        used_fallback=True,
        valid_model_output=False,
        warnings=[
            *warnings,
            ParseWarning(
                code=ParseWarningCode.FALLBACK_EQUAL_WEIGHTS,
                message="Using equal weights with no hard constraints because parsing failed.",
            ),
        ],
        raw_output_available=raw_output_available,
    )


def _failed_result(warnings: list[ParseWarning]) -> ParseIntentResult:
    return ParseIntentResult(
        intent=None,
        source=IntentSource.FALLBACK,
        used_fallback=False,
        valid_model_output=False,
        warnings=warnings,
        raw_output_available=False,
    )


def _source_for_provider(provider_name: str) -> IntentSource:
    if provider_name == IntentSource.GEMINI.value:
        return IntentSource.GEMINI
    if provider_name == IntentSource.FREESOLO.value:
        return IntentSource.FREESOLO
    if provider_name == IntentSource.FIXTURE.value:
        return IntentSource.FIXTURE
    return IntentSource.PROMPTED
