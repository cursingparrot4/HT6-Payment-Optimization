"""Evaluation orchestration: run/cache raw outputs, parse strictly, compute metrics.

Flow (IMPLEMENTATION.md §13): hash the frozen inputs, run each named runner over
every example with an atomic on-disk cache, parse raw text with fallback disabled,
then score parser validity, weight error, constraint extraction, downstream decision
match (five exact single-purchase probes), and monthly ILP agreement.

Downstream and monthly metrics compare *weights*: predicted and gold intents are
applied to the frozen probe/monthly structures with constraints cleared, because
dataset constraints name held-out context cards that do not exist in the probes —
forcing them would make every solve trivially infeasible and measure nothing.
Constraint extraction has its own dedicated metric block (§9).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from engine.config import DEFAULT_ENGINE_CONFIG, engine_config_hash
from engine.models import Constraint, Goal, Intent, OptimizationStatus, SolverMethod
from engine.objective import quantize_intent_weights
from engine.optimize import allocate_month, recommend_purchase
from eval.dataset import FrozenDataset
from eval.metrics import (
    cluster_bootstrap_interval,
    constraint_metrics,
    downstream_match_rate,
    json_parse_rate,
    monthly_agreement,
    schema_valid_rate,
    weight_mae_ppm,
)
from eval.models import (
    REQUIRED_ROLES,
    ErrorCategory,
    EvalExample,
    EvalReport,
    ExampleSource,
    ParseOutcome,
    RunnerOutput,
    RunnerReport,
    SubsetMetrics,
)
from eval.runners import ModelRunner
from eval.scenarios import (
    MONTHLY_SCENARIO,
    PROBES,
    monthly_scenario_sha256,
    probe_suite_sha256,
)
from intent.manifests import canonical_json, sha256_bytes
from intent.models import ParseWarningCode
from intent.parser import IntentParseError, parse_provider_output
from intent.prompts import build_intent_system_prompt

REQUEST_SCHEMA_ID = "generate-intent-request-v1"


# ---------------------------------------------------------------- cache


def _cache_key(dataset_sha256: str, example_id: str, runner: ModelRunner) -> str:
    material = "|".join(
        (
            dataset_sha256,
            example_id,
            runner.runner_id,
            runner.model_id,
            runner.prompt_version,
            sha256_bytes(REQUEST_SCHEMA_ID.encode("ascii")),
        )
    )
    return sha256_bytes(material.encode("utf-8"))


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(canonical_json(payload), encoding="utf-8")
    os.replace(tmp, path)


def _load_cached_output(path: Path, runner: ModelRunner, example_id: str) -> RunnerOutput | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("provider_name") != runner.provider_name
        or payload.get("model_id") != runner.model_id
        or payload.get("prompt_version") != runner.prompt_version
    ):
        return None
    return RunnerOutput(
        example_id=example_id,
        runner_id=runner.runner_id,
        raw_text=payload.get("raw_text"),
        error_category=(
            ErrorCategory(payload["error_category"]) if payload.get("error_category") else None
        ),
        latency_ms=payload.get("latency_ms"),
        cached=True,
    )


async def _run_with_cache(
    runner: ModelRunner,
    example: EvalExample,
    dataset_sha256: str,
    cache_dir: Path | None,
    refresh: bool,
) -> RunnerOutput:
    cache_path = None
    if cache_dir is not None:
        cache_path = cache_dir / f"{_cache_key(dataset_sha256, example.example_id, runner)}.json"
        if not refresh:
            cached = _load_cached_output(cache_path, runner, example.example_id)
            if cached is not None:
                return cached
    output = await runner.run(example)
    if cache_path is not None:
        # Identity metadata plus raw output only — never API keys or headers (§5).
        _atomic_write_json(
            cache_path,
            {
                "provider_name": runner.provider_name,
                "model_id": runner.model_id,
                "prompt_version": runner.prompt_version,
                "example_id": example.example_id,
                "runner_id": runner.runner_id,
                "raw_text": output.raw_text,
                "error_category": output.error_category.value if output.error_category else None,
                "latency_ms": output.latency_ms,
            },
        )
    return output


# ---------------------------------------------------------------- parsing


def parse_output(example: EvalExample, output: RunnerOutput) -> ParseOutcome:
    """Strict parse with fallback disabled — invalid output stays invalid (§4)."""

    if output.error_category is not None:
        return ParseOutcome(
            example_id=example.example_id,
            json_parse_ok=False,
            schema_valid=False,
            error_category=output.error_category,
        )
    try:
        parsed = parse_provider_output(output.raw_text or "", example.card_context)
    except IntentParseError as exc:
        json_ok = exc.warning.code is not ParseWarningCode.INVALID_JSON
        return ParseOutcome(
            example_id=example.example_id,
            json_parse_ok=json_ok,
            schema_valid=False,
            error_category=(
                ErrorCategory.SCHEMA_INVALID if json_ok else ErrorCategory.INVALID_JSON
            ),
        )
    return ParseOutcome(
        example_id=example.example_id,
        json_parse_ok=True,
        schema_valid=True,
        intent=parsed.intent,
    )


# ---------------------------------------------------------------- engine oracles


def _weights_only(intent: Intent) -> Intent:
    return Intent(weights=dict(intent.weights), constraints=Constraint())


def _intent_cache_token(intent: Intent) -> str:
    ppm = quantize_intent_weights(intent)
    return canonical_json({goal.value: ppm[goal] for goal in Goal})


class EngineOracle:
    """Deterministic engine answers, memoized by canonical weight ppm (§13)."""

    def __init__(self) -> None:
        self._probe_winners: dict[tuple[str, str], str | None] = {}
        self._monthly_plans: dict[str, dict[str, str] | None] = {}

    def probe_winner(self, intent: Intent, probe_index: int) -> str | None:
        stripped = _weights_only(intent)
        key = (_intent_cache_token(stripped), PROBES[probe_index].probe_id)
        if key not in self._probe_winners:
            probe = PROBES[probe_index]
            result = recommend_purchase(list(probe.cards), probe.purchase, stripped)
            self._probe_winners[key] = result.winner.card_id if result.winner else None
        return self._probe_winners[key]

    def monthly_plan(self, intent: Intent) -> dict[str, str] | None:
        """Purchase->card map from an exact ILP solve, or None when not optimal."""

        stripped = _weights_only(intent)
        key = _intent_cache_token(stripped)
        if key not in self._monthly_plans:
            result = allocate_month(
                list(MONTHLY_SCENARIO.cards),
                list(MONTHLY_SCENARIO.purchases),
                stripped,
                method=SolverMethod.ILP,
            )
            if result.status is not OptimizationStatus.OPTIMAL:
                self._monthly_plans[key] = None
            else:
                self._monthly_plans[key] = {
                    assignment.purchase_id: assignment.card_id
                    for assignment in result.assignments
                }
        return self._monthly_plans[key]


# ---------------------------------------------------------------- metric assembly


def _subset_metrics(
    examples: list[EvalExample],
    outcomes: dict[str, ParseOutcome],
    matches: dict[str, list[bool]],
    monthly: dict[str, float | None],
    *,
    seed: int,
    resamples: int,
) -> SubsetMetrics:
    subset_outcomes = [outcomes[e.example_id] for e in examples]
    valid_pairs = [
        (e.gold_intent, outcomes[e.example_id].intent)
        for e in examples
        if outcomes[e.example_id].schema_valid
    ]
    mae, per_goal, valid_count = weight_mae_ppm(valid_pairs)
    constraint_rows: list[tuple[Constraint, Constraint | None]] = []
    for e in examples:
        predicted = outcomes[e.example_id].intent
        constraint_rows.append(
            (e.gold_intent.constraints, predicted.constraints if predicted is not None else None)
        )
    subset_matches = {e.example_id: matches[e.example_id] for e in examples}
    subset_monthly = [monthly[e.example_id] for e in examples]
    mean_agreement, exact_rate, unavailable = monthly_agreement(subset_monthly)
    return SubsetMetrics(
        example_count=len(examples),
        json_parse_rate=json_parse_rate(subset_outcomes),
        schema_valid_rate=schema_valid_rate(subset_outcomes),
        weight_mae_ppm=mae,
        weight_mae_valid_count=valid_count,
        per_goal_mae_ppm=per_goal,
        constraints=constraint_metrics(constraint_rows) if examples else None,
        downstream_match_rate=downstream_match_rate(subset_matches) if examples else None,
        downstream_interval=(
            cluster_bootstrap_interval(subset_matches, seed=seed, resamples=resamples)
            if examples
            else None
        ),
        monthly_mean_agreement=mean_agreement,
        monthly_exact_match_rate=exact_rate,
        monthly_unavailable_count=unavailable,
    )


async def evaluate_runner(
    runner: ModelRunner,
    dataset: FrozenDataset,
    oracle: EngineOracle,
    *,
    cache_dir: Path | None,
    seed: int,
    resamples: int,
    refresh: bool = False,
    include_monthly: bool = True,
) -> RunnerReport:
    outcomes: dict[str, ParseOutcome] = {}
    matches: dict[str, list[bool]] = {}
    monthly: dict[str, float | None] = {}
    error_counts: dict[ErrorCategory, int] = {}

    for example in dataset.examples:
        output = await _run_with_cache(
            runner, example, dataset.dataset_sha256, cache_dir, refresh
        )
        outcome = parse_output(example, output)
        outcomes[example.example_id] = outcome
        if outcome.error_category is not None:
            error_counts[outcome.error_category] = (
                error_counts.get(outcome.error_category, 0) + 1
            )

        gold_winners = [
            oracle.probe_winner(example.gold_intent, index) for index in range(len(PROBES))
        ]
        if outcome.schema_valid and outcome.intent is not None:
            predicted_winners = [
                oracle.probe_winner(outcome.intent, index) for index in range(len(PROBES))
            ]
            matches[example.example_id] = [
                gold is not None and gold == predicted
                for gold, predicted in zip(gold_winners, predicted_winners, strict=True)
            ]
        else:
            # Invalid output is a mismatch on every probe, never a dropped row (§6).
            matches[example.example_id] = [False] * len(PROBES)

        if not include_monthly:
            monthly[example.example_id] = None
        elif not outcome.schema_valid or outcome.intent is None:
            monthly[example.example_id] = 0.0
        else:
            gold_plan = oracle.monthly_plan(example.gold_intent)
            predicted_plan = oracle.monthly_plan(outcome.intent)
            if gold_plan is None or predicted_plan is None:
                monthly[example.example_id] = None
            else:
                matching = sum(
                    1
                    for purchase in MONTHLY_SCENARIO.purchases
                    if gold_plan.get(purchase.id) == predicted_plan.get(purchase.id)
                )
                monthly[example.example_id] = matching / len(MONTHLY_SCENARIO.purchases)

    def subset(source: ExampleSource | None) -> list[EvalExample]:
        if source is None:
            return list(dataset.examples)
        return [e for e in dataset.examples if e.source is source]

    return RunnerReport(
        runner_id=runner.runner_id,
        model_role=runner.model_role,
        provider_name=runner.provider_name,
        model_id=runner.model_id,
        prompt_version=runner.prompt_version,
        synthetic_fixture=runner.synthetic_fixture,
        error_counts=error_counts,
        overall=_subset_metrics(
            subset(None), outcomes, matches, monthly, seed=seed, resamples=resamples
        ),
        generated=_subset_metrics(
            subset(ExampleSource.GENERATED_TEST),
            outcomes, matches, monthly, seed=seed, resamples=resamples,
        ),
        adversarial=_subset_metrics(
            subset(ExampleSource.ADVERSARIAL_TEST),
            outcomes, matches, monthly, seed=seed, resamples=resamples,
        ),
    )


def prompt_contract_sha256(dataset: FrozenDataset) -> str:
    """Hash of every rendered system prompt — the exact contract each model saw."""

    rendered = [
        build_intent_system_prompt(example.reference_date, example.card_context)
        for example in dataset.examples
    ]
    return sha256_bytes(canonical_json(rendered).encode("utf-8"))


async def run_evaluation(
    dataset: FrozenDataset,
    runners: list[ModelRunner],
    *,
    cache_dir: Path | None,
    seed: int = 42,
    resamples: int = 1_000,
    refresh: bool = False,
    include_monthly: bool = True,
) -> EvalReport:
    if not runners:
        raise ValueError("at least one model runner is required")
    oracle = EngineOracle()
    runner_reports = [
        await evaluate_runner(
            runner,
            dataset,
            oracle,
            cache_dir=cache_dir,
            seed=seed,
            resamples=resamples,
            refresh=refresh,
            include_monthly=include_monthly,
        )
        for runner in runners
    ]
    present_roles = {report.model_role for report in runner_reports}
    missing = [role for role in REQUIRED_ROLES if role not in present_roles]
    if any(report.synthetic_fixture for report in runner_reports):
        status = "fixture"
    elif missing:
        status = "partial"
    else:
        status = "final"
    warnings = []
    if missing:
        warnings.append(
            "Missing required model roles (no credentials): "
            + ", ".join(role.value for role in missing)
            + ". This report cannot support comparative model claims."
        )
    if not include_monthly:
        warnings.append("Monthly ILP agreement was skipped for this run.")
    if dataset.duplicate_text_count:
        warnings.append(
            f"The frozen set repeats {dataset.duplicate_text_count} user phrasings; "
            "effective sample size is smaller than the row count."
        )
    return EvalReport(
        evaluated_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status=status,
        dataset_path=dataset.path,
        dataset_sha256=dataset.dataset_sha256,
        example_count=len(dataset.examples),
        generated_count=dataset.generated_count,
        adversarial_count=dataset.adversarial_count,
        prompt_version=dataset.prompt_version,
        prompt_sha256=prompt_contract_sha256(dataset),
        engine_config_hash=engine_config_hash(DEFAULT_ENGINE_CONFIG),
        probe_suite_sha256=probe_suite_sha256(),
        monthly_scenario_sha256=monthly_scenario_sha256(),
        bootstrap_seed=seed,
        bootstrap_resamples=resamples,
        runners=runner_reports,
        missing_roles=missing,
        warnings=warnings,
    )


def run_evaluation_sync(*args, **kwargs) -> EvalReport:
    return asyncio.run(run_evaluation(*args, **kwargs))
