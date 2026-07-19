"""Narrow provider protocols and deterministic offline fixtures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from intent.models import (
    IntentCardContext,
    IntentSource,
    ProviderResponse,
    ResponseFormat,
)
from intent.prompts import INTENT_PROMPT_VERSION


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
