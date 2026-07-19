"""Model runners: the protocol, offline fixtures, and the live provider adapter.

A runner turns one frozen example into raw model text plus error/latency metadata —
nothing more. It never parses, never falls back, and never fabricates output: the
harness applies the same strict parser used at runtime with fallback disabled, so
an invalid model answer stays visible as evidence (IMPLEMENTATION.md §4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol

from eval.models import ErrorCategory, EvalExample, ModelRole, RunnerOutput
from intent.models import IntentCardContext
from intent.providers import (
    IntentProvider,
    IntentProviderError,
    IntentProviderTimeoutError,
    IntentProviderUnavailableError,
)


class ModelRunner(Protocol):
    runner_id: str
    provider_name: str
    model_id: str
    model_role: ModelRole
    prompt_version: str
    synthetic_fixture: bool

    async def run(self, example: EvalExample) -> RunnerOutput: ...


@dataclass(slots=True)
class FixtureModelRunner:
    """Deterministic offline runner for harness-mechanics tests only (§15).

    ``responses`` maps example_id to raw text or an exception to raise. Reports built
    from fixture runners are permanently labeled ``synthetic_fixture`` and can never
    land in the submission report path.
    """

    responses: Mapping[str, str | BaseException]
    model_role: ModelRole
    runner_id: str = "fixture-runner"
    provider_name: str = "fixture"
    model_id: str = "fixture-eval-v1"
    prompt_version: str = "intent-v1"
    synthetic_fixture: bool = True
    calls: list[str] = field(default_factory=list, repr=False)

    async def run(self, example: EvalExample) -> RunnerOutput:
        self.calls.append(example.example_id)
        configured = self.responses.get(example.example_id)
        if configured is None:
            return RunnerOutput(
                example_id=example.example_id,
                runner_id=self.runner_id,
                error_category=ErrorCategory.PROVIDER_UNAVAILABLE,
            )
        if isinstance(configured, BaseException):
            category = (
                ErrorCategory.PROVIDER_TIMEOUT
                if isinstance(configured, (IntentProviderTimeoutError, TimeoutError))
                else ErrorCategory.PROVIDER_ERROR
            )
            return RunnerOutput(
                example_id=example.example_id,
                runner_id=self.runner_id,
                error_category=category,
            )
        return RunnerOutput(
            example_id=example.example_id,
            runner_id=self.runner_id,
            raw_text=configured,
            latency_ms=0,
        )


@dataclass(slots=True)
class ProviderModelRunner:
    """Adapter running a real ``IntentProvider`` under the frozen prompt contract.

    Sends exactly the frozen example's user text, reference date, and card context;
    the provider renders the shared versioned system prompt, so every role sees the
    identical contract (§3). Provider failures map to typed error categories.
    """

    provider: IntentProvider
    model_role: ModelRole
    runner_id: str
    prompt_version: str = "intent-v1"
    synthetic_fixture: bool = False

    @property
    def provider_name(self) -> str:
        return self.provider.name

    @property
    def model_id(self) -> str:
        return self.provider.model_id

    async def run(self, example: EvalExample) -> RunnerOutput:
        card_context: tuple[IntentCardContext, ...] = example.card_context
        started = monotonic()
        try:
            response = await self.provider.generate_intent(
                example.user_text,
                example.reference_date,
                card_context,
            )
        except (IntentProviderTimeoutError, TimeoutError):
            return RunnerOutput(
                example_id=example.example_id,
                runner_id=self.runner_id,
                error_category=ErrorCategory.PROVIDER_TIMEOUT,
                latency_ms=int((monotonic() - started) * 1000),
            )
        except IntentProviderUnavailableError:
            return RunnerOutput(
                example_id=example.example_id,
                runner_id=self.runner_id,
                error_category=ErrorCategory.PROVIDER_UNAVAILABLE,
                latency_ms=int((monotonic() - started) * 1000),
            )
        except IntentProviderError:
            return RunnerOutput(
                example_id=example.example_id,
                runner_id=self.runner_id,
                error_category=ErrorCategory.PROVIDER_ERROR,
                latency_ms=int((monotonic() - started) * 1000),
            )
        return RunnerOutput(
            example_id=example.example_id,
            runner_id=self.runner_id,
            raw_text=response.raw_text,
            latency_ms=response.latency_ms
            if response.latency_ms is not None
            else int((monotonic() - started) * 1000),
        )
