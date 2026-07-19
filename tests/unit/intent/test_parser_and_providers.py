from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from pydantic import ValidationError

from engine.models import Card, Goal, SignupBonus
from intent.context import build_intent_card_context
from intent.models import (
    IntentCardContext,
    IntentSource,
    ParseIntentResult,
    ParseWarningCode,
    ProviderResponse,
)
from intent.parser import IntentParseError, parse_intent, parse_provider_output
from intent.prompts import INTENT_PROMPT_VERSION, build_intent_system_prompt
from intent.providers import (
    FixtureIntentProvider,
    IntentProviderError,
    IntentProviderTimeoutError,
    IntentProviderUnavailableError,
)


def contexts() -> tuple[IntentCardContext, ...]:
    return (
        IntentCardContext(
            id="amex-gold-rewards",
            name="American Express Gold Rewards Card",
            has_active_bonus=True,
        ),
        IntentCardContext(
            id="rbc-avion-visa-infinite",
            name="RBC Avion Visa Infinite",
            has_active_bonus=False,
        ),
    )


def valid_payload(**overrides) -> dict:
    payload = {
        "weights": {
            "max_cashback": 0.1,
            "max_travel": 0.1,
            "credit_health": 0.4,
            "hit_signup_bonus": 0.3,
            "max_cashflow": 0.05,
            "min_risk": 0.05,
        },
        "constraints": {
            "max_utilization_bps": 3000,
            "max_utilization_until": "2026-10-18",
            "must_hit_bonus_card_ids": ["amex-gold-rewards"],
        },
    }
    payload.update(overrides)
    return payload


def run_parse(provider, *, allow_fallback: bool = False, text: str = "goal"):
    return asyncio.run(
        parse_intent(
            text,
            date(2026, 7, 18),
            contexts(),
            provider,
            allow_fallback=allow_fallback,
        )
    )


def test_exact_json_parses_and_provider_receives_minimal_context() -> None:
    provider = FixtureIntentProvider({"goal": json.dumps(valid_payload())})

    result = run_parse(provider)

    assert result.valid_model_output is True
    assert result.used_fallback is False
    assert result.source is IntentSource.FIXTURE
    assert result.intent.constraints.max_utilization_bps == 3000
    assert result.intent.constraints.must_hit_bonus_card_ids == ["amex-gold-rewards"]
    assert sum(result.intent.weights.values()) == pytest.approx(1.0)
    assert provider.calls == [("goal", date(2026, 7, 18), contexts())]


def test_one_json_fence_is_accepted_with_warning_but_prose_is_rejected() -> None:
    fenced = parse_provider_output(
        "```json\n" + json.dumps(valid_payload()) + "\n```",
        contexts(),
    )
    assert fenced.warnings[0].code is ParseWarningCode.JSON_EXTRACTED_FROM_FENCE

    with pytest.raises(IntentParseError) as error:
        parse_provider_output("Here is JSON: " + json.dumps(valid_payload()), contexts())
    assert error.value.warning.code is ParseWarningCode.INVALID_JSON

    with pytest.raises(IntentParseError) as multiple:
        parse_provider_output(
            json.dumps(valid_payload()) + json.dumps(valid_payload()),
            contexts(),
        )
    assert multiple.value.warning.code is ParseWarningCode.INVALID_JSON


def test_missing_goals_are_filled_and_nonunit_weights_are_normalized() -> None:
    payload = valid_payload(
        weights={
            "credit_health": 4,
            "hit_signup_bonus": 2,
        }
    )
    parsed = parse_provider_output(json.dumps(payload), contexts())

    assert parsed.intent.weights[Goal.CREDIT_HEALTH] == pytest.approx(2 / 3)
    assert parsed.intent.weights[Goal.MAX_CASHBACK] == 0
    assert [warning.code for warning in parsed.warnings] == [
        ParseWarningCode.MISSING_GOAL_FILLED_ZERO,
        ParseWarningCode.WEIGHTS_NORMALIZED,
    ]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda payload: payload["weights"].update({"unknown_goal": 1}),
            ParseWarningCode.UNKNOWN_GOAL_REJECTED,
        ),
        (
            lambda payload: payload["constraints"].update({"dont_use_card_ids": []}),
            ParseWarningCode.UNKNOWN_CONSTRAINT_REJECTED,
        ),
        (
            lambda payload: payload["weights"].update({"max_cashback": float("nan")}),
            ParseWarningCode.INVALID_JSON,
        ),
        (
            lambda payload: payload["weights"].update({goal.value: 0 for goal in Goal}),
            ParseWarningCode.SCHEMA_VALIDATION_FAILED,
        ),
        (
            lambda payload: payload["constraints"].update({"max_utilization_until": "tomorrow"}),
            ParseWarningCode.SCHEMA_VALIDATION_FAILED,
        ),
        (
            lambda payload: payload["constraints"].update(
                {"must_hit_bonus_card_ids": ["rbc-avion-visa-infinite"]}
            ),
            ParseWarningCode.SCHEMA_VALIDATION_FAILED,
        ),
    ],
)
def test_invalid_schema_and_numbers_fail_closed(mutation, expected) -> None:
    payload = valid_payload()
    mutation(payload)
    raw = json.dumps(payload)
    if expected is ParseWarningCode.INVALID_JSON:
        raw = raw.replace("NaN", "NaN")

    with pytest.raises(IntentParseError) as error:
        parse_provider_output(raw, contexts())
    assert error.value.warning.code is expected


def test_provider_failures_and_invalid_output_have_visible_optional_fallback() -> None:
    unavailable = FixtureIntentProvider({"goal": IntentProviderUnavailableError("offline")})
    fallback = run_parse(unavailable, allow_fallback=True)

    assert fallback.source is IntentSource.FALLBACK
    assert fallback.used_fallback is True
    assert fallback.valid_model_output is False
    assert fallback.intent.constraints.must_hit_bonus_card_ids == []
    assert all(weight == pytest.approx(1 / 6) for weight in fallback.intent.weights.values())
    assert [warning.code for warning in fallback.warnings] == [
        ParseWarningCode.PROVIDER_UNAVAILABLE,
        ParseWarningCode.FALLBACK_EQUAL_WEIGHTS,
    ]

    malformed = FixtureIntentProvider({"goal": "not-json"})
    failed = run_parse(malformed, allow_fallback=False)
    assert failed.intent is None
    assert failed.source is IntentSource.FIXTURE
    assert failed.used_fallback is False
    assert failed.raw_output_available is True


def test_timeout_is_distinct_and_blank_input_never_calls_provider() -> None:
    provider = FixtureIntentProvider({"goal": IntentProviderTimeoutError("slow")})
    result = run_parse(provider, allow_fallback=False)
    assert result.warnings[0].code is ParseWarningCode.PROVIDER_TIMEOUT

    blank_provider = FixtureIntentProvider({"goal": json.dumps(valid_payload())})
    with pytest.raises(ValueError, match="nonempty"):
        run_parse(blank_provider, text="   ")
    assert blank_provider.calls == []


def test_native_timeout_and_generic_typed_provider_error_degrade_visibly() -> None:
    native_timeout = FixtureIntentProvider({"goal": TimeoutError("native timeout")})
    timeout_result = run_parse(native_timeout, allow_fallback=False)
    assert timeout_result.warnings[0].code is ParseWarningCode.PROVIDER_TIMEOUT

    generic = FixtureIntentProvider({"goal": IntentProviderError("provider error")})
    generic_result = run_parse(generic, allow_fallback=True)
    assert generic_result.used_fallback is True
    assert generic_result.warnings[0].code is ParseWarningCode.PROVIDER_UNAVAILABLE


def test_async_cancellation_propagates_instead_of_becoming_fallback() -> None:
    provider = FixtureIntentProvider({"goal": asyncio.CancelledError()})
    with pytest.raises(asyncio.CancelledError):
        run_parse(provider, allow_fallback=True)


def test_unexpected_provider_exception_uses_visible_fallback_without_details() -> None:
    provider = FixtureIntentProvider({"goal": KeyError("secret-value")})
    result = run_parse(provider, allow_fallback=True)

    assert result.used_fallback is True
    assert result.warnings[0].code is ParseWarningCode.PROVIDER_UNAVAILABLE
    assert "KeyError" in result.warnings[0].message
    assert "secret-value" not in result.warnings[0].message


def test_prompt_contains_contract_date_and_only_safe_card_context() -> None:
    prompt = build_intent_system_prompt(date(2026, 7, 18), contexts())

    assert INTENT_PROMPT_VERSION in prompt
    assert "2026-07-18" in prompt
    assert "max_utilization_bps=3000" in prompt
    assert "amex-gold-rewards" in prompt
    assert "credit_limit" not in prompt
    assert "current_balance" not in prompt


def test_repeated_fixture_parse_is_deterministic() -> None:
    raw = json.dumps(valid_payload(), sort_keys=True)
    first = run_parse(FixtureIntentProvider({"goal": raw}))
    second = run_parse(FixtureIntentProvider({"goal": raw}))
    assert first.model_dump() == second.model_dump()


def test_card_context_contains_only_safe_fields_and_active_bonus_state() -> None:
    active = Card(
        id="active",
        name="Active Bonus Card",
        credit_limit_cents=100_000,
        current_balance_cents=20_000,
        reward_rules=[],
        base_rate_bps=100,
        base_reward_type="cashback",
        point_value_millicents=1_000,
        annual_fee_cents=0,
        statement_day=10,
        due_day=5,
        signup_bonus=SignupBonus(
            spend_required_cents=10_000,
            spend_so_far_cents=0,
            reward_value_cents=5_000,
            deadline_date=date(2026, 8, 1),
        ),
    )
    expired = active.model_copy(
        update={
            "id": "expired",
            "signup_bonus": active.signup_bonus.model_copy(
                update={"deadline_date": date(2026, 7, 1)}
            ),
        }
    )

    context = build_intent_card_context([expired, active], date(2026, 7, 18))

    assert [card.id for card in context] == ["active", "expired"]
    assert context[0].has_active_bonus is True
    assert context[1].has_active_bonus is False
    assert set(context[0].model_dump()) == {"id", "name", "has_active_bonus"}


def test_provider_identity_mismatch_fails_attribution() -> None:
    class WrongIdentityProvider(FixtureIntentProvider):
        async def generate_intent(self, text, reference_date, card_context):
            response = await super().generate_intent(text, reference_date, card_context)
            return response.model_copy(update={"model_id": "different-model"})

    result = run_parse(
        WrongIdentityProvider({"goal": json.dumps(valid_payload())}),
        allow_fallback=False,
    )

    assert result.intent is None
    assert result.warnings[0].code is ParseWarningCode.PROVIDER_UNAVAILABLE


def test_provider_metadata_rejects_secret_or_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="unsupported keys"):
        ProviderResponse(
            raw_text="{}",
            provider_name="fixture",
            model_id="fixture-v1",
            metadata={"authorization": "secret"},
        )


def test_parse_result_rejects_inconsistent_fallback_state() -> None:
    with pytest.raises(ValidationError, match="fallback state"):
        ParseIntentResult(
            intent=None,
            source=IntentSource.FALLBACK,
            used_fallback=True,
            valid_model_output=False,
            raw_output_available=False,
        )
