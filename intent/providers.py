"""Narrow provider protocols, deterministic offline fixtures, and live model adapters."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from time import monotonic
from typing import Protocol

import httpx

from intent.models import (
    IntentCardContext,
    IntentSource,
    ProviderResponse,
    ResponseFormat,
)
from intent.prompts import INTENT_PROMPT_VERSION, build_intent_system_prompt


class IntentProviderError(RuntimeError):
    """Base class for typed provider infrastructure failures."""


class IntentProviderUnavailableError(IntentProviderError):
    pass


class IntentProviderTimeoutError(IntentProviderError):
    pass


class IntentProvider(Protocol):
    name: str
    model_id: str
    source: IntentSource

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse: ...


class ParaphraseProvider(Protocol):
    name: str
    model_id: str
    synthetic_fixture: bool

    async def generate_paraphrases(self, latent, count: int) -> ProviderResponse: ...


@dataclass(slots=True)
class FixtureIntentProvider:
    responses: Mapping[str, str | BaseException]
    name: str = "fixture"
    model_id: str = "fixture-intent-v1"
    source: IntentSource = IntentSource.FIXTURE
    calls: list[tuple[str, date, tuple[IntentCardContext, ...]]] = field(
        default_factory=list,
        repr=False,
    )

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse:
        self.calls.append((text, reference_date, card_context))
        configured = self.responses.get(text)
        if configured is None:
            raise IntentProviderUnavailableError("no fixture response configured")
        if isinstance(configured, BaseException):
            raise configured
        return ProviderResponse(
            raw_text=configured,
            provider_name=self.name,
            model_id=self.model_id,
            response_format=ResponseFormat.TEXT,
            cached=True,
            metadata={"attempt_count": 1, "prompt_version": INTENT_PROMPT_VERSION},
        )


@dataclass(slots=True)
class FixtureParaphraseProvider:
    responses: Mapping[str, Sequence[str]]
    name: str = "fixture"
    model_id: str = "fixture-paraphrase-v1"
    synthetic_fixture: bool = True
    call_count: int = 0

    async def generate_paraphrases(self, latent, count: int) -> ProviderResponse:
        self.call_count += 1
        phrases = list(self.responses.get(latent.latent_id, ()))[:count]
        if not phrases:
            raise IntentProviderUnavailableError(
                f"no fixture paraphrases configured for {latent.latent_id}"
            )
        import json

        return ProviderResponse(
            raw_text=json.dumps(phrases, ensure_ascii=True),
            provider_name=self.name,
            model_id=self.model_id,
            response_format=ResponseFormat.TEXT,
            cached=True,
            metadata={"attempt_count": 1, "prompt_version": INTENT_PROMPT_VERSION},
        )


# ---------------------------------------------------------------- live model adapters
#
# The deterministic money path never trusts a model: parse_intent validates every field and
# falls back to visible equal weights on any failure. These adapters only turn a goal string
# into raw JSON text; they are opt-in and stay unconfigured (raising IntentProviderUnavailableError)
# until their environment variables are set.


class GeminiIntentProvider:
    """Gemini REST adapter using structured JSON output (prompted general model)."""

    name = "gemini"
    source: IntentSource = IntentSource.PROMPTED

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
            raise IntentProviderUnavailableError("GEMINI_API_KEY is not configured.")

        system_prompt = build_intent_system_prompt(reference_date, card_context)
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
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
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise IntentProviderTimeoutError("Gemini request timed out.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise IntentProviderError(f"Gemini request failed: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()

        raw_text = _extract_gemini_text(body)
        return ProviderResponse(
            raw_text=raw_text,
            provider_name=self.name,
            model_id=self.model_id,
            latency_ms=int((monotonic() - started) * 1000),
            response_format=ResponseFormat.JSON_SCHEMA,
            cached=False,
            metadata={"prompt_version": INTENT_PROMPT_VERSION},
        )


class FreesoloIntentProvider:
    """Adapter for the trained Freesolo intent SLM (OpenAI-compatible chat completions).

    The public endpoint contract is not committed here; base URL, key, model, and path come
    from the environment. Unconfigured, it reports itself unavailable so parse_intent falls back.
    """

    name = "freesolo"
    source: IntentSource = IntentSource.FREESOLO

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
            raise IntentProviderUnavailableError("FREESOLO_API_KEY is not configured.")
        if not self.base_url:
            raise IntentProviderUnavailableError("FREESOLO_BASE_URL is not configured.")

        system_prompt = build_intent_system_prompt(reference_date, card_context)
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
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
            raise IntentProviderTimeoutError("Freesolo request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            detail = _safe_error_text(exc.response)
            raise IntentProviderError(
                f"Freesolo request failed: HTTP {exc.response.status_code}. {detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise IntentProviderError(f"Freesolo request failed: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()

        raw_text = _extract_freesolo_text(body)
        return ProviderResponse(
            raw_text=raw_text,
            provider_name=self.name,
            model_id=self.model_id,
            latency_ms=int((monotonic() - started) * 1000),
            response_format=ResponseFormat.JSON_SCHEMA,
            cached=False,
            metadata={"prompt_version": INTENT_PROMPT_VERSION},
        )


def _extract_gemini_text(body: object) -> str:
    if not isinstance(body, dict):
        raise IntentProviderError("Gemini returned a non-object response.")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise IntentProviderError("Gemini returned no candidates.")
    first = candidates[0]
    if not isinstance(first, dict):
        raise IntentProviderError("Gemini candidate has an invalid shape.")
    content = first.get("content")
    if not isinstance(content, dict):
        raise IntentProviderError("Gemini candidate has no content.")
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise IntentProviderError("Gemini content has no parts.")
    texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    raw_text = "".join(text for text in texts if isinstance(text, str)).strip()
    if not raw_text:
        raise IntentProviderError("Gemini returned empty text.")
    return raw_text


def _extract_freesolo_text(body: object) -> str:
    if not isinstance(body, dict):
        raise IntentProviderError("Freesolo returned a non-object response.")
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
    raise IntentProviderError("Freesolo response did not contain model text.")


def _join_freesolo_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    if base_url.endswith("/v1") and normalized_path.startswith("/v1/"):
        normalized_path = normalized_path.removeprefix("/v1")
    return f"{base_url}{normalized_path}"


def _safe_error_text(response: httpx.Response) -> str:
    return response.text.strip().replace("\n", " ")[:300]
