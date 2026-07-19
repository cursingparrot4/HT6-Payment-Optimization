from __future__ import annotations

import asyncio
from datetime import date

import httpx

from engine.models import Goal
from intent.models import IntentCardContext, ProviderResponse
from intent.parser import parse_intent, parse_provider_response
from intent.providers import FreesoloIntentProvider, ProviderFailure


class BrokenProvider:
    name = "broken"
    model_id = "broken-model"

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse:
        del text, reference_date, card_context
        raise ProviderFailure("offline")


def response(raw: str, provider_name: str = "fixture") -> ProviderResponse:
    return ProviderResponse(
        raw_text=raw,
        provider_name=provider_name,
        model_id="test-model",
    )


def test_valid_json_is_normalized_into_engine_intent() -> None:
    result = parse_provider_response(
        response(
            """
            {
              "weights": {
                "max_cashback": 0,
                "max_travel": 0,
                "credit_health": 5,
                "hit_signup_bonus": 3,
                "max_cashflow": 1,
                "min_risk": 1
              },
              "constraints": {
                "max_utilization_bps": 3000,
                "max_utilization_until": "2026-10-18",
                "must_hit_bonus_card_ids": []
              }
            }
            """
        ),
        allow_fallback=False,
    )

    assert result.intent is not None
    assert result.source == "fixture"
    assert result.used_fallback is False
    assert result.valid_model_output is True
    assert result.intent.weights[Goal.CREDIT_HEALTH] == 0.5
    assert result.intent.constraints.max_utilization_bps == 3000
    assert any(warning.code == "weights_normalized" for warning in result.warnings)


def test_missing_goals_are_filled_with_zero_before_validation() -> None:
    result = parse_provider_response(
        response(
            """
            ```json
            {"weights":{"max_cashback":1},"constraints":{}}
            ```
            """
        ),
        allow_fallback=False,
    )

    assert result.intent is not None
    assert result.intent.weights[Goal.MAX_CASHBACK] == 1.0
    assert result.intent.weights[Goal.MAX_TRAVEL] == 0.0
    assert any(warning.code == "json_extracted_from_fence" for warning in result.warnings)
    assert any(warning.code == "missing_goal_filled_zero" for warning in result.warnings)


def test_unknown_constraint_rejects_output_without_fallback_when_disabled() -> None:
    result = parse_provider_response(
        response(
            """
            {
              "weights": {
                "max_cashback": 1,
                "max_travel": 1,
                "credit_health": 1,
                "hit_signup_bonus": 1,
                "max_cashflow": 1,
                "min_risk": 1
              },
              "constraints": {"recommended_card_id": "amex"}
            }
            """
        ),
        allow_fallback=False,
    )

    assert result.intent is None
    assert result.valid_model_output is False
    assert result.used_fallback is False
    assert any(warning.code == "unknown_constraint_rejected" for warning in result.warnings)


def test_provider_failure_uses_visible_equal_weight_fallback() -> None:
    result = asyncio.run(
        parse_intent(
            "whatever",
            date(2026, 7, 18),
            provider=BrokenProvider(),
            allow_fallback=True,
        )
    )

    assert result.intent is not None
    assert result.source == "fallback"
    assert result.used_fallback is True
    assert result.valid_model_output is False
    assert set(result.intent.weights) == set(Goal)
    assert any(warning.code == "provider_unavailable" for warning in result.warnings)
    assert any(warning.code == "fallback_equal_weights" for warning in result.warnings)


def test_freesolo_provider_accepts_openai_compatible_chat_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"weights":{"max_cashback":0,"max_travel":0,'
                                '"credit_health":4,"hit_signup_bonus":1,'
                                '"max_cashflow":0,"min_risk":0},'
                                '"constraints":{"max_utilization_bps":3000,'
                                '"max_utilization_until":"2026-10-18",'
                                '"must_hit_bonus_card_ids":[]}}'
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = FreesoloIntentProvider(
        api_key="test-key",
        base_url="https://freesolo.example",
        model_id="trained-intent",
        client=client,
    )
    result = asyncio.run(
        parse_intent(
            "keep my utilization under 30 percent for mortgage",
            date(2026, 7, 18),
            provider=provider,
            allow_fallback=False,
        )
    )
    asyncio.run(client.aclose())

    assert result.source == "freesolo"
    assert result.intent is not None
    assert result.provider_name == "freesolo"
    assert result.model_id == "trained-intent"
    assert result.intent.weights[Goal.CREDIT_HEALTH] == 0.8


def test_freesolo_provider_accepts_openai_v1_base_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://freesolo.example/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"weights":{"max_cashback":1,"max_travel":0,'
                                '"credit_health":0,"hit_signup_bonus":0,'
                                '"max_cashflow":0,"min_risk":0},'
                                '"constraints":{"max_utilization_bps":null,'
                                '"max_utilization_until":null,'
                                '"must_hit_bonus_card_ids":[]}}'
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = FreesoloIntentProvider(
        api_key="test-key",
        base_url="https://freesolo.example/v1",
        model_id="trained-intent",
        client=client,
    )
    result = asyncio.run(
        parse_intent(
            "cashback",
            date(2026, 7, 18),
            provider=provider,
            allow_fallback=False,
        )
    )
    asyncio.run(client.aclose())

    assert result.source == "freesolo"
    assert result.intent is not None
    assert result.intent.weights[Goal.MAX_CASHBACK] == 1.0
