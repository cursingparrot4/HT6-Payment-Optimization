"""Provider protocols and model adapters for intent extraction."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date
from time import monotonic
from typing import Protocol

import httpx

from intent.models import IntentCardContext, ProviderResponse, safe_metadata
from intent.prompts import INTENT_PROMPT_VERSION, render_intent_prompt


class ProviderFailure(RuntimeError):
    def __init__(self, message: str, *, timeout: bool = False) -> None:
        self.timeout = timeout
        super().__init__(message)


class IntentProvider(Protocol):
    name: str
    model_id: str

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse: ...


class FixtureIntentProvider:
    """Deterministic demo/test provider; visibly labeled as fixture, never AI."""

    name = "fixture"
    model_id = "local-demo-fixture"

    _examples: Mapping[str, str] = {
        "mortgage_low_utilization_bonus": (
            '{"weights":{"max_cashback":0.05,"max_travel":0.1,"credit_health":0.5,'
            '"hit_signup_bonus":0.25,"max_cashflow":0.05,"min_risk":0.05},'
            '"constraints":{"max_utilization_bps":3000,'
            '"max_utilization_until":"2026-10-18","must_hit_bonus_card_ids":[]}}'
        ),
        "travel_rewards": (
            '{"weights":{"max_cashback":0.05,"max_travel":0.75,"credit_health":0.05,'
            '"hit_signup_bonus":0.05,"max_cashflow":0.05,"min_risk":0.05},'
            '"constraints":{"max_utilization_bps":null,'
            '"max_utilization_until":null,"must_hit_bonus_card_ids":[]}}'
        ),
        "cashback": (
            '{"weights":{"max_cashback":0.75,"max_travel":0.05,"credit_health":0.05,'
            '"hit_signup_bonus":0.05,"max_cashflow":0.05,"min_risk":0.05},'
            '"constraints":{"max_utilization_bps":null,'
            '"max_utilization_until":null,"must_hit_bonus_card_ids":[]}}'
        ),
    }

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse:
        del reference_date, card_context
        normalized = " ".join(text.lower().split())
        if "mortgage" in normalized and ("bonus" in normalized or "spend" in normalized):
            raw = self._examples["mortgage_low_utilization_bonus"]
        elif "travel" in normalized or "points" in normalized or "miles" in normalized:
            raw = self._examples["travel_rewards"]
        elif "cashback" in normalized or "cash back" in normalized or "cash" in normalized:
            raw = self._examples["cashback"]
        else:
            raise ProviderFailure("No fixture intent matches this text.")
        return ProviderResponse(
            raw_text=raw,
            provider_name=self.name,
            model_id=self.model_id,
            response_format="text",
            cached=True,
            metadata={"prompt_version": INTENT_PROMPT_VERSION},
        )


class GeminiIntentProvider:
    """Gemini REST adapter using structured JSON output."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_id = model_id or os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self._client = client
        self.timeout_seconds = timeout_seconds

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse:
        if not self.api_key:
            raise ProviderFailure("GEMINI_API_KEY is not configured.")

        prompt = render_intent_prompt(text, reference_date, card_context)
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_id}:generateContent"
        )
        started = monotonic()
        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.post(
                url,
                headers={
                    "x-goog-api-key": self.api_key,
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderFailure("Gemini request timed out.", timeout=True) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderFailure(f"Gemini request failed: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()

        raw_text = _extract_gemini_text(body)
        latency_ms = int((monotonic() - started) * 1000)
        return ProviderResponse(
            raw_text=raw_text,
            provider_name=self.name,
            model_id=self.model_id,
            latency_ms=latency_ms,
            response_format="json_schema",
            cached=False,
            metadata=safe_metadata(
                {
                    "prompt_version": INTENT_PROMPT_VERSION,
                    "candidate_count": len(body.get("candidates", []))
                    if isinstance(body, dict)
                    else 0,
                }
            ),
        )


def _extract_gemini_text(body: object) -> str:
    if not isinstance(body, dict):
        raise ProviderFailure("Gemini returned a non-object response.")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ProviderFailure("Gemini returned no candidates.")
    first = candidates[0]
    if not isinstance(first, dict):
        raise ProviderFailure("Gemini candidate has an invalid shape.")
    content = first.get("content")
    if not isinstance(content, dict):
        raise ProviderFailure("Gemini candidate has no content.")
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ProviderFailure("Gemini content has no parts.")
    texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    raw_text = "".join(text for text in texts if isinstance(text, str)).strip()
    if not raw_text:
        raise ProviderFailure("Gemini returned empty text.")
    return raw_text


class FreesoloIntentProvider:
    """Environment-configured adapter for a trained Freesolo intent model.

    The project plan treats Freesolo as the trained SLM path, but the public
    endpoint contract is not committed in this repo. This adapter assumes an
    OpenAI-compatible chat-completions surface unless env vars point elsewhere.
    """

    name = "freesolo"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model_id: str | None = None,
        path: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("FREESOLO_API_KEY")
        self.base_url = (base_url or os.getenv("FREESOLO_BASE_URL") or "").rstrip("/")
        self.model_id = model_id or os.getenv("FREESOLO_MODEL", "switchpay-intent-sft")
        self.path = path or os.getenv("FREESOLO_CHAT_COMPLETIONS_PATH", "/v1/chat/completions")
        self._client = client
        self.timeout_seconds = timeout_seconds

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse:
        if not self.api_key:
            raise ProviderFailure("FREESOLO_API_KEY is not configured.")
        if not self.base_url:
            raise ProviderFailure("FREESOLO_BASE_URL is not configured.")

        prompt = render_intent_prompt(text, reference_date, card_context)
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        url = _join_freesolo_url(self.base_url, self.path)
        started = monotonic()
        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.post(
                url,
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderFailure("Freesolo request timed out.", timeout=True) from exc
        except httpx.HTTPStatusError as exc:
            detail = _safe_error_text(exc.response)
            raise ProviderFailure(
                f"Freesolo request failed: HTTP {exc.response.status_code}. {detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderFailure(f"Freesolo request failed: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()

        raw_text = _extract_freesolo_text(body)
        latency_ms = int((monotonic() - started) * 1000)
        return ProviderResponse(
            raw_text=raw_text,
            provider_name=self.name,
            model_id=self.model_id,
            latency_ms=latency_ms,
            response_format="json_schema",
            cached=False,
            metadata=safe_metadata(
                {
                    "prompt_version": INTENT_PROMPT_VERSION,
                    "endpoint_configured": True,
                }
            ),
        )


def _extract_freesolo_text(body: object) -> str:
    if not isinstance(body, dict):
        raise ProviderFailure("Freesolo returned a non-object response.")

    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
            if isinstance(first.get("text"), str):
                return first["text"].strip()

    if isinstance(body.get("output_text"), str):
        return body["output_text"].strip()
    data = body.get("data")
    if isinstance(data, dict) and isinstance(data.get("answer"), str):
        return data["answer"].strip()

    raise ProviderFailure("Freesolo response did not contain model text.")


def _join_freesolo_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    if base_url.endswith("/v1") and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path.removeprefix("/v1")
    return f"{base_url}{normalized_path}"


def _safe_error_text(response: httpx.Response) -> str:
    text = response.text.strip().replace("\n", " ")
    return text[:300]
