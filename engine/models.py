"""Canonical domain and result contracts for the deterministic engine."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictBool,
    field_validator,
    model_validator,
)

StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictSignedInt = Annotated[int, Field(strict=True)]
StrictBasisPoints = Annotated[int, Field(strict=True, ge=0, le=10_000)]
StrictCardDay = Annotated[int, Field(strict=True, ge=1, le=28)]
Identifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
DisplayName = Annotated[str, Field(strict=True, min_length=1, max_length=120)]
Category = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$"),
]

_CATEGORY_SEPARATORS = re.compile(r"[\s-]+")


class DomainModel(BaseModel):
    """Base configuration shared by engine-owned Pydantic contracts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RewardType(StrEnum):
    CASHBACK = "cashback"
    POINTS = "points"
    MILES = "miles"


class Goal(StrEnum):
    MAX_CASHBACK = "max_cashback"
    MAX_TRAVEL = "max_travel"
    CREDIT_HEALTH = "credit_health"
    HIT_SIGNUP_BONUS = "hit_signup_bonus"
    MAX_CASHFLOW = "max_cashflow"
    MIN_RISK = "min_risk"


class OptimizationStatus(StrEnum):
    OPTIMAL = "optimal"
    HEURISTIC = "heuristic"
    HEURISTIC_FALLBACK = "heuristic_fallback"
    INFEASIBLE = "infeasible"
    UNRESOLVED = "unresolved"


class SolverMethod(StrEnum):
    SINGLE_PURCHASE = "single_purchase"
    GREEDY = "greedy"
    ILP = "ilp"


class IssueCode(StrEnum):
    DUPLICATE_ID = "duplicate_id"
    UNKNOWN_LOCKED_CARD = "unknown_locked_card"
    UNKNOWN_ASSIGNED_CARD = "unknown_assigned_card"
    MISSING_ASSIGNMENT = "missing_assignment"
    PURCHASE_LOCKED_TO_OTHER_CARD = "purchase_locked_to_other_card"
    UNKNOWN_BONUS_CARD = "unknown_bonus_card"
    CARD_HAS_NO_BONUS = "card_has_no_bonus"
    ZERO_CREDIT_LIMIT = "zero_credit_limit"
    CARD_ALREADY_OVER_LIMIT = "card_already_over_limit"
    PURCHASE_EXCEEDS_CAPACITY = "purchase_exceeds_capacity"
    CREDIT_LIMIT_EXCEEDED = "credit_limit_exceeded"
    UTILIZATION_CEILING_EXCEEDED = "utilization_ceiling_exceeded"
    BONUS_DEADLINE_PASSED = "bonus_deadline_passed"
    BONUS_TARGET_UNREACHABLE = "bonus_target_unreachable"
    NO_FEASIBLE_ASSIGNMENT = "no_feasible_assignment"
    HEURISTIC_DEAD_END = "heuristic_dead_end"
    SOLVER_TIMEOUT = "solver_timeout"
    SOLVER_ERROR = "solver_error"


class ConstraintKind(StrEnum):
    CREDIT_LIMIT = "credit_limit"
    UTILIZATION_CEILING = "utilization_ceiling"
    SIGNUP_BONUS = "signup_bonus"


def _normalize_category(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("category must be a string")
    normalized = _CATEGORY_SEPARATORS.sub("_", value.strip().lower())
    return normalized


class RewardRule(DomainModel):
    category: Category
    rate_bps: StrictBasisPoints
    reward_type: RewardType

    _normalize_category = field_validator("category", mode="before")(_normalize_category)


class SignupBonus(DomainModel):
    spend_required_cents: StrictPositiveInt
    spend_so_far_cents: StrictNonNegativeInt
    reward_value_cents: StrictNonNegativeInt
    deadline_date: date

    @property
    def remaining_spend_cents(self) -> int:
        return max(0, self.spend_required_cents - self.spend_so_far_cents)


class Card(DomainModel):
    id: Identifier
    name: DisplayName
    credit_limit_cents: StrictNonNegativeInt
    current_balance_cents: StrictNonNegativeInt
    reward_rules: list[RewardRule] = Field(default_factory=list, max_length=20)
    base_rate_bps: StrictBasisPoints
    base_reward_type: RewardType
    point_value_millicents: StrictNonNegativeInt
    annual_fee_cents: StrictNonNegativeInt
    statement_day: StrictCardDay
    due_day: StrictCardDay
    signup_bonus: SignupBonus | None = None

    @model_validator(mode="after")
    def reward_categories_are_unique(self) -> Card:
        categories = [rule.category for rule in self.reward_rules]
        if len(categories) != len(set(categories)):
            raise ValueError("reward rule categories must be unique per card")
        return self


class Purchase(DomainModel):
    id: Identifier
    amount_cents: StrictPositiveInt
    category: Category
    date: date
    is_recurring: StrictBool
    locked_card_id: Identifier | None = None

    _normalize_category = field_validator("category", mode="before")(_normalize_category)


class Constraint(DomainModel):
    max_utilization_bps: StrictBasisPoints | None = None
    max_utilization_until: date | None = None
    must_hit_bonus_card_ids: list[Identifier] = Field(default_factory=list, max_length=8)

    @field_validator("must_hit_bonus_card_ids")
    @classmethod
    def forced_bonus_ids_are_unique_and_sorted(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("must-hit bonus card IDs must be unique")
        return sorted(value)

    @model_validator(mode="after")
    def cutoff_requires_ceiling(self) -> Constraint:
        if self.max_utilization_until is not None and self.max_utilization_bps is None:
            raise ValueError("max_utilization_until requires max_utilization_bps")
        return self


class Intent(DomainModel):
    """Validated decimal-weight interchange contract between parser and engine."""

    goal_order: ClassVar[tuple[Goal, ...]] = tuple(Goal)

    weights: dict[Goal, FiniteFloat]
    constraints: Constraint = Field(default_factory=Constraint)

    @field_validator("weights", mode="before")
    @classmethod
    def validate_and_normalize_weights(cls, value: Any) -> dict[Goal, float]:
        if not isinstance(value, Mapping):
            raise ValueError("weights must be a mapping containing all six goals")

        parsed: dict[Goal, Decimal] = {}
        for raw_goal, raw_weight in value.items():
            try:
                goal = raw_goal if isinstance(raw_goal, Goal) else Goal(raw_goal)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown goal: {raw_goal!r}") from exc

            if goal in parsed:
                raise ValueError(f"duplicate goal: {goal.value}")
            if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float, Decimal)):
                raise ValueError(f"weight for {goal.value} must be a JSON number")

            try:
                weight = Decimal(str(raw_weight))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"weight for {goal.value} is invalid") from exc
            if not weight.is_finite() or weight < 0:
                raise ValueError(f"weight for {goal.value} must be finite and nonnegative")
            parsed[goal] = weight

        expected = set(cls.goal_order)
        actual = set(parsed)
        if actual != expected:
            missing = sorted(goal.value for goal in expected - actual)
            raise ValueError(f"weights must contain every goal; missing={missing}")

        total = sum(parsed.values(), start=Decimal(0))
        if total <= 0:
            raise ValueError("at least one intent weight must be positive")

        return {goal: float(parsed[goal] / total) for goal in cls.goal_order}

    @classmethod
    def equal_weights(cls, constraints: Constraint | None = None) -> Intent:
        return cls(
            weights={goal: 1.0 for goal in cls.goal_order},
            constraints=constraints or Constraint(),
        )


class Scenario(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    id: Identifier
    name: DisplayName
    synthetic: Literal[True] = True
    reference_date: date
    cards: list[Card]
    purchases: list[Purchase]
    intent: Intent | None = None


class OptimizationIssue(DomainModel):
    code: IssueCode
    message: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    card_ids: list[Identifier] = Field(default_factory=list)
    purchase_ids: list[Identifier] = Field(default_factory=list)
    actual: StrictSignedInt | None = None
    required: StrictSignedInt | None = None
    suggestion: Annotated[str, Field(strict=True, min_length=1, max_length=500)]

    @field_validator("card_ids", "purchase_ids")
    @classmethod
    def affected_ids_are_unique_and_sorted(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("affected IDs must be unique")
        return sorted(value)


class ConstraintSlack(DomainModel):
    kind: ConstraintKind
    card_id: Identifier
    slack_cents: StrictNonNegativeInt
    binding: StrictBool
    near_binding: StrictBool

    @model_validator(mode="after")
    def flags_match_slack(self) -> ConstraintSlack:
        if self.binding != (self.slack_cents == 0):
            raise ValueError("binding must be true exactly when slack_cents is zero")
        if self.binding and self.near_binding:
            raise ValueError("a binding constraint cannot also be near-binding")
        return self


class RawFactorBreakdown(DomainModel):
    cashback_cents: StrictNonNegativeInt
    travel_value_cents: StrictNonNegativeInt
    signup_eligible_spend_cents: StrictNonNegativeInt
    signup_progress_cents: StrictNonNegativeInt
    signup_bonus_earned_cents: StrictNonNegativeInt
    signup_goal_points: StrictNonNegativeInt
    cashflow_days: StrictNonNegativeInt
    cashflow_value_cents: StrictNonNegativeInt
    utilization_before_bps: StrictNonNegativeInt
    utilization_after_bps: StrictNonNegativeInt
    credit_penalty_points: StrictNonNegativeInt
    risk_penalty_points: StrictNonNegativeInt


class ObjectiveBreakdown(DomainModel):
    utility_by_goal: dict[Goal, StrictSignedInt]
    total_utility: StrictSignedInt

    @field_validator("utility_by_goal", mode="before")
    @classmethod
    def require_every_goal(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("utility_by_goal must be a mapping")
        parsed: dict[Goal, Any] = {}
        for raw_goal, utility in value.items():
            try:
                goal = raw_goal if isinstance(raw_goal, Goal) else Goal(raw_goal)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown goal: {raw_goal!r}") from exc
            if goal in parsed:
                raise ValueError(f"duplicate goal: {goal.value}")
            parsed[goal] = utility
        if set(parsed) != set(Goal):
            missing = sorted(goal.value for goal in set(Goal) - set(parsed))
            raise ValueError(f"utility_by_goal must contain every goal; missing={missing}")
        return {goal: parsed[goal] for goal in Goal}

    @model_validator(mode="after")
    def total_matches_goal_contributions(self) -> ObjectiveBreakdown:
        if self.total_utility != sum(self.utility_by_goal.values()):
            raise ValueError("total_utility must equal the sum of utility_by_goal")
        return self


class CandidateDecision(DomainModel):
    card_id: Identifier
    feasible: StrictBool
    rank: StrictPositiveInt | None = None
    raw_factors: RawFactorBreakdown | None = None
    objective: ObjectiveBreakdown | None = None
    issues: list[OptimizationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence_for_candidate_state(self) -> CandidateDecision:
        if self.feasible:
            if self.raw_factors is None or self.objective is None:
                raise ValueError("feasible candidates require raw_factors and objective")
        elif not self.issues:
            raise ValueError("infeasible candidates require at least one issue")
        return self


class MetricDelta(DomainModel):
    cashback_cents: StrictSignedInt = 0
    travel_value_cents: StrictSignedInt = 0
    signup_progress_cents: StrictSignedInt = 0
    signup_bonus_earned_cents: StrictSignedInt = 0
    signup_goal_points: StrictSignedInt = 0
    projected_reward_value_cents: StrictSignedInt = 0
    max_card_utilization_bps: StrictSignedInt = 0
    credit_penalty_points: StrictSignedInt = 0
    risk_penalty_points: StrictSignedInt = 0
    cashflow_days_total: StrictSignedInt = 0
    cashflow_value_cents: StrictSignedInt = 0
    total_utility: StrictSignedInt = 0


class AssignmentAlternative(DomainModel):
    card_id: Identifier
    feasible: StrictBool
    resulting_plan_utility: StrictSignedInt | None = None
    total_utility_delta: StrictSignedInt | None = None
    metric_deltas: MetricDelta | None = None
    issues: list[OptimizationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence_for_alternative_state(self) -> AssignmentAlternative:
        values = (self.resulting_plan_utility, self.total_utility_delta, self.metric_deltas)
        if self.feasible:
            if any(value is None for value in values):
                raise ValueError("feasible alternatives require utility and metric deltas")
        else:
            if not self.issues:
                raise ValueError("infeasible alternatives require at least one issue")
            if any(value is not None for value in values):
                raise ValueError("infeasible alternatives cannot contain plan metrics")
        return self


class PurchaseAssignment(DomainModel):
    purchase_id: Identifier
    card_id: Identifier
    raw_factors: RawFactorBreakdown
    objective: ObjectiveBreakdown
    alternatives: list[AssignmentAlternative] = Field(default_factory=list)


class CardPlanSummary(DomainModel):
    card_id: Identifier
    assigned_purchase_ids: list[Identifier] = Field(default_factory=list)
    assigned_spend_cents: StrictNonNegativeInt
    ending_balance_cents: StrictNonNegativeInt
    ending_utilization_bps: StrictNonNegativeInt
    credit_limit_slack_cents: StrictNonNegativeInt
    utilization_slack_cents: StrictNonNegativeInt | None = None
    bonus_eligible_spend_cents: StrictNonNegativeInt = 0
    bonus_progress_cents: StrictNonNegativeInt = 0
    bonus_remaining_cents: StrictNonNegativeInt | None = None
    bonus_hit: StrictBool | None = None
    cashflow_days_total: StrictNonNegativeInt = 0
    constraint_slacks: list[ConstraintSlack] = Field(default_factory=list)

    @field_validator("assigned_purchase_ids")
    @classmethod
    def assigned_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("assigned purchase IDs must be unique")
        return value

    @model_validator(mode="after")
    def bonus_fields_are_consistent(self) -> CardPlanSummary:
        if (self.bonus_remaining_cents is None) != (self.bonus_hit is None):
            raise ValueError("bonus_remaining_cents and bonus_hit must both be set or both be null")
        return self


class AllocationMetrics(DomainModel):
    cashback_cents: StrictNonNegativeInt
    travel_value_cents: StrictNonNegativeInt
    signup_progress_cents: StrictNonNegativeInt
    signup_bonus_earned_cents: StrictNonNegativeInt
    signup_goal_points: StrictNonNegativeInt
    signup_bonus_hit_count: StrictNonNegativeInt
    projected_reward_value_cents: StrictNonNegativeInt
    max_card_utilization_bps: StrictNonNegativeInt
    credit_penalty_points: StrictNonNegativeInt
    risk_penalty_points: StrictNonNegativeInt
    cashflow_days_total: StrictNonNegativeInt
    cashflow_value_cents: StrictNonNegativeInt
    total_utility: StrictSignedInt

    @model_validator(mode="after")
    def projected_reward_reconciles(self) -> AllocationMetrics:
        expected = (
            self.cashback_cents
            + self.travel_value_cents
            + self.signup_bonus_earned_cents
        )
        if self.projected_reward_value_cents != expected:
            raise ValueError(
                "projected_reward_value_cents must equal cashback, travel value, "
                "and newly earned signup bonuses"
            )
        return self


class RecommendationResult(DomainModel):
    status: OptimizationStatus
    solver_method: SolverMethod = SolverMethod.SINGLE_PURCHASE
    winner: CandidateDecision | None = None
    runner_up: CandidateDecision | None = None
    candidates: list[CandidateDecision] = Field(default_factory=list)
    excluded_cards: list[CandidateDecision] = Field(default_factory=list)
    issues: list[OptimizationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def recommendation_state_is_consistent(self) -> RecommendationResult:
        if self.solver_method is not SolverMethod.SINGLE_PURCHASE:
            raise ValueError("recommendations must use the single_purchase solver method")
        if self.status is OptimizationStatus.OPTIMAL:
            if self.winner is None or not self.winner.feasible:
                raise ValueError("optimal recommendations require a feasible winner")
            if not self.candidates or self.candidates[0] != self.winner:
                raise ValueError("winner must be the first ranked candidate")
            expected_runner = self.candidates[1] if len(self.candidates) > 1 else None
            if self.runner_up != expected_runner:
                raise ValueError("runner_up must be the second ranked candidate when present")
        elif self.status is OptimizationStatus.INFEASIBLE:
            if self.winner is not None or self.runner_up is not None or self.candidates:
                raise ValueError("infeasible recommendations cannot contain feasible candidates")
            if not self.issues:
                raise ValueError("infeasible recommendations require at least one issue")
        else:
            raise ValueError("recommendation status must be optimal or infeasible")
        if any(candidate.feasible for candidate in self.excluded_cards):
            raise ValueError("excluded cards must be infeasible candidates")
        return self


class AllocationResult(DomainModel):
    status: OptimizationStatus
    solver_method: SolverMethod
    assignments: list[PurchaseAssignment] = Field(default_factory=list)
    card_summaries: list[CardPlanSummary] = Field(default_factory=list)
    metrics: AllocationMetrics | None = None
    issues: list[OptimizationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def allocation_state_is_consistent(self) -> AllocationResult:
        successful = {
            OptimizationStatus.OPTIMAL,
            OptimizationStatus.HEURISTIC,
            OptimizationStatus.HEURISTIC_FALLBACK,
        }
        if self.status in successful:
            if self.metrics is None:
                raise ValueError("successful allocations require metrics")
            if (
                self.status is OptimizationStatus.OPTIMAL
                and self.solver_method is not SolverMethod.ILP
            ):
                raise ValueError("optimal monthly allocations require the ILP solver method")
            if self.status in {
                OptimizationStatus.HEURISTIC,
                OptimizationStatus.HEURISTIC_FALLBACK,
            } and self.solver_method is not SolverMethod.GREEDY:
                raise ValueError("heuristic allocations require the greedy solver method")
        else:
            if self.assignments or self.card_summaries or self.metrics is not None:
                raise ValueError(
                    "failed allocations cannot contain assignments, summaries, or metrics"
                )
            if not self.issues:
                raise ValueError("failed allocations require at least one issue")
            if (
                self.status is OptimizationStatus.UNRESOLVED
                and self.solver_method is not SolverMethod.GREEDY
            ):
                raise ValueError("unresolved allocations must come from the greedy solver")
        return self

    @property
    def successful(self) -> bool:
        return self.status in {
            OptimizationStatus.OPTIMAL,
            OptimizationStatus.HEURISTIC,
            OptimizationStatus.HEURISTIC_FALLBACK,
        }


class FrontierPoint(DomainModel):
    label: Annotated[str, Field(strict=True, min_length=1, max_length=120)]
    weights_ppm: dict[Goal, StrictNonNegativeInt]
    frontier_metrics: dict[Goal, StrictSignedInt]
    allocation: AllocationResult

    @field_validator("weights_ppm", mode="before")
    @classmethod
    def ppm_contains_every_goal(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("weights_ppm must be a mapping")
        parsed: dict[Goal, Any] = {}
        for raw_goal, weight in value.items():
            try:
                goal = raw_goal if isinstance(raw_goal, Goal) else Goal(raw_goal)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown goal: {raw_goal!r}") from exc
            if goal in parsed:
                raise ValueError(f"duplicate goal: {goal.value}")
            parsed[goal] = weight
        if set(parsed) != set(Goal):
            missing = sorted(goal.value for goal in set(Goal) - set(parsed))
            raise ValueError(f"weights_ppm must contain every goal; missing={missing}")
        return {goal: parsed[goal] for goal in Goal}

    @model_validator(mode="after")
    def frontier_point_is_consistent(self) -> FrontierPoint:
        if sum(self.weights_ppm.values()) != 1_000_000:
            raise ValueError("weights_ppm must sum exactly to 1,000,000")
        if not self.allocation.successful:
            raise ValueError("frontier points require a successful allocation")
        return self


class FrontierResult(DomainModel):
    solver_method: SolverMethod
    active_goal_ids: list[Goal]
    swept_goal_ids: list[Goal]
    grid_size: StrictPositiveInt
    attempted_solves: StrictNonNegativeInt
    successful_solves: StrictNonNegativeInt
    complete_frontier: Literal[False] = False
    points: list[FrontierPoint] = Field(default_factory=list, max_length=5)
    truncation_reason: str | None = None
    issues: list[OptimizationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def frontier_metadata_is_consistent(self) -> FrontierResult:
        if len(self.active_goal_ids) != len(set(self.active_goal_ids)):
            raise ValueError("active_goal_ids must be unique")
        if len(self.swept_goal_ids) != len(set(self.swept_goal_ids)):
            raise ValueError("swept_goal_ids must be unique")
        if not set(self.swept_goal_ids).issubset(self.active_goal_ids):
            raise ValueError("swept goals must be a subset of active goals")
        if not 1 <= len(self.swept_goal_ids) <= 3:
            raise ValueError("one to three goals may be swept")
        if self.attempted_solves > self.grid_size:
            raise ValueError("attempted_solves cannot exceed grid_size")
        if self.successful_solves > self.attempted_solves:
            raise ValueError("successful_solves cannot exceed attempted_solves")
        if len(self.points) > self.successful_solves:
            raise ValueError("representative points cannot exceed successful solves")
        return self


class AssignmentChange(DomainModel):
    purchase_id: Identifier
    base_card_id: Identifier
    override_card_id: Identifier

    @model_validator(mode="after")
    def cards_must_differ(self) -> AssignmentChange:
        if self.base_card_id == self.override_card_id:
            raise ValueError("assignment changes require different card IDs")
        return self


class WhatIfResult(DomainModel):
    purchase_id: Identifier
    override_card_id: Identifier
    base_result: AllocationResult
    override_result: AllocationResult
    deltas: MetricDelta | None = None
    changed_assignments: list[AssignmentChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def what_if_state_is_consistent(self) -> WhatIfResult:
        both_successful = self.base_result.successful and self.override_result.successful
        if both_successful != (self.deltas is not None):
            raise ValueError("what-if deltas exist exactly when both plans are successful")
        if not both_successful and self.changed_assignments:
            raise ValueError("failed what-if comparisons cannot contain assignment changes")
        return self
