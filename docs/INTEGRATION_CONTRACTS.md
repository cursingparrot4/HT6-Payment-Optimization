# Integration Contracts

This document is the shared boundary between parallel workstreams. It specifies ownership, dependency direction, canonical semantics, and serialized shapes. It is deliberately stricter than a sketch: implementation can evolve internally as long as these observable contracts remain stable.

## 1. Contract ownership

| Contract | Canonical implementation | Consumers |
|---|---|---|
| Card, purchase, bonus, goal, constraints, intent | `engine/models.py` | All modules |
| Weight quantization and objective utility | `engine/objective.py` | Recommender and allocators |
| Feasibility and issue codes | `engine/feasibility.py` | Recommender, allocators, API, explain |
| Optimization results and metrics | `engine/models.py` | Explain, API, eval |
| Parser metadata and fallback state | `intent/models.py` | API, UI, eval |
| Explanation blocks | `explain/models.py` | API, UI |
| HTTP request and response wrappers | `api/schemas.py` | UI and API tests |
| Public product provenance and synthetic account/scenario documents | `data/models.py` | Data loaders, API, UI, eval |

No consumer should copy a Pydantic model just to avoid an import. If a consumer needs a presentation-only shape, it may define that shape locally but cannot change the meaning of a domain field.

## 2. Primitive representations

### Money and value

- Currency is an integer number of cents: `220000` means `$2,200.00`.
- Point values are integer millicents per point: `1250` means `1.25` cents per point.
- Rates and utilization are integer basis points: `300` means `3%`; `2750` means `27.50%`.
- Intent weights enter the engine as integer parts-per-million: `450000` means `45%` of preference weight.
- Dates serialize as ISO 8601 calendar dates, for example `2026-10-18`.
- IDs are stable, opaque, nonempty ASCII strings. IDs are compared case-sensitively.
- Display formatting happens only in the explanation/UI layers.

The parser may temporarily use finite decimal/float JSON values because JSON has one number type. It must reject `NaN`, infinity, negative values, and an all-zero vector. The exact sequence is: provider/API JSON numbers -> strict parser/domain validation -> normalized interchange `Intent` -> one conversion in `engine/objective.py` to ppm -> integer-only objective and solver arithmetic. No parser/provider independently invents ppm fields.

Every domain Pydantic model uses `ConfigDict(extra="forbid")`. Integer money/rate/day fields use `StrictInt` or `Field(strict=True)` so they reject floats, booleans, and numeric strings instead of coercing them. Date fields still accept ISO strings from fixture/API JSON. Intent weights are the documented numeric-interchange exception and receive explicit finite-number validation.

### Rounding

- Reward accrual floors at the final integer division required by the reward rule.
- Point conversion floors after multiplying accrued points by millicents-per-point.
- Utilization uses floor division for reporting, but hard feasibility uses cross multiplication to avoid accepting an amount merely because displayed bps rounded down.
- Weight quantization uses `Decimal(str(value))` and largest remainder so all six ppm values sum exactly to `1_000_000`.
- Display percentages and dollars may use `Decimal`; they never feed back into optimization.

## 3. Canonical domain models

The examples below are JSON shapes. Python uses Pydantic v2 and string enums.

### RewardRule

```json
{
  "category": "groceries",
  "rate_bps": 400,
  "reward_type": "points"
}
```

Rules:

- `category` is normalized lowercase snake case.
- `rate_bps` is nonnegative.
- `reward_type` is one of `cashback`, `points`, or `miles`.
- A card cannot contain duplicate rule categories.
- The first exact category match is used. If none matches, `base_rate_bps` and the card's `base_reward_type` are used.
- A rule for `other` is ordinary category data, not a second fallback mechanism.

The original sketch did not define the reward type for the base rate. The implementation adds `base_reward_type` to remove that ambiguity.

### SignupBonus

```json
{
  "spend_required_cents": 400000,
  "spend_so_far_cents": 125000,
  "reward_value_cents": 60000,
  "deadline_date": "2026-10-31"
}
```

All values are nonnegative. `spend_so_far_cents` may exceed the requirement in imported data; the engine clamps remaining spend to zero without changing source data. Planned purchases count only when their date is on or before the deadline. The MVP assumes every purchase category is bonus-eligible.

### Card

```json
{
  "id": "summit-journey",
  "name": "Summit Journey (synthetic)",
  "credit_limit_cents": 1200000,
  "current_balance_cents": 85000,
  "reward_rules": [],
  "base_rate_bps": 100,
  "base_reward_type": "points",
  "point_value_millicents": 1250,
  "annual_fee_cents": 9500,
  "statement_day": 12,
  "due_day": 7,
  "signup_bonus": null
}
```

Rules:

- Limits, balances, rates, values, and fees are nonnegative integers.
- A zero-limit card validates as input but is infeasible for new purchases.
- `statement_day` and `due_day` are between 1 and 28 inclusive.
- `current_balance_cents` greater than the limit is valid imported state but makes new assignments infeasible and produces an issue.
- `point_value_millicents` is used only for point/mile rules. Cashback already represents cents.
- `annual_fee_cents` is metadata and a disclosed sunk-cost assumption, not a monthly assignment factor.

### Purchase

```json
{
  "id": "rent-2026-08",
  "amount_cents": 220000,
  "category": "rent",
  "date": "2026-08-01",
  "is_recurring": true,
  "locked_card_id": null
}
```

Rules:

- Amount must be a positive integer.
- Category normalization matches reward-rule normalization.
- Purchase IDs are unique inside a request.
- A lock references a card in the same request. Unknown locks produce structured infeasibility, not a low-level key error.
- Purchases are indivisible.

### Goal and Intent

The six and only six goal keys are:

```text
max_cashback
max_travel
credit_health
hit_signup_bonus
max_cashflow
min_risk
```

Engine-facing intent JSON contains every key:

```json
{
  "weights": {
    "max_cashback": 0.10,
    "max_travel": 0.10,
    "credit_health": 0.45,
    "hit_signup_bonus": 0.25,
    "max_cashflow": 0.05,
    "min_risk": 0.05
  },
  "constraints": {
    "max_utilization_bps": 3000,
    "max_utilization_until": "2026-11-01",
    "must_hit_bonus_card_ids": ["harbor-bonus"]
  }
}
```

Validation rules:

- All six keys are present after parser normalization.
- Every value is finite and nonnegative.
- The vector contains at least one positive value.
- `Intent` normalizes values for ergonomic API interchange; `objective.py` alone produces canonical ppm immediately before any recommendation/allocation scoring.
- `max_utilization_bps` is absent or between `0` and `10000`.
- `max_utilization_until` requires `max_utilization_bps`; a ceiling without a date applies to the full planning horizon.
- `must_hit_bonus_card_ids` is unique and stable-sorted after validation.
- Request-level validation checks that forced-bonus IDs exist and have bonuses.

`max_utilization_bps` is a per-card ceiling, not portfolio-wide utilization. Total portfolio utilization does not change when a fixed total purchase amount moves among cards, so per-card concentration is the meaningful assignment constraint.

## 4. Raw factors and objective utility

Every candidate or assignment exposes raw facts separately from utility contributions.

### Raw factor breakdown

```json
{
  "cashback_cents": 0,
  "travel_value_cents": 3300,
  "signup_eligible_spend_cents": 220000,
  "signup_progress_cents": 220000,
  "signup_bonus_earned_cents": 0,
  "signup_goal_points": 30000,
  "cashflow_days": 37,
  "cashflow_value_cents": 111,
  "utilization_before_bps": 708,
  "utilization_after_bps": 2541,
  "credit_penalty_points": 0,
  "risk_penalty_points": 0
}
```

Raw fields are exact under documented assumptions. `signup_progress_cents` is spend progress, not reward money. `signup_bonus_earned_cents` is nonzero only if the evaluated plan reaches the threshold by the deadline.

### Weighted objective breakdown

```json
{
  "utility_by_goal": {
    "max_cashback": 0,
    "max_travel": 82500000,
    "credit_health": -12000000,
    "hit_signup_bonus": 30000000,
    "max_cashflow": 2775000,
    "min_risk": 0
  },
  "total_utility": 103275000
}
```

These values are integer comparison units, not cents and not user-facing money. Each goal follows:

$$
U_g = w_g^{ppm} \times f_g(raw, config)
$$

The common factor of one million does not need to be divided out for ranking. Calibration functions and constants live in `engine/config.py`; both greedy and ILP implementations use the same configuration. Changing calibration requires objective tests and a Sarah-scenario snapshot review.

Goal mapping:

| Goal | Positive signal | Negative signal |
|---|---|---|
| `max_cashback` | Cashback cents | None |
| `max_travel` | Static cents value of points/miles | None |
| `credit_health` | None | Convex aggregate per-card utilization penalty |
| `hit_signup_bonus` | Capped progress utility plus earned bonus value | None |
| `max_cashflow` | Carry-value cents and reported float days | None |
| `min_risk` | None | Near-limit headroom penalty; never duplicates a hard violation |

Hard violations are excluded before scoring. A huge risk penalty is not a substitute for a constraint.

## 5. Feasibility contract

The shared analyzer receives cards, purchases, constraints, and optionally a proposed assignment map. It returns issues and per-card slack. The optimizer does not duplicate these rules.

Checks run in this order so diagnostics are deterministic:

1. Duplicate IDs and unknown card references.
2. Locked purchase validity.
3. Individual purchase capacity.
4. Full-horizon card credit limits.
5. Active dated or full-horizon per-card utilization ceilings.
6. Forced bonus existence, deadline eligibility, available eligible spend, and capacity.

For a dated utilization ceiling, use:

$$
10000 \times (current\ balance + eligible\ assigned\ spend)
\leq max\ utilization\ bps \times credit\ limit
$$

This cross-multiplied comparison is exact. Eligibility is inclusive: `purchase.date <= max_utilization_until`. For a `2026-10-31` cutoff, a purchase on `2026-10-31` counts and one on `2026-11-01` does not. If no purchase in the evaluated horizon is on/before the cutoff, that dated ceiling is inactive for routing; all assigned spend still remains subject to the full credit limit. A ceiling without a cutoff applies to the full horizon.

### Stable issue codes

| Code | Meaning |
|---|---|
| `duplicate_id` | Duplicate card or purchase ID |
| `unknown_locked_card` | Purchase lock references an absent card |
| `unknown_purchase` | What-if or assignment input references an absent purchase |
| `unknown_assigned_card` | Assignment references an absent card |
| `missing_assignment` | A purchase has no assignment entry |
| `purchase_locked_to_other_card` | Assignment/candidate conflicts with a valid purchase lock |
| `unknown_bonus_card` | Forced bonus references an absent card |
| `card_has_no_bonus` | Forced card has no signup bonus |
| `zero_credit_limit` | Card cannot accept spend |
| `card_already_over_limit` | Imported balance is already above limit |
| `purchase_exceeds_capacity` | One purchase cannot fit a required/available card |
| `credit_limit_exceeded` | Proposed aggregate spend exceeds a card limit |
| `utilization_ceiling_exceeded` | Proposed dated/full-horizon spend breaches the ceiling |
| `bonus_deadline_passed` | A forced bonus cannot receive eligible planned spend |
| `bonus_target_unreachable` | Eligible purchases/capacity cannot satisfy forced remaining spend |
| `no_feasible_assignment` | No complete indivisible assignment exists |
| `heuristic_dead_end` | Greedy and bounded repair did not find a complete plan; feasibility is unproven |
| `solver_timeout` | Exact solve did not finish in the configured limit |
| `solver_error` | Exact solver failed unexpectedly |

Normal exact solves are isolated behind a caller-side wall-clock watchdog. The configured limit is 1-60 seconds; timeout or native worker failure cannot block the API indefinitely and returns an honestly labeled greedy fallback when available. Sampled-frontier solves inherit the smaller remaining total frontier budget.

Every issue contains `code`, `message`, affected card/purchase IDs, optional integer `actual`, optional integer `required`, and a concrete `suggestion`. Messages are presentation-ready but tests assert codes and values rather than full prose.

## 6. Optimization results

### Status and method

`status` is one of:

- `optimal`: complete single-purchase enumeration or CBC proved the modeled optimum.
- `heuristic`: requested greedy/local-search result.
- `heuristic_fallback`: greedy result returned because exact solving timed out or errored.
- `infeasible`: enumeration, an analytical contradiction, or CBC proved that no complete assignment satisfies all hard constraints.
- `unresolved`: a heuristic failed to complete an assignment and no infeasibility proof is available.

`solver_method` is `single_purchase`, `greedy`, or `ilp`. An ILP request that falls back reports `solver_method=greedy`, `status=heuristic_fallback`, and includes the exact-solver issue.

### Assignment and card summary

```json
{
  "purchase_id": "rent-2026-08",
  "card_id": "summit-journey",
  "raw_factors": {},
  "objective": {},
  "alternatives": [
    {
      "card_id": "harbor-rent",
      "feasible": true,
      "resulting_plan_utility": 101000000,
      "total_utility_delta": -2275000,
      "metric_deltas": {
        "projected_reward_value_cents": 1100,
        "max_card_utilization_bps": 450,
        "cashflow_days_total": -8,
        "signup_bonus_earned_cents": 0
      },
      "issues": []
    }
  ]
}
```

For a monthly plan, `alternatives` are computed by the optimizer against the final complete assignment: move this one purchase to another card, keep all other assignments fixed, and recompute feasibility and aggregate metrics. Feasible alternatives sort by descending resulting plan utility then card ID; infeasible alternatives follow in card-ID order with issue codes. The explanation layer consumes these values and never reconstructs a stateless comparison. The selected card is not repeated in the alternatives list.

Monthly `PurchaseAssignment.raw_factors` contains additive reward/cashflow facts and the card's final ending utilization for context. Aggregate signup progress/completion and credit/risk penalties remain in card summaries and allocation metrics rather than being arbitrarily attributed to individual purchases. Consequently, per-purchase objective contributions do not sum to the plan objective; the plan objective is authoritative.

```json
{
  "card_id": "summit-journey",
  "assigned_purchase_ids": ["rent-2026-08"],
  "assigned_spend_cents": 220000,
  "ending_balance_cents": 305000,
  "ending_utilization_bps": 2541,
  "credit_limit_slack_cents": 895000,
  "utilization_slack_cents": 55000,
  "bonus_eligible_spend_cents": 220000,
  "bonus_remaining_cents": 55000,
  "bonus_hit": false,
  "cashflow_days_total": 37
}
```

`utilization_slack_cents` is null when no hard utilization ceiling applies. Zero means binding. Positive values below a configurable display threshold are near-binding, not binding.

### Allocation result

```json
{
  "status": "heuristic",
  "solver_method": "greedy",
  "assignments": [],
  "card_summaries": [],
  "metrics": {
    "cashback_cents": 0,
    "travel_value_cents": 0,
    "signup_bonus_earned_cents": 0,
    "signup_goal_points": 0,
    "projected_reward_value_cents": 0,
    "max_card_utilization_bps": 0,
    "credit_penalty_points": 0,
    "risk_penalty_points": 0,
    "cashflow_days_total": 0,
    "cashflow_value_cents": 0,
    "total_utility": 0
  },
  "issues": [],
  "warnings": []
}
```

For `infeasible` or `unresolved`, assignment and summary lists are empty and issues are nonempty. `projected_reward_value_cents` equals cashback plus static travel value plus signup bonuses actually reached; it does not include signup progress utility or cashflow carrying value.

### Sampled frontier objective dimensions

Frontier metadata distinguishes `active_goal_ids` (all goals with positive original weight) from `swept_goal_ids` (the top two or three actually varied). Dominance compares one unweighted, direction-aware metric for each swept goal only. It never compares blended total utility or silently claims coverage over non-swept goals:

| Goal | Frontier metric | Direction |
|---|---|---|
| `max_cashback` | `cashback_cents` | Maximize |
| `max_travel` | `travel_value_cents` | Maximize |
| `credit_health` | `credit_penalty_points` | Minimize |
| `hit_signup_bonus` | `signup_goal_points` | Maximize |
| `max_cashflow` | `cashflow_value_cents` | Maximize |
| `min_risk` | `risk_penalty_points` | Minimize |

`signup_goal_points` is the unweighted deterministic signup-progress/completion calibration from the shared engine config. The result also exposes earned bonus cents, hit count, and spend progress so the UI can explain it. Using one scalar per selected goal keeps dominance well-defined; scaling does not affect Pareto dominance.

### Single-purchase recommendation

The result contains `winner`, optional `runner_up`, all feasible ranked candidates, excluded cards with issue codes, and warnings. If no card is feasible, status is `infeasible` and winner is null. Locked purchases evaluate only the locked card while still returning why invalid alternatives were excluded.

## 7. Parser contract

The provider boundary returns raw text; it never constructs engine objects directly:

```text
IntentProvider.generate(text, reference_date) -> ProviderResponse
```

`ProviderResponse` contains raw output, provider name, model ID, latency, and optional request metadata. It never stores an API key.

Post-processing performs these steps in order:

1. Extract one JSON object, including from a fenced response.
2. Parse JSON without permissive `NaN` support.
3. Map known goal keys; reject unknown constraint fields.
4. Fill omitted goal keys with zero.
5. Validate constraints and all numeric values.
6. Normalize positive weights and create `Intent`.
7. Return source metadata and warnings.

On terminal failure, demo mode creates equal importance by assigning `1.0` to each goal before normalizing and returns no constraints with `used_fallback=true`. Canonical engine ppm then becomes `166667` for the first four goals in enum order and `166666` for the last two, totaling exactly `1_000_000`. Eval mode raises a typed parse failure so invalid-JSON rate remains measurable. Relative dates are not guessed in post-processing; prompts include `reference_date` and require absolute ISO output.

## 8. Explanation contract

The explanation builder consumes only optimization results, cards, purchases, and intent. It does not import scoring helpers. Its structured output includes:

- Headline with chosen card and purchase/plan context.
- Positive and caution factor lines with machine-readable factor kind.
- Raw amount, unit, formatted text, and source field for every line.
- Alternative comparison identifying the next feasible card or explaining exclusion.
- Binding and near-binding constraint lines based on engine-provided slack.
- Solver-status disclosure and warnings.

The builder must never say a partial signup progress amount was earned, attach confidence to deterministic math, or describe a heuristic result as optimal.

## 9. API contract

All endpoints return HTTP 200 for valid requests even when the domain result is infeasible. FastAPI uses 422 for malformed request structure and 503 only when an explicitly required external provider is unavailable and fallback is disabled.

| Endpoint | Request | Response payload |
|---|---|---|
| `GET /demo-scenario` | none | Sarah's synthetic scenario and named manual intent presets |
| `POST /parse-intent` | text, required reference date, card context/scenario, optional allowed provider | Parse result with intent/source/warnings |
| `POST /recommend` | cards, purchase, intent | Recommendation plus explanation |
| `POST /allocate` | cards, purchases, intent, solver preference | Allocation plus explanation blocks |
| `POST /frontier` | cards, purchases, intent, max points | Sampled frontier metadata and plans |
| `POST /what-if` | base scenario, purchase ID, override card ID, solver preference | Base/override summaries and integer deltas |
| `GET /health` | none | Process, fixture, solver, and provider readiness |

Every response uses a top-level envelope: `{"schema_version": "1.0", "data": <endpoint payload>, "warnings": []}`. Domain warnings may also remain inside a result when tied to that result; envelope warnings describe orchestration/provider behavior. Deterministic endpoints do not add random IDs or timestamps so equal inputs can be compared byte-for-byte after canonical JSON serialization.

## 10. Evaluation contract

Evaluation never uses parser fallback. Each model runner is named and produces cached raw responses. All runners receive the same system contract, reference date, and held-out examples.

Headline downstream match uses a fixed suite of single-purchase probes spanning rent, grocery, dining, and travel. For each held-out intent, compare the card selected from predicted intent against the card selected from gold intent. Report monthly per-purchase assignment agreement separately; do not collapse it into the headline metric.

Required report columns:

- Valid JSON and schema rate.
- Six-goal macro mean absolute error.
- Constraint field exact match and per-field precision/recall/F1.
- Downstream top-card match with bootstrap 95% interval.
- Monthly assignment agreement.
- Provider/model identity, sample count, dataset hash, prompt version, and fallback count (must be zero).

## 11. Determinism rules

- Input lists are canonicalized by stable IDs before tie-breaking.
- Recommendation ties prefer lexicographically smaller card IDs.
- Greedy ordering is locked purchases first, then descending amount, then purchase ID.
- Local search scans purchase IDs then card IDs in stable order and accepts only strict utility improvement.
- ILP multiplies primary utility by a proven bound larger than the complete secondary tie score before adding deterministic tie coefficients. The implementation must assert the resulting coefficient/objective bound is below `2**53` for CBC's numeric representation under accepted inputs; otherwise it uses a two-pass primary-then-secondary solve or returns a typed solver error rather than silently risking a changed primary optimum.
- Frontier assignment keys sort `(purchase_id, card_id)` pairs.
- Synthetic generation and bootstrap evaluation require explicit seeds.

## 12. Contract-change checklist

Before merging a shared contract change:

1. Update the canonical Pydantic model and JSON schema snapshot.
2. Update this document and the owner module guide.
3. Update request/response examples if serialized output changed.
4. Add migration notes for fixture files.
5. Run model, API contract, parser, and explanation tests.
6. Notify both implementers before rebasing parallel work.
