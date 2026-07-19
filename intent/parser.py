"""Strict provider-output parsing, validation, and visible runtime fallback."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from engine.models import Constraint, Goal, Intent
from intent.models import (
    IntentCardContext,
    IntentSource,
    ParsedIntentOutput,
    ParseIntentResult,
    ParseWarning,
    ParseWarningCode,
)
from intent.providers import (
    IntentProvider,
    IntentProviderError,
    IntentProviderTimeoutError,
    IntentProviderUnavailableError,
)

_JSON_FENCE = re.compile(r"\A```(?:json)?\s*\n?(.*?)\n?```\s*\Z", re.DOTALL | re.IGNORECASE)
_ROOT_FIELDS = {"weights", "constraints"}
_CONSTRAINT_FIELDS = {
    "max_utilization_bps",
    "max_utilization_until",
    "must_hit_bonus_card_ids",
}


class IntentParseError(ValueError):
    def __init__(self, warning: ParseWarning) -> None:
        self.warning = warning
        super().__init__(warning.message)


def _warning(code: ParseWarningCode, message: str) -> ParseWarning:
    return ParseWarning(code=code, message=message)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON numeric constant is not allowed: {value}")


def _parse_object(text: str) -> tuple[dict[str, Any], list[ParseWarning]]:
    trimmed = text.strip()
    if not trimmed:
        raise IntentParseError(
            _warning(ParseWarningCode.INVALID_JSON, "Provider returned an empty response.")
        )
    warnings: list[ParseWarning] = []
    candidate = trimmed
    try:
        parsed = json.loads(candidate, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError):
        fence = _JSON_FENCE.fullmatch(trimmed)
        if fence is None:
            raise IntentParseError(
                _warning(
                    ParseWarningCode.INVALID_JSON,
                    "Provider output must contain exactly one JSON object and no prose.",
                )
            ) from None
        candidate = fence.group(1).strip()
        warnings.append(
            _warning(
                ParseWarningCode.JSON_EXTRACTED_FROM_FENCE,
                "JSON was extracted from one Markdown fence.",
            )
        )
        try:
            parsed = json.loads(candidate, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise IntentParseError(
                _warning(ParseWarningCode.INVALID_JSON, f"Invalid JSON: {exc}")
            ) from exc
    if not isinstance(parsed, dict):
        raise IntentParseError(
            _warning(ParseWarningCode.INVALID_JSON, "Intent JSON root must be an object.")
        )
    return parsed, warnings


def _validate_root_fields(payload: Mapping[str, Any]) -> None:
    fields = set(payload)
    if fields == _ROOT_FIELDS:
        return
    unknown = sorted(fields - _ROOT_FIELDS)
    missing = sorted(_ROOT_FIELDS - fields)
    detail = f"unknown={unknown}, missing={missing}"
    raise IntentParseError(
        _warning(
            ParseWarningCode.SCHEMA_VALIDATION_FAILED,
            f"Intent JSON root fields are invalid: {detail}.",
        )
    )


def _normalize_weights(
    raw_weights: Any,
) -> tuple[dict[str, int | float | Decimal], list[ParseWarning]]:
    if not isinstance(raw_weights, dict):
        raise IntentParseError(
            _warning(ParseWarningCode.SCHEMA_VALIDATION_FAILED, "weights must be an object.")
        )
    warnings: list[ParseWarning] = []
    known = {goal.value for goal in Goal}
    unknown = sorted(set(raw_weights) - known)
    if unknown:
        raise IntentParseError(
            _warning(
                ParseWarningCode.UNKNOWN_GOAL_REJECTED,
                f"Unknown goal keys were rejected: {unknown}.",
            )
        )
    weights: dict[str, int | float | Decimal] = dict(raw_weights)
    missing = [goal.value for goal in Goal if goal.value not in weights]
    for goal in missing:
        weights[goal] = 0
    if missing:
        warnings.append(
            _warning(
                ParseWarningCode.MISSING_GOAL_FILLED_ZERO,
                f"Missing goal keys were filled with zero: {missing}.",
            )
        )
    decimals: list[Decimal] = []
    for goal, raw_value in weights.items():
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float, Decimal)):
            raise IntentParseError(
                _warning(
                    ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                    f"Weight for {goal} must be a JSON number.",
                )
            )
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError) as exc:
            raise IntentParseError(
                _warning(
                    ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                    f"Weight for {goal} is invalid.",
                )
            ) from exc
        if not value.is_finite() or value < 0:
            raise IntentParseError(
                _warning(
                    ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                    f"Weight for {goal} must be finite and nonnegative.",
                )
            )
        decimals.append(value)
    total = sum(decimals, start=Decimal(0))
    if total <= 0:
        raise IntentParseError(
            _warning(
                ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                "At least one intent weight must be positive.",
            )
        )
    if total != Decimal(1):
        warnings.append(
            _warning(
                ParseWarningCode.WEIGHTS_NORMALIZED,
                f"Weights were normalized from a total of {total}.",
            )
        )
    return weights, warnings


def _validate_constraints(
    raw_constraints: Any,
    card_context: Sequence[IntentCardContext],
) -> dict[str, Any]:
    if not isinstance(raw_constraints, dict):
        raise IntentParseError(
            _warning(
                ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                "constraints must be an object.",
            )
        )
    unknown = sorted(set(raw_constraints) - _CONSTRAINT_FIELDS)
    if unknown:
        raise IntentParseError(
            _warning(
                ParseWarningCode.UNKNOWN_CONSTRAINT_REJECTED,
                f"Unknown constraint fields were rejected: {unknown}.",
            )
        )
    constraints = {
        "max_utilization_bps": raw_constraints.get("max_utilization_bps"),
        "max_utilization_until": raw_constraints.get("max_utilization_until"),
        "must_hit_bonus_card_ids": raw_constraints.get("must_hit_bonus_card_ids", []),
    }
    cards_by_id: dict[str, IntentCardContext] = {}
    for card in card_context:
        if card.id in cards_by_id:
            raise ValueError(f"duplicate card context ID: {card.id}")
        cards_by_id[card.id] = card
    forced = constraints["must_hit_bonus_card_ids"]
    if not isinstance(forced, list) or any(not isinstance(card_id, str) for card_id in forced):
        raise IntentParseError(
            _warning(
                ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                "must_hit_bonus_card_ids must be a list of card IDs.",
            )
        )
    for card_id in forced:
        card = cards_by_id.get(card_id)
        if card is None or not card.has_active_bonus:
            raise IntentParseError(
                _warning(
                    ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                    f"Forced bonus card {card_id!r} is unavailable or has no active bonus.",
                )
            )
    return constraints


def parse_provider_output(
    raw_text: str,
    card_context: Sequence[IntentCardContext],
) -> ParsedIntentOutput:
    payload, warnings = _parse_object(raw_text)
    _validate_root_fields(payload)
    weights, weight_warnings = _normalize_weights(payload["weights"])
    warnings.extend(weight_warnings)
    constraints = _validate_constraints(payload["constraints"], card_context)
    try:
        intent = Intent(weights=weights, constraints=Constraint.model_validate(constraints))
    except ValidationError as exc:
        raise IntentParseError(
            _warning(
                ParseWarningCode.SCHEMA_VALIDATION_FAILED,
                f"Intent schema validation failed: {exc.errors(include_url=False)}",
            )
        ) from exc
    return ParsedIntentOutput(intent=intent, warnings=warnings)


def _failure_result(
    provider: IntentProvider,
    warning: ParseWarning,
    *,
    allow_fallback: bool,
    raw_output_available: bool,
) -> ParseIntentResult:
    if allow_fallback:
        return ParseIntentResult(
            intent=Intent.equal_weights(),
            source=IntentSource.FALLBACK,
            provider_name=provider.name,
            model_id=provider.model_id,
            used_fallback=True,
            valid_model_output=False,
            warnings=[
                warning,
                _warning(
                    ParseWarningCode.FALLBACK_EQUAL_WEIGHTS,
                    "Equal weights and no hard constraints were used as a visible fallback.",
                ),
            ],
            raw_output_available=raw_output_available,
        )
    return ParseIntentResult(
        intent=None,
        source=provider.source,
        provider_name=provider.name,
        model_id=provider.model_id,
        used_fallback=False,
        valid_model_output=False,
        warnings=[warning],
        raw_output_available=raw_output_available,
    )


async def parse_intent(
    text: str,
    reference_date: date,
    card_context: tuple[IntentCardContext, ...],
    provider: IntentProvider,
    *,
    allow_fallback: bool,
) -> ParseIntentResult:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("intent text must be a nonempty string")
    if len({card.id for card in card_context}) != len(card_context):
        raise ValueError("card context IDs must be unique")
    try:
        response = await provider.generate_intent(text, reference_date, card_context)
    except asyncio.CancelledError:
        raise
    except (IntentProviderTimeoutError, TimeoutError) as exc:
        return _failure_result(
            provider,
            _warning(ParseWarningCode.PROVIDER_TIMEOUT, f"Intent provider timed out: {exc}"),
            allow_fallback=allow_fallback,
            raw_output_available=False,
        )
    except IntentProviderUnavailableError as exc:
        return _failure_result(
            provider,
            _warning(
                ParseWarningCode.PROVIDER_UNAVAILABLE,
                f"Intent provider is unavailable: {exc}",
            ),
            allow_fallback=allow_fallback,
            raw_output_available=False,
        )
    except IntentProviderError as exc:
        return _failure_result(
            provider,
            _warning(
                ParseWarningCode.PROVIDER_UNAVAILABLE,
                f"Intent provider failed: {exc}",
            ),
            allow_fallback=allow_fallback,
            raw_output_available=False,
        )
    except Exception as exc:
        return _failure_result(
            provider,
            _warning(
                ParseWarningCode.PROVIDER_UNAVAILABLE,
                f"Intent provider failed with {type(exc).__name__}.",
            ),
            allow_fallback=allow_fallback,
            raw_output_available=False,
        )
    if response.provider_name != provider.name or response.model_id != provider.model_id:
        return _failure_result(
            provider,
            _warning(
                ParseWarningCode.PROVIDER_UNAVAILABLE,
                "Intent provider response identity did not match the configured provider.",
            ),
            allow_fallback=allow_fallback,
            raw_output_available=True,
        )
    try:
        parsed = parse_provider_output(response.raw_text, card_context)
    except IntentParseError as exc:
        return _failure_result(
            provider,
            exc.warning,
            allow_fallback=allow_fallback,
            raw_output_available=True,
        )
    return ParseIntentResult(
        intent=parsed.intent,
        source=provider.source,
        provider_name=response.provider_name,
        model_id=response.model_id,
        used_fallback=False,
        valid_model_output=True,
        warnings=parsed.warnings,
        raw_output_available=True,
    )
