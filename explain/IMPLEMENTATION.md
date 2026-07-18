# Explanation Layer Implementation Guide

## 1. Mission and boundary

The explanation layer turns engine facts into structured, faithful decision cards. It helps a user understand what was selected, which measurable factors mattered, why the strongest alternative lost or was infeasible, which constraints bind, and whether the result is exact or heuristic.

It does not:

- Call an LLM.
- Import or invoke scoring/objective/solver functions.
- Recompute rewards, utilization, cashflow, bonus progress, or utility.
- Invent confidence values.
- Describe partial bonus progress as earned reward.
- Describe a heuristic or sampled frontier as mathematically complete/optimal.

The module imports engine input/result models only. It remains pure: equal inputs produce deeply equal output.

## 2. Files

```text
explain/
  IMPLEMENTATION.md
  __init__.py
  models.py       # structured explanation output models
  formatters.py   # cents/bps/days formatting only
  builder.py      # recommendation and allocation explanation builders
  frontier.py     # strategy labels and tradeoff summaries
```

Templates belong in Python functions or small immutable constants. Do not add Jinja or a second templating dependency for this scope.

## 3. Public surface

```python
def explain_recommendation(
    result: RecommendationResult,
    cards: list[Card],
    purchase: Purchase,
    intent: Intent,
) -> RecommendationExplanation: ...

def explain_allocation(
    result: AllocationResult,
    cards: list[Card],
    purchases: list[Purchase],
    intent: Intent,
) -> AllocationExplanation: ...

def explain_frontier(
    result: FrontierResult,
) -> FrontierExplanation: ...

def explain_what_if(
    result: WhatIfResult,
) -> WhatIfExplanation: ...
```

Inputs must already agree by ID. If a referenced ID is absent because of an integration bug, raise a typed `ExplanationContractError`; do not silently omit the line.

## 4. Output models

Use Pydantic models with stable enums.

### ExplanationLine

- `kind`: `reward`, `travel`, `utilization`, `bonus`, `cashflow`, `risk`, `constraint`, `solver`, or `warning`.
- `tone`: `positive`, `neutral`, `caution`, or `blocking`.
- `label`: short machine-stable label.
- `text`: complete display text.
- `raw_value: int | None`.
- `unit`: `cents`, `bps`, `days`, `points`, `boolean`, or null.
- `source_path`: field path in engine output, for example `metrics.travel_value_cents`.
- `goal: Goal | None`.

### AlternativeExplanation

- Alternative card ID/name.
- `feasible`.
- Utility delta in internal points, clearly not formatted as money.
- Metric-delta lines.
- Blocking issue lines when infeasible.
- One concise summary sentence.

### DecisionCard

- Card and purchase identifiers/names.
- Headline.
- Solver status/method disclosure.
- Ordered factor lines.
- Alternative explanation, optional only when no other card exists.
- Binding/near-binding constraint lines.
- Warning lines.

### AllocationExplanation

- Plan summary lines.
- Card summary blocks.
- Per-purchase decision cards in purchase date/ID order.
- Highlighted purchase IDs (recurring rent first, then highest amounts).
- Failure block for `infeasible` or `unresolved` results.

The UI decides which cards are initially expanded. The explanation builder returns complete structured content.

## 5. Formatting rules

Formatting is deterministic and locale-fixed for the demo:

- Cents: `$42.00`, `-$11.25`; never convert through float.
- Basis points: `18.25%`; use quotient/remainder or `Decimal`.
- Days: `1 day` or `42 days`.
- Internal utility: integer plus label such as `2,275,000 utility points`, never `$`.
- Card/purchase names come from validated synthetic models and are escaped by the renderer.

Use ASCII source strings. The UI may pair lines with icons/colors based on `tone`; do not embed checkmark/warning glyphs in the text contract.

## 6. Recommendation explanation

### Winner facts

Start from `result.winner`. Candidate data already contains raw and weighted contributions.

Build factor lines in this order:

1. Hard-constraint outcome and utilization slack when a ceiling exists.
2. Projected cashback/travel reward.
3. Signup progress/completion.
4. Cashflow days and configured carrying value.
5. Credit-health/risk effects.

Suppress zero-valued reward/cashflow lines that add no decision information. Always retain hard-constraint lines and before/after utilization/headroom context. If a positively weighted goal has zero outcome that explains a tradeoff (for example, no bonus progress), show one neutral line in the summary rather than repeating zero on every card.

### Dominant factors

Rank nonzero `utility_by_goal` contributions by absolute magnitude. Use the top two or three to decide which lines receive `positive` or `caution` emphasis. Display the corresponding raw fact. For a negative credit contribution, say utilization increased and show before/after; do not say the engine is "less confident."

### Runner-up

Use `result.runner_up`, already ranked by the engine. Compare raw fields with exact integer deltas:

- Reward difference by type.
- Utilization-after difference.
- Bonus progress/completion difference.
- Float-day/value difference.
- Risk difference.

Lead with the factor(s) responsible for the largest weighted utility gaps, but phrase with raw measurements. Example: "Harbor Rent would earn $11.00 more cashback, but would finish at 34.20% utilization and breach Sarah's 30.00% ceiling." If an alternative is excluded, use its issue code/message rather than comparing a fake score.

## 7. Monthly allocation explanation

### Plan summary

Show:

- Solver status/method and whether exact optimality is proven.
- Projected reward value, split into cashback, static travel value, and newly earned signup bonuses.
- Maximum ending per-card utilization and the card where it occurs.
- Bonus hit/progress summaries by card.
- Total float days and configured carrying-value estimate.
- Warnings/assumptions such as static point valuation when travel value is nonzero.

Do not sum utilization percentages across cards. Do not call cashflow carrying value "interest saved" or guaranteed earnings.

### Per-card summary

For each card with existing or assigned balance:

- Assigned spend and purchase count.
- Ending balance/utilization.
- Credit-limit slack.
- Active utilization-ceiling slack.
- Bonus remaining/hit state.

Slack exactly zero is `binding`. Positive slack at or below the engine-provided/configured near-binding threshold is `near-binding`. Never inspect PuLP dual values.

### Per-purchase why-not comparison

Use `PurchaseAssignment.alternatives`; these are final-plan one-purchase relocations computed by the engine.

1. Select the first feasible alternative when available.
2. Show metric deltas and total-utility direction.
3. If none is feasible, choose the first infeasible alternative in stable card order and explain its top blocking issue.
4. Never call a stateless recommender for comparison: doing so would ignore capacity, bonus, and utilization effects of other purchases.

If the plan is ILP-optimal, every feasible one-purchase move should be non-improving. For a greedy plan after local search, the same should hold within supported relocation search; if an improving alternative appears, emit an internal warning because solver trace and status disagree.

## 8. Failure explanations

For `infeasible`:

- Headline: no plan satisfies all hard constraints in the modeled scenario.
- List issues in analyzer order.
- Show affected card/purchase names.
- Present concrete engine-provided suggestions.
- State whether infeasibility was proven by CBC or an analytical contradiction when metadata permits.

For `unresolved`:

- Headline: the heuristic did not find a complete plan.
- Do not state that no plan exists.
- Suggest trying the exact solver or relaxing the specific blocked assignment/constraint identified by the search trace.

For `heuristic_fallback`:

- Display that the exact solver did not finish/failed and the shown plan is a verified feasible heuristic.

## 9. Sampled frontier explanations

Labels derive from the sampled weight vector and objective metric, not arbitrary index. The result distinguishes all positive `active_goal_ids` from the two or three `swept_goal_ids`; labels and dominance claims mention swept goals only:

- Pure/extreme point: `Max cashback`, `Max travel value`, `Best credit health`, `Best bonus progress`, `Best cashflow`, or `Most headroom`.
- Mixed interior point: `Balanced: cashback + credit health` using selected goals ordered by weight.
- Duplicate labels receive a short deterministic suffix based on rank, not random IDs.

For each point, describe exact tradeoffs against a designated balanced/reference point using selected goal metrics. Always include:

> Sampled from N weight settings across [swept goals]; other nondominated allocations may exist, including tradeoffs involving non-swept goals.

Do not say "the Pareto frontier is complete."

## 10. What-if explanations

State that the override locks one purchase to another card and reoptimizes the rest of the plan. Show signed deltas for projected reward, max utilization, bonus progress/earned value, cashflow, and utility. Name other purchases that moved if the engine result provides assignment diffs.

If override is infeasible/unresolved, explain why and do not display null deltas as zeros.

## 11. Wording constraints

Approved distinctions:

- "Projected cashback" versus "static travel-point value."
- "Makes $X of qualifying spend progress" versus "earns the signup bonus."
- "Configured carrying-value estimate" versus "interest-free float days."
- "Hard ceiling" versus "preference penalty."
- "Optimal under the modeled inputs" only for `optimal`.
- "Heuristic plan" or "fallback plan" for other successful statuses.

Forbidden wording:

- Guaranteed savings/returns.
- Predicted credit-score increase/decrease.
- Confidence percentage on deterministic calculations.
- Real issuer endorsement.
- Complete frontier claim.
- Bonus earnings before threshold completion.

## 12. Tests

- Every displayed number equals a cited engine field or exact delta.
- Money and percentages format without float.
- Winner/runner-up factor direction is correct.
- Monthly alternative uses final-state trace, not a fresh score.
- Infeasible alternatives show issue codes and no score gap.
- Binding versus near-binding uses provided slack exactly.
- Partial signup progress never says earned.
- All five statuses produce honest solver disclosure.
- Static point-value warning appears when travel value is shown.
- Frontier disclosure includes attempted grid size and incompleteness.
- Equal input produces identical output/order.

Use small hand-built engine result fixtures. Add one integration test using real engine output, but keep most template tests independent of solver details.

## 13. Completion checklist

- Explanation builders import no scoring/solver functions.
- Structured models support API serialization without UI parsing prose.
- Every line has a source path or explicit issue source.
- Recommendations and monthly plans include a faithful alternative.
- Failure and solver-status wording is honest.
- No confidence or LLM-generated financial prose exists.
