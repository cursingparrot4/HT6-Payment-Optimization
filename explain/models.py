"""Structured, renderer-agnostic explanation contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from engine.models import Goal, OptimizationStatus, SolverMethod

Identifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=120),
]
DisplayText = Annotated[str, Field(strict=True, min_length=1, max_length=1_000)]
SourcePath = Annotated[str, Field(strict=True, min_length=1, max_length=240)]
StrictSignedInt = Annotated[int, Field(strict=True)]


class ExplanationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExplanationKind(StrEnum):
    REWARD = "reward"
    TRAVEL = "travel"
    UTILIZATION = "utilization"
    BONUS = "bonus"
    CASHFLOW = "cashflow"
    RISK = "risk"
    CONSTRAINT = "constraint"
    SOLVER = "solver"
    WARNING = "warning"


class ExplanationTone(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    CAUTION = "caution"
    BLOCKING = "blocking"


class ExplanationUnit(StrEnum):
    CENTS = "cents"
    BPS = "bps"
    DAYS = "days"
    POINTS = "points"
    COUNT = "count"
    BOOLEAN = "boolean"


class ExplanationLine(ExplanationModel):
    kind: ExplanationKind
    tone: ExplanationTone
    label: Identifier
    text: DisplayText
    raw_value: StrictSignedInt | StrictBool | None = None
    unit: ExplanationUnit | None = None
    source_path: SourcePath
    goal: Goal | None = None

    @model_validator(mode="after")
    def value_and_unit_agree(self) -> ExplanationLine:
        if (self.raw_value is None) != (self.unit is None):
            raise ValueError("raw_value and unit must both be set or both be null")
        if isinstance(self.raw_value, bool) and self.unit is not ExplanationUnit.BOOLEAN:
            raise ValueError("boolean values require the boolean unit")
        if (
            self.raw_value is not None
            and not isinstance(self.raw_value, bool)
            and self.unit is ExplanationUnit.BOOLEAN
        ):
            raise ValueError("the boolean unit requires a boolean value")
        return self


class AlternativeExplanation(ExplanationModel):
    card_id: Identifier
    card_name: Identifier
    feasible: StrictBool
    summary: DisplayText
    utility_delta_points: StrictSignedInt | None = None
    lines: list[ExplanationLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def utility_delta_requires_feasibility(self) -> AlternativeExplanation:
        if not self.feasible and self.utility_delta_points is not None:
            raise ValueError("infeasible alternatives cannot have a utility delta")
        return self


class DecisionCard(ExplanationModel):
    card_id: Identifier
    card_name: Identifier
    purchase_id: Identifier
    purchase_label: Identifier
    headline: DisplayText
    status: OptimizationStatus
    solver_method: SolverMethod
    factor_lines: list[ExplanationLine] = Field(default_factory=list)
    constraint_lines: list[ExplanationLine] = Field(default_factory=list)
    alternative: AlternativeExplanation | None = None
    warning_lines: list[ExplanationLine] = Field(default_factory=list)


class CardSummaryExplanation(ExplanationModel):
    card_id: Identifier
    card_name: Identifier
    headline: DisplayText
    lines: list[ExplanationLine] = Field(default_factory=list)


class FailureExplanation(ExplanationModel):
    headline: DisplayText
    lines: list[ExplanationLine]
    suggestions: list[DisplayText] = Field(default_factory=list)


class RecommendationExplanation(ExplanationModel):
    status: OptimizationStatus
    headline: DisplayText
    decision_card: DecisionCard | None = None
    excluded_alternatives: list[AlternativeExplanation] = Field(default_factory=list)
    failure: FailureExplanation | None = None

    @model_validator(mode="after")
    def state_matches_status(self) -> RecommendationExplanation:
        if self.status is OptimizationStatus.OPTIMAL:
            if self.decision_card is None or self.failure is not None:
                raise ValueError("optimal recommendation requires a decision card only")
        elif self.failure is None or self.decision_card is not None:
            raise ValueError("failed recommendation requires a failure block only")
        return self


class AllocationExplanation(ExplanationModel):
    status: OptimizationStatus
    solver_method: SolverMethod
    headline: DisplayText
    summary_lines: list[ExplanationLine] = Field(default_factory=list)
    card_summaries: list[CardSummaryExplanation] = Field(default_factory=list)
    decision_cards: list[DecisionCard] = Field(default_factory=list)
    highlighted_purchase_ids: list[Identifier] = Field(default_factory=list)
    warning_lines: list[ExplanationLine] = Field(default_factory=list)
    failure: FailureExplanation | None = None

    @model_validator(mode="after")
    def state_matches_status(self) -> AllocationExplanation:
        successful = {
            OptimizationStatus.OPTIMAL,
            OptimizationStatus.HEURISTIC,
            OptimizationStatus.HEURISTIC_FALLBACK,
        }
        if self.status in successful:
            if self.failure is not None:
                raise ValueError("successful allocation cannot contain a failure block")
        elif self.failure is None:
            raise ValueError("failed allocation requires a failure block")
        return self


class FrontierPointExplanation(ExplanationModel):
    label: Identifier
    summary: DisplayText
    status: OptimizationStatus
    solver_method: SolverMethod
    metric_lines: list[ExplanationLine]


class FrontierExplanation(ExplanationModel):
    headline: DisplayText
    active_goals: list[Goal]
    swept_goals: list[Goal]
    attempted_solves: StrictSignedInt
    successful_solves: StrictSignedInt
    complete_frontier: StrictBool
    points: list[FrontierPointExplanation] = Field(default_factory=list)
    disclosure_lines: list[ExplanationLine] = Field(default_factory=list)
    warning_lines: list[ExplanationLine] = Field(default_factory=list)


class WhatIfExplanation(ExplanationModel):
    headline: DisplayText
    purchase_id: Identifier
    override_card_id: Identifier
    base_status: OptimizationStatus
    override_status: OptimizationStatus
    delta_lines: list[ExplanationLine] = Field(default_factory=list)
    changed_assignment_lines: list[ExplanationLine] = Field(default_factory=list)
    warning_lines: list[ExplanationLine] = Field(default_factory=list)
    failure: FailureExplanation | None = None


class ExplanationContractError(RuntimeError):
    def __init__(self, source_path: str, reason: str) -> None:
        self.source_path = source_path
        self.reason = reason
        super().__init__(f"explanation contract error at {source_path}: {reason}")
