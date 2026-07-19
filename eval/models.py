"""Typed records for the frozen intent evaluation harness.

Everything the harness persists or compares is a validated model: frozen examples,
raw runner outputs, per-example parse outcomes, metric blocks, and the final report.
The report model enforces the provenance rules from eval/IMPLEMENTATION.md §4/§14:
unique runner identities, no fixture posing as a measured system, zero fallback use,
and "final" status only when all three named roles are present and measured.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from engine.models import Goal, Intent
from intent.models import IntentCardContext

Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=120)]
Sha256 = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModelRole(StrEnum):
    TRAINED_SLM = "trained_slm"
    BASE_SLM = "base_slm"
    BIG_PROMPTED = "big_prompted"


REQUIRED_ROLES: tuple[ModelRole, ...] = (
    ModelRole.TRAINED_SLM,
    ModelRole.BASE_SLM,
    ModelRole.BIG_PROMPTED,
)


class ErrorCategory(StrEnum):
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    INVALID_JSON = "invalid_json"
    SCHEMA_INVALID = "schema_invalid"


class ExampleSource(StrEnum):
    GENERATED_TEST = "generated_test"
    ADVERSARIAL_TEST = "adversarial_test"


class EvalExample(EvalModel):
    """One frozen held-out example (IMPLEMENTATION.md §3)."""

    example_id: Identifier
    user_text: Annotated[str, Field(strict=True, min_length=1, max_length=4_000)]
    gold_raw: Annotated[str, Field(strict=True, min_length=1, max_length=20_000)]
    gold_intent: Intent
    reference_date: date
    card_context: tuple[IntentCardContext, ...]
    source: ExampleSource
    latent_id: Identifier | None = None


class FrozenDataset(EvalModel):
    path: str
    dataset_sha256: Sha256
    prompt_version: Identifier
    duplicate_text_count: StrictInt = 0
    examples: tuple[EvalExample, ...]

    @model_validator(mode="after")
    def example_ids_are_unique_and_ordered(self) -> FrozenDataset:
        ids = [example.example_id for example in self.examples]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate example IDs in frozen dataset")
        if ids != sorted(ids):
            raise ValueError("frozen dataset must be stable-sorted by example_id")
        return self

    @property
    def generated_count(self) -> int:
        return sum(1 for e in self.examples if e.source is ExampleSource.GENERATED_TEST)

    @property
    def adversarial_count(self) -> int:
        return sum(1 for e in self.examples if e.source is ExampleSource.ADVERSARIAL_TEST)


class RunnerOutput(EvalModel):
    """Raw output of one model call. Never contains a fallback intent (§4)."""

    example_id: Identifier
    runner_id: Identifier
    raw_text: str | None = None
    error_category: ErrorCategory | None = None
    latency_ms: StrictInt | None = None
    cached: StrictBool = False

    @model_validator(mode="after")
    def output_xor_error(self) -> RunnerOutput:
        if (self.raw_text is None) == (self.error_category is None):
            raise ValueError("runner output carries exactly one of raw_text or error_category")
        if self.error_category in (ErrorCategory.INVALID_JSON, ErrorCategory.SCHEMA_INVALID):
            raise ValueError("parse categories are assigned by the harness, not the runner")
        return self


class ParseOutcome(EvalModel):
    """Strict parse of one raw output with fallback disabled (§6/§7)."""

    example_id: Identifier
    json_parse_ok: StrictBool
    schema_valid: StrictBool
    intent: Intent | None = None
    error_category: ErrorCategory | None = None

    @model_validator(mode="after")
    def valid_state_is_consistent(self) -> ParseOutcome:
        if self.schema_valid and (self.intent is None or not self.json_parse_ok):
            raise ValueError("schema-valid outcomes require parsed JSON and an intent")
        if not self.schema_valid and self.intent is not None:
            raise ValueError("invalid outcomes cannot carry an intent")
        if (self.error_category is None) == (not self.schema_valid):
            raise ValueError("invalid outcomes require an error category; valid ones forbid it")
        return self


class ConstraintFieldMetrics(EvalModel):
    max_utilization_bps_exact: StrictInt
    max_utilization_until_exact: StrictInt
    must_hit_bonus_exact: StrictInt
    whole_constraint_exact: StrictInt
    denominator: StrictInt
    atom_true_positives: StrictInt
    atom_false_positives: StrictInt
    atom_false_negatives: StrictInt

    @property
    def precision(self) -> float:
        predicted = self.atom_true_positives + self.atom_false_positives
        return self.atom_true_positives / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        gold = self.atom_true_positives + self.atom_false_negatives
        return self.atom_true_positives / gold if gold else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


class BootstrapInterval(EvalModel):
    seed: StrictInt
    resamples: StrictInt
    lower: float
    upper: float


class SubsetMetrics(EvalModel):
    """Metric block for one example subset (all / generated / adversarial) (§6)."""

    example_count: StrictInt
    json_parse_rate: float
    schema_valid_rate: float
    weight_mae_ppm: StrictInt | None = None
    weight_mae_valid_count: StrictInt
    per_goal_mae_ppm: dict[Goal, StrictInt] = Field(default_factory=dict)
    constraints: ConstraintFieldMetrics | None = None
    downstream_match_rate: float | None = None
    downstream_interval: BootstrapInterval | None = None
    monthly_mean_agreement: float | None = None
    monthly_exact_match_rate: float | None = None
    monthly_unavailable_count: StrictInt = 0


class RunnerReport(EvalModel):
    runner_id: Identifier
    model_role: ModelRole
    provider_name: Identifier
    model_id: Identifier
    prompt_version: Identifier
    synthetic_fixture: StrictBool
    fallback_count: Literal[0] = 0
    error_counts: dict[ErrorCategory, StrictInt] = Field(default_factory=dict)
    overall: SubsetMetrics
    generated: SubsetMetrics
    adversarial: SubsetMetrics


class EvalReport(EvalModel):
    report_schema_version: Literal["1.0"] = "1.0"
    evaluated_at_utc: Annotated[str, Field(strict=True, min_length=20, max_length=32)]
    status: Literal["final", "partial", "fixture"]
    dataset_path: str
    dataset_sha256: Sha256
    example_count: StrictInt
    generated_count: StrictInt
    adversarial_count: StrictInt
    prompt_version: Identifier
    prompt_sha256: Sha256
    engine_config_hash: Sha256
    probe_suite_sha256: Sha256
    monthly_scenario_sha256: Sha256
    bootstrap_seed: StrictInt
    bootstrap_resamples: StrictInt
    runners: list[RunnerReport] = Field(min_length=1)
    missing_roles: list[ModelRole] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def provenance_is_consistent(self) -> EvalReport:
        seen_runner_ids: dict[str, tuple[str, str]] = {}
        for runner in self.runners:
            identity = (runner.provider_name, runner.model_id)
            existing = seen_runner_ids.get(runner.runner_id)
            if existing is not None and existing != identity:
                raise ValueError(
                    f"runner_id {runner.runner_id!r} is reused for a different model identity"
                )
            seen_runner_ids[runner.runner_id] = identity
        roles = [runner.model_role for runner in self.runners]
        if len(roles) != len(set(roles)):
            raise ValueError("each model role may appear at most once")

        present = set(roles)
        missing = [role for role in REQUIRED_ROLES if role not in present]
        if sorted(self.missing_roles) != sorted(missing):
            raise ValueError("missing_roles must list exactly the absent required roles")

        any_fixture = any(runner.synthetic_fixture for runner in self.runners)
        if self.status == "final":
            if missing:
                raise ValueError("final reports require all three named model roles")
            if any_fixture:
                raise ValueError("fixture runners cannot appear in a final report")
        elif self.status == "partial":
            if any_fixture:
                raise ValueError("fixture runners belong in fixture reports, not partial ones")
            if not missing:
                raise ValueError("a partial report with every role present must be final")
        elif not any_fixture:
            raise ValueError("fixture reports must contain at least one fixture runner")
        return self
