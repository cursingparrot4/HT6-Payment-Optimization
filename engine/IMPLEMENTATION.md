# Deterministic Engine Implementation Guide

## Implementation status

Implemented and validated:

- Strict input and result models, enums, issue codes, and status invariants.
- Frozen engine calibration plus reproducible config hashing.
- Decimal-to-ppm largest-remainder quantization and integer goal weighting.
- Statement-close/due-date calculations and pure integer scoring.
- Shared scenario/assignment feasibility with temporal utilization and forced bonuses.
- Exact single-purchase recommendation with stable winner/runner-up ranking.
- Aggregate plan evaluation and greedy monthly allocation with bounded repair, relocation/swap search, structured `infeasible` versus `unresolved`, and final-state alternatives.
- Exact PuLP/CBC allocation with all-binary reachable spend states, objective parity verification, deterministic two-pass tie-breaking, and greedy fallback.
- Brute-force parity coverage for aggregate utilization, headroom, bonus progress/completion, locks, temporal ceilings, and indivisible infeasibility.
- Bounded sampled strategy frontier with raw-goal dominance filtering and representative selection.
- Reoptimized one-purchase what-if with assignment and metric deltas.
- Public `optimize.py` dispatch for recommendation, greedy/exact monthly allocation, sampled frontier, and what-if.

Pending in this module:

- Performance validation against the full synthetic Sarah fixture once the data module lands.
- Any calibration changes discovered during UI/demo rehearsal.

Current focused gate: `uv run python -m pytest tests/unit/engine tests/oracle -q` and `uv run python -m ruff check engine tests/unit/engine tests/oracle`.

## 1. Mission and boundary

The engine is the source of truth for every financial calculation and optimization decision. Given validated synthetic cards, purchases, and an `Intent`, it produces deterministic recommendations or allocations with auditable factors, explicit solver status, and structured failure information.

The engine does not:

- Parse natural language.
- Format UI strings.
- Call external services.
- Load repository JSON fixtures.
- Store state between requests.
- Model real payments, statement ledgers, interest charges, award availability, or credit-score changes.

The engine may depend on Pydantic and PuLP. It must not import any other project package.

## 2. File ownership

Implement in this order:

| File | Responsibility |
|---|---|
| `models.py` | Domain inputs, factors, metrics, issues, and result models |
| `config.py` | Frozen objective/scoring assumptions and solver limits |
| `dates.py` | Statement-close, due-date, and float-day calculations |
| `scoring.py` | Pure raw per-purchase/card calculations |
| `objective.py` | Weight quantization, aggregate utility, and marginal utility |
| `feasibility.py` | Shared constraints, exact comparisons, issue codes, and slack |
| `recommend.py` | Exact one-purchase enumeration |
| `greedy.py` | Stateful monthly heuristic, repair, relocation, and swaps |
| `ilp.py` | PuLP/CBC exact allocation and fallback signaling |
| `pareto.py` | Weight grids, canonicalization, raw dominance, representatives |
| `what_if.py` | Override one purchase, reoptimize, and compute deltas |
| `optimize.py` | Stable public facade and solver dispatch |

Only `optimize.py` should be treated as the optimizer's public call surface by API/eval code. Tests may import internal pure functions.

## 3. Public Python surface

Target signatures:

```python
def recommend_purchase(
    cards: list[Card],
    purchase: Purchase,
    intent: Intent,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> RecommendationResult: ...

def allocate_month(
    cards: list[Card],
    purchases: list[Purchase],
    intent: Intent,
    method: SolverMethod = SolverMethod.GREEDY,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> AllocationResult: ...

def sample_frontier(
    cards: list[Card],
    purchases: list[Purchase],
    intent: Intent,
    method: SolverMethod = SolverMethod.ILP,
    max_points: int = 5,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> FrontierResult: ...

def run_what_if(
    cards: list[Card],
    purchases: list[Purchase],
    intent: Intent,
    purchase_id: str,
    override_card_id: str,
    method: SolverMethod = SolverMethod.ILP,
    config: EngineConfig = DEFAULT_ENGINE_CONFIG,
) -> WhatIfResult: ...
```

Inputs are not mutated. Sort copies when deterministic ordering is needed. Default config is frozen and safe to share.

## 4. Models

Use Pydantic v2 with `ConfigDict(extra="forbid")` on domain models and `StrictInt` or `Field(strict=True)` on every integer money/rate/day field. This rejects floats, booleans, and numeric strings without preventing ISO date strings from validating in fixture/API JSON. Prefer enums over unconstrained strings for reward type, goal, status, solver method, factor kind, and issue code.

### Input models

- `RewardType`: `cashback`, `points`, `miles`.
- `RewardRule`: normalized category, nonnegative bps, reward type.
- `SignupBonus`: required spend, prior spend, reward value, deadline.
- `Card`: fields in integration contracts plus `base_reward_type`.
- `Purchase`: positive cents, normalized category, date, recurring flag, optional lock.
- `Constraint`: utilization ceiling/cutoff and forced bonus IDs.
- `Intent`: all six finite nonnegative weights and constraints.
- `Scenario`: cards, purchases, intent optional, scenario ID/name/reference date for API/data interchange.

Model-level validators handle local invariants. Collection-level validation such as unique IDs and valid references belongs in `feasibility.py` because requests combine independently valid objects.

### Output models

Implement typed outputs rather than nested anonymous dictionaries:

- `OptimizationIssue`
- `ConstraintSlack`
- `RawFactorBreakdown`
- `ObjectiveBreakdown`
- `CandidateDecision`
- `AssignmentAlternative`
- `PurchaseAssignment`
- `CardPlanSummary`
- `AllocationMetrics`
- `RecommendationResult`
- `AllocationResult`
- `FrontierPoint`
- `FrontierResult`
- `MetricDelta`
- `WhatIfResult`

All collection defaults use `Field(default_factory=...)`. All result lists have deterministic order. Include enough raw source fields for explanations so that `explain` never calls a scoring function.

### Status rules

- Single-purchase enumeration with a winner: `optimal`.
- Complete greedy/local-search plan: `heuristic`.
- Greedy returned after exact timeout/error: `heuristic_fallback`.
- CBC or an analytical contradiction proves impossibility: `infeasible`.
- Greedy/repair cannot complete a plan without proof: `unresolved`.

Never infer status from whether `assignments` is empty; set it deliberately and test allowed combinations.

## 5. Configuration

Use frozen dataclasses or frozen Pydantic models. Recommended shape:

```python
@dataclass(frozen=True)
class UtilizationBand:
    upper_bps: int
    penalty_points_per_bps: int

@dataclass(frozen=True)
class EngineConfig:
    weight_scale_ppm: int = 1_000_000
    annual_carry_rate_bps: int = 500
    signup_progress_pool_bps: int = 2_000
    desired_headroom_bps: int = 1_000
    minimum_headroom_cents: int = 50_000
    utilization_bands: tuple[UtilizationBand, ...] = (...)
    greedy_repair_depth: int = 2
    local_search_max_passes: int = 20
    ilp_timeout_seconds: int = 5
    ilp_wall_timeout_seconds: int = 60
    ilp_max_card_states: int = 5_000
    ilp_combined_tie_break_limit: int = 1_000_000
    frontier_two_goal_steps: int = 5
    frontier_three_goal_denominator: int = 4
    frontier_max_solves: int = 15
    frontier_timeout_seconds: int = 15
```

Suggested utilization bands:

| Utilization range | Incremental points per bps |
|---|---:|
| 0-30% | 0 |
| 30-50% | 2 |
| 50-75% | 6 |
| 75-100% | 20 |
| Above 100% imported state | 50 |

The hard credit limit prevents a new plan above 100%, but the last band keeps diagnostics defined when input balances are already invalid.

Validate that band endpoints increase and slopes are nondecreasing. Keep constants out of algorithm modules.

## 6. Date calculations

All card days are at most 28, avoiding invalid dates across months.

### Next statement close

1. Build a date in the purchase year/month at `statement_day`.
2. If purchase date is on or before it, use it.
3. Otherwise move to the next calendar month and use `statement_day`.

### Due date for that close

1. Build a date in the close year/month at `due_day`.
2. If it is strictly after the close, use it.
3. Otherwise move to the next calendar month and use `due_day`.

`float_days = (due_date - purchase.date).days` and must be positive. Tests cover month and year rollover plus purchase/close equality.

## 7. Raw scoring

Every helper is pure, deterministic, and independently tested.

### Rule selection

Use an exact normalized category match. If absent, use `base_rate_bps` and `base_reward_type`. Do not let an `other` rule silently replace base rate unless the purchase category itself is `other`.

### Reward accrual

For cashback, return cashback cents and zero travel value. For points/miles, accrue whole points first, then convert with the card's static point value. Return zero cashback and the converted travel value. This separation is essential: `max_cashback` must not reward a point card and `max_travel` must not reward cash.

### Utilization

`utilization_after(card, extra_cents)` returns integer bps for display. For zero limit, return `10000` plus a zero-limit issue through feasibility; do not divide by zero. Display helpers do not decide feasibility.

`utilization_penalty_points(util_bps, config)` integrates each configured band slope. For each band, multiply the bps width inside that band by its incremental slope, then sum. With the table above, `4500` bps costs `(3000 * 0) + (1500 * 2) = 3000` points. The plan-level credit penalty is ending penalty minus starting penalty so existing balances do not masquerade as harm caused by the plan.

### Risk headroom

Define desired ending headroom:

```text
desired = min(
    credit_limit_cents,
    max(minimum_headroom_cents, credit_limit_cents * desired_headroom_bps // 10000),
)
```

Compute `headroom_before = limit - current_balance` and `headroom_after = limit - ending_balance`. The incremental risk penalty is `max(0, desired - headroom_after) - max(0, desired - headroom_before)`. This goal overlaps with utilization directionally but answers a distinct question: whether useful capacity remains for unexpected spend.

### Cashflow

Return both float days and carrying-value cents:

```text
amount * annual_rate_bps * days // (10000 * 365)
```

Multiply before dividing; Python integers do not overflow and early division would incorrectly erase small carrying values. This value can be zero for small purchases and should not be rounded upward.

### Signup progress

For a candidate plan/card:

```text
remaining_before = max(0, requirement - spend_so_far)
eligible_assigned = sum(amount for dated-eligible assigned purchases)
progress = min(remaining_before, eligible_assigned)
progress_pool = bonus_value * signup_progress_pool_bps // 10000
progress_utility = progress_pool * progress // remaining_before  # when remaining > 0
completion_utility = bonus_value - progress_pool  # only when progress == remaining
```

If the bonus was already achieved before this plan, new utility is zero. Metrics may report `bonus_hit=true` but `signup_bonus_earned_cents` for this plan remains zero unless product wording explicitly distinguishes prior state.

### Factor breakdown scope

For recommendation, raw factors represent adding that purchase to current state. For monthly assignment output, each purchase retains additive reward/cashflow facts, while card summaries hold aggregate utilization/bonus facts. Do not allocate aggregate bonus value arbitrarily across purchases for display.

After a complete monthly plan is finalized, evaluate every one-purchase alternative against that final state. For each purchase/card pair other than the selected card, keep all other assignments fixed, recompute feasibility and plan metrics, and emit `AssignmentAlternative`. This optimizer-owned trace is the only source for monthly "why not" explanations. Sort feasible alternatives by resulting plan utility descending then card ID; append infeasible alternatives by card ID.

## 8. Weight quantization and utility

### Quantization

1. Convert each finite input through `Decimal(str(value))`.
2. Normalize by the exact decimal sum.
3. Multiply by `1_000_000`.
4. Take each floor.
5. Distribute remaining units to largest fractional remainders.
6. Break equal remainders by `Goal` enum order.

Return `dict[Goal, int]`. Assert nonnegative values and exact sum.

### Plan utility

Implement one `evaluate_plan` function used by greedy verification and tests. It takes an assignment map, validates it, computes additive pair facts, computes aggregate card facts, maps them to per-goal utility, applies ppm weights, and returns metrics plus objective breakdown.

The candidate marginal utility used by greedy is:

$$
U(plan \cup assignment) - U(plan)
$$

Optimize this delta, not a stateless card score. Cache unchanged card summaries within a pass if profiling shows a need; correctness comes first.

The complete plan objective is the sum of additive purchase/card utility, minus aggregate ending utilization and risk penalties by card, plus aggregate capped bonus progress/completion utility by card. Both the delta above and ILP objective must equal this same `evaluate_plan` definition.

## 9. Feasibility analyzer

Expose small composable functions plus one full-plan analyzer:

```python
def validate_scenario(cards, purchases, intent) -> list[OptimizationIssue]: ...

def feasible_cards_for_purchase(
    state: PlanningState,
    purchase: Purchase,
    intent: Intent,
) -> dict[str, list[OptimizationIssue]]: ...

def analyze_assignment(
    cards: list[Card],
    purchases: list[Purchase],
    intent: Intent,
    assignments: dict[str, str],
) -> FeasibilityReport: ...
```

Use exact cents capacity. Utilization ceiling capacity in cents is the greatest assigned eligible spend satisfying the cross-multiplied inequality; do not derive it from displayed bps. Full-horizon credit capacity and dated utilization capacity are separate.

Analytical infeasibility proofs may include:

- Unknown/invalid lock.
- Locked purchase exceeds the locked card's capacity.
- A purchase fits no card even before considering other purchases.
- Total purchase spend exceeds total available credit.
- Purchases subject to a full-horizon utilization ceiling exceed total ceiling capacity.
- Forced bonus remaining spend exceeds total eligible planned spend.
- Forced bonus card cannot receive enough eligible spend under its own capacity.

These checks are necessary, not always sufficient. If none proves failure, do not report `infeasible` merely because greedy failed.

## 10. Single-purchase recommendation

Algorithm:

1. Validate IDs and intent references.
2. If locked, evaluate only the locked card and mark others excluded by lock.
3. For each permitted card in ID order, run feasibility against current balance.
4. For feasible cards, evaluate the one-assignment plan.
5. Sort by descending total utility, then card ID.
6. Return winner, runner-up, ranked candidates, excluded-card issues, and status.

This is exact enumeration, not a heuristic. Preserve each candidate breakdown so explanation can compare winner and runner-up without recalculation.

## 11. Greedy monthly allocator

### Initial order

Sort purchases by:

1. Locked before unlocked.
2. Descending amount.
3. Purchase ID.

Bonus deadline eligibility and completion utility affect candidate value, but version one has no separate continuous urgency multiplier and introduces no second undocumented purchase order.

### Assignment pass

For each purchase:

1. Determine feasible candidate cards under the current partial state.
2. Tentatively add the purchase to each candidate.
3. Compute marginal total utility including every changed aggregate card factor.
4. Select highest delta, then lexicographically ascending card ID.
5. Commit to a fresh state object or a clearly controlled mutable state.

If no candidate exists, run bounded repair:

- Try moving one prior unlocked purchase to a feasible alternate card, then place the blocked purchase.
- If configured depth is two, try two relocations with stable iteration and a visited assignment key.
- Never move a locked purchase.
- Accept the first complete repair in deterministic order; objective improvement is secondary to completion during repair.

If repair fails, run analytical proof checks. Return `infeasible` if proven, otherwise `unresolved` with `heuristic_dead_end`.

### Local search

After a complete plan:

1. Relocation phase: scan purchase IDs and alternate card IDs, accepting the best strict improvement in a pass.
2. Swap phase: scan unlocked purchase pairs on different cards, accepting the best strict improvement.
3. Restart relocation after an accepted swap.
4. Stop when a full relocation+swap cycle improves nothing or max passes is reached.

Always rerun full feasibility and objective evaluation before returning. Local search must never produce a worse utility than the initial complete greedy plan.

## 12. Exact ILP

### Assignment variables

For purchase `p` and card `c`:

```text
x[p,c] in {0,1}
sum_c x[p,c] == 1
```

Set incompatible/locked combinations to zero or avoid creating them. Force the locked combination to one.

### Credit and utilization constraints

For each card:

```text
current_balance + sum_p amount[p] * x[p,c] <= credit_limit
```

For an active ceiling, include only purchases through the cutoff when one exists and use the cross-multiplied linear constraint.

### Additive objective

Precompute cashback, travel value, and cashflow value for every feasible `(p,c)` pair. Multiply mapped utility by ppm weight. These are valid static coefficients.

### Aggregate utilization and headroom states

For every card with nonzero credit-health or risk weight, compute the finite set of assigned-spend totals reachable from the indivisible purchase amounts within available credit. Create one binary variable per reachable state, choose exactly one, and link the assignment spend expression to that selected state.

Precompute `incremental_utilization_penalty_points(card, state)` and `incremental_risk_penalty_points(card, state)` with the same pure Python functions used by `evaluate_plan`; add their signed integer coefficients directly to the objective. A full-horizon utilization ceiling reduces the state capacity before enumeration. Dated ceilings remain separate linear assignment constraints because post-cutoff spend can exceed the dated capacity.

Cap each card at `ilp_max_card_states`. Exceeding the cap is an explicit `heuristic_fallback`, never an approximation. This all-binary formulation avoids bundled CBC instability observed with large-coefficient general-integer floor variables while preserving exact Python/ILP parity.

### Signup bonus

For each active unmet bonus, compute reachable deadline-eligible spend totals within card capacity. Choose one binary state and link it to the eligible assignment expression. Precompute the exact capped progress, floored progress-pool value, and all-or-nothing completion value for every state. A forced bonus permits only states at or above remaining required spend.

The same state cap and honest fallback apply. This removes ambiguous one-way big-M indicators and matches `signup_bonus_factors` exactly, including non-divisible reward/threshold values.

### Deterministic tie-break

Build an integer secondary score preferring lower card IDs for each purchase. Let `max_secondary` be a proven upper bound. Optimize:

```text
primary_utility * (max_secondary + 1) + secondary_score
```

This guarantees the tie-break cannot change a one-unit primary preference in exact integer arithmetic. Compute and assert input-specific primary/secondary bounds remain below `2**53` for CBC. If they do not, use a two-pass solve (fix the proven primary optimum, then optimize the secondary score) or return `solver_error`; do not silently rely on imprecise coefficients.

### Status handling

- `LpStatusOptimal`: return exact plan as `optimal`.
- `LpStatusInfeasible`: return `infeasible` with analyzer diagnostics.
- Timeout/not solved/error: ignore any unverified CBC incumbent and call the tested greedy allocator. If it succeeds, return that plan as `heuristic_fallback` with `solver_method=greedy`; otherwise preserve `unresolved`/proven `infeasible` and include the exact-solver issue.

Every normal CBC call runs inside a spawned worker process. `ilp_timeout_seconds` is passed to CBC as its internal target, while `ilp_wall_timeout_seconds` is an absolute caller-side watchdog constrained to `1..60` seconds. If CBC ignores its internal timer, hangs in `wait()`, or exits natively, the parent kills the complete worker/CBC process tree and returns the verified greedy fallback. Injected test solvers remain in-process for deterministic unit testing.

Small assignment keys use one combined secondary solve. Larger keys use sequential lexicographic fixing to avoid numerically dangerous exponential objective coefficients. The process watchdog covers primary solving, tie-breaking, verification, and alternative generation. Do not claim the best incumbent is optimal. Record solve duration in debug logs, not deterministic response payloads.

## 13. Sampled strategy frontier

Record every positive original goal as `active_goal_ids`. Sweep the top two or three as `swept_goal_ids`; stable Goal order breaks equal weights. If fewer than two goals are positive, return the single base plan with a warning rather than inventing a tradeoff dimension.

- Two goals: use 5 weights from `(1,0)` through `(0,1)`.
- Three goals: enumerate nonnegative integer triples summing to denominator 4, producing 15 points.
- Enforce `frontier_max_solves=15` and a total frontier time budget; stop cleanly with partial-result warnings when exhausted.
- Preserve all hard constraints.
- Set non-swept goal weights to zero for the sweep. This is fixed v1 behavior, not a UI option, and result metadata discloses it.

For each successful plan record one unweighted comparison metric per selected goal:

- `max_cashback`: maximize cashback cents.
- `max_travel`: maximize static travel-value cents.
- `credit_health`: minimize aggregate credit penalty points.
- `hit_signup_bonus`: maximize unweighted signup goal points; retain earned bonus, hit count, and progress for display.
- `max_cashflow`: maximize cashflow carrying-value cents; retain days for display.
- `min_risk`: minimize aggregate risk penalty points.

Canonicalize assignment maps, remove duplicates, then discard a plan if another sampled plan is at least as good in every swept-goal direction and strictly better in one. Select up to 5 representatives: objective extremes first, then plans maximizing normalized distance from already selected points. For the 1-2 day build, a simpler deterministic evenly spaced selection after sorting is acceptable if documented and tested.

Result metadata includes `active_goal_ids`, `swept_goal_ids`, grid size, attempted/successful solves, solver statuses, truncation reason, and `complete_frontier=false` with the disclosure that unsupported or unsampled nondominated allocations may exist.

## 14. What-if

Validate purchase and card IDs. Clone purchases and set the selected purchase's `locked_card_id` to the override card. Solve base and override with the same requested method/config. This is a **reoptimized plan**: other purchases may move.

Return integer deltas `override - base` for reward value, max utilization, cashflow days/value, bonus progress, bonus earned, and total utility. If override is infeasible or unresolved, keep the base result and return structured override issues instead of fake deltas.

## 15. Tests and acceptance

### Models

- Reject negative money/rates and invalid days.
- Reject float/bool coercion for every integer money/rate/day field under strict Pydantic configuration.
- Reject bool-as-int and nonfinite weights.
- Normalize categories and forced IDs.
- Verify JSON serialization dates/enums.

### Dates and scoring

- Category rule and base fallback.
- Cashback and points floor at correct stages.
- Zero-limit reporting.
- Utilization band boundaries.
- Month/year statement and due rollover.
- Bonus before/on/after deadline and already-complete bonus.
- Cashflow values stay integer.

### Objective

- Ppm sums exactly for equal, sparse, repeating-decimal, and tiny weights.
- Aggregate evaluation matches hand calculations.
- Existing penalty baseline is not charged to plan delta.
- No money/factor output is float.

### Recommendation and greedy

- Stable ties.
- Locks and hard ceilings.
- Cumulative capacity.
- Repair recovers a crafted one-move dead end.
- Unrecovered dead end is `unresolved`, not `infeasible`.
- Local search never reduces utility.
- Repeat run deep-equals first run.
- Monthly alternatives equal hand-computed one-purchase moves from final state.

### ILP

- Compare with brute-force enumeration on multiple 2-3 card, 3-6 purchase fixtures.
- Exercise every hard constraint.
- Verify utilization piecewise parity.
- Verify bonus hit and partial progress.
- Verify timeout fallback status through an injected/mocked solver boundary.
- Verify the caller-side wall watchdog kills a sleeping worker and returns fallback.
- Assert optimal utility is at least greedy utility on the same feasible scenario.

### Frontier and what-if

- Dominance directions are correct.
- Duplicate assignments collapse.
- Labels/representatives are deterministic.
- Override can rearrange other purchases.
- Infeasible override returns issues and no deltas.

## 16. Completion checklist

- Public functions and outputs match integration contracts.
- No imports violate the dependency graph.
- All algorithms use the same `EngineConfig` and `evaluate_plan` semantics.
- Raw metrics reconcile to assignment/card summaries.
- Every failure has a stable issue code.
- Every successful result has an honest status.
- Focused tests and Ruff pass before API integration begins.
