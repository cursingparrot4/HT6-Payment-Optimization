# Logic Review and Decision Record

This document reviews the product and technical logic before implementation. It records assumptions that are easy for parallel contributors to interpret differently. The goal is not to make a prototype look more certain than it is; the goal is to make every simplification explicit and internally consistent.

## 1. Executive verdict

The central architecture is sound:

- Reward accrual, balance capacity, utilization, deadlines, and payment timing are deterministic calculations and belong in code/optimization, not ML.
- Natural-language intent is uncertain and belongs behind a validated model boundary.
- A solver can evaluate whether parser error changes an actual decision, making downstream match a useful task metric.
- Structured templates are more faithful than generated prose for financial explanations.
- Monthly allocation is a more differentiated deliverable than a single-card recommendation.

The original proposal needed corrections in five areas before an exactness claim was defensible:

1. Objective values used incompatible units without calibration.
2. Static per-purchase ILP coefficients could not represent aggregate utilization or all-or-nothing bonus completion.
3. A greedy allocator was described as guaranteed even though greedy search can dead-end on a feasible instance.
4. A weighted sweep was described as the Pareto frontier even though it samples only supported solutions and may miss integer frontier points.
5. Temporal balance behavior was under-specified relative to statement dates.

The canonical plan resolves all five. None invalidates the core idea.

## 2. Decision table

| ID | Question | Decision | Reason |
|---|---|---|---|
| D001 | Are reward calculations learned? | No; pure integer functions | Rates and balances are known inputs, not predictions |
| D002 | What may the LLM output? | Only `Intent` weights and hard constraints | Keeps uncertain language outside the money path |
| D003 | What numeric form do intent weights use in the engine? | Integer ppm summing to `1_000_000` | Prevents float propagation and gives deterministic ties |
| D004 | How are different objectives compared? | Raw facts map to documented integer utility, then weights apply | Cents, bps, and days are not directly commensurate |
| D005 | What does utilization ceiling mean? | Per-card hard ceiling | Overall utilization is invariant to routing a fixed spend total |
| D006 | Do statements reset balances in the MVP? | No | Payments and historical ledger are absent; pretending to reset would be less correct |
| D007 | What do statement dates affect? | Interest-free float calculation only | This is supportable from the available fields |
| D008 | Are annual fees part of routing? | No; display as sunk portfolio metadata | The month cannot avoid a fee on a card the user already holds |
| D009 | When is a signup bonus displayed as reward? | Only when the plan reaches it by the deadline | Partial progress is not earned money |
| D010 | Can partial bonus progress influence routing? | Yes, through a capped 20% utility pool | Prevents zero incentive until the threshold while limiting double counting |
| D011 | Is greedy failure proof of infeasibility? | No; return `unresolved` absent proof | Heuristic completeness is not guaranteed |
| D012 | When can the product say optimal? | Enumeration for one purchase or CBC optimal status | Honest claim tied to a proof-producing method |
| D013 | What is the frontier? | A sampled strategy frontier with exact dominance filtering over sampled plans | Weighted sweeps can miss nondominated integer solutions |
| D014 | What is parser fallback? | Equal weights/no constraints plus visible warning | Matches the original contract; manual correction keeps it honest |
| D015 | Does eval permit fallback? | Never | Otherwise model attribution and valid-output rates are corrupted |
| D016 | Does the UI calculate scores? | Never | Prevents drift from engine behavior |
| D017 | Can one purchase be split? | No | Matches real card routing and keeps the assignment model tractable |
| D018 | Are point values dynamic? | No; static per synthetic card | Award inventory and redemption modeling are explicit non-goals |
| D019 | Does bonus value receive a continuous deadline-urgency multiplier? | No in v1 | Deadline eligibility and completion already provide temporal behavior; an extra curve is uncalibrated and complicates exact ILP parity |
| D020 | Where are intent weights quantized? | `engine/objective.py`, once | Parser/domain validation owns interchange normalization; optimizer owns ppm arithmetic |

## 3. Objective soundness

### 3.1 Why raw weighted sums are insufficient

Consider a reward of `4,400` cents, an ending utilization of `3,400` bps, and `35` float days. Multiplying each directly by a user weight implies that a basis point, a cent, and a day have the same scale. The resulting preference sliders would be misleading: a small numerical change in one factor could overwhelm a high stated weight in another.

The engine therefore keeps two layers:

1. **Raw facts:** exact cents, bps, spend progress, and days used for metrics and explanations.
2. **Utility points:** integer, documented calibration outputs used only for ranking and solving.

Positive money-valued factors can use one utility point per value cent. Credit-health utility uses a convex penalty schedule calibrated against the synthetic scenario. Risk utility uses ending credit-headroom shortfall. This calibration is a product assumption, not a financial forecast, and belongs in `engine/config.py` with tests.

### 3.2 Proposed default calibration

The first implementation should use these reviewable defaults:

- Cashback: `1` utility point per reward cent.
- Travel: `1` utility point per static travel-value cent.
- Cashflow: `1` utility point per carrying-value cent at `500` annual bps.
- Signup progress: up to `20%` of bonus value, proportional to remaining spend completed.
- Signup completion: the remaining `80%` when the threshold is reached on time.
- Credit health: no incremental penalty through 30% utilization, then convex slopes per bps across 30-50%, 50-75%, 75-100%, and above 100% imported state.
- Minimum risk: ending credit headroom shortfall below a configured reserve, measured in cents and mapped one-for-one to penalty points.

The exact utilization slopes are defaults, not universal truths. They must produce intuitive Sarah-scenario behavior at equal weights and visibly respond when credit-health weight increases. Snapshot tests protect calibration from accidental drift but should not pretend it is externally validated financial advice.

There is no continuous deadline-urgency multiplier in v1. A purchase after the deadline is ineligible, and reaching the threshold earns completion utility; this is enough temporal behavior for the available fields. Add an urgency curve only with a documented calibration and matching Python/ILP formulation.

### 3.3 Aggregate versus marginal values

Cashback, travel value, and cashflow are additive by purchase-card pair. Utilization penalty, risk headroom, and bonus completion are aggregate by card. A valid monthly objective is:

$$
U(plan) = \sum_{p,c} x_{p,c} U_{additive}(p,c)
- \sum_c U_{util}(ending_c)
- \sum_c U_{risk}(ending_c)
+ \sum_c U_{bonus}(eligibleSpend_c)
$$

Greedy candidate selection must calculate the change in this plan-level utility when a purchase moves. The ILP must introduce aggregate variables for the nonlinear pieces. Reusing static `factor_breakdown(card, purchase)` as the entire monthly coefficient would be simpler but logically wrong.

## 4. Financial arithmetic review

### Rewards

For cashback:

$$
rewardCents = \left\lfloor \frac{amountCents \times rateBps}{10000} \right\rfloor
$$

For points or miles:

$$
points = \left\lfloor \frac{amountCents \times rateBps}{10000} \right\rfloor
$$

$$
valueCents = \left\lfloor \frac{points \times pointValueMillicents}{1000} \right\rfloor
$$

This interpretation treats `300` bps as 3 points per dollar for a point rule. Synthetic fixture documentation must say so.

### Utilization

Reported utilization may floor to integer bps. Hard feasibility must use cross multiplication rather than the floored display value. A card with zero limit is valid input for diagnostics but infeasible for new spend.

Per-card utilization matters for routing. Aggregate portfolio utilization after assigning a fixed set of purchases is:

$$
\frac{\sum balances + \sum purchases}{\sum limits}
$$

and does not change based on which card receives each purchase. It may be useful display context, but it cannot distinguish allocations.

### Cashflow

The next statement close is the first card statement day on or after the purchase date. The due date is the first card due day strictly after that close. Purchase-on-close-day behavior is intentionally conservative: it is treated as appearing on that close. Float days are calendar-day difference from purchase to due date.

Carrying value is:

$$
\left\lfloor
\frac{amountCents \times annualCarryBps \times floatDays}{10000 \times 365}
\right\rfloor
$$

This is opportunity-cost utility, not card interest and not guaranteed earnings. The UI should emphasize days and treat cents as a configured estimate.

### Signup bonus

Only spend dated on/before the deadline is eligible. The MVP assumes all categories count. The bonus value is static synthetic value. If the user forces a bonus that is already achieved, the constraint is satisfied and adds no new completion reward; the existing reward is not attributed to this plan.

## 5. Temporal-model limitations

The input lacks transaction posting timestamps, payment events, statement balances, grace-period state, and account opening date. A fully faithful ledger simulator is therefore out of scope.

The MVP uses these assumptions:

- `current_balance_cents` remains outstanding throughout the planning horizon.
- Every planned purchase immediately consumes available credit.
- A dated utilization ceiling counts current balance plus purchases dated through the cutoff.
- Purchases after the cutoff do not count toward that ceiling but still count toward the credit limit and ending utilization.
- Statement day changes float timing only.
- Due day does not imply an automatic payment.

These assumptions are conservative for capacity and transparent for a hackathon. Adding payments without payment amounts and dates would create false precision.

## 6. Search and solver review

### Single purchase

Enumerating all cards is exact for one indivisible purchase. Stable card-ID tie-breaking makes equal-input outputs deterministic. This result may be labeled `optimal` within the modeled objective and constraints.

### Greedy monthly allocation

Largest-first assignment tends to protect capacity for expensive purchases but can still choose an early card that blocks a later locked or bonus-critical assignment. Locked purchases should be placed first. Candidate ordering should use marginal plan utility and a stable tie-break. A bounded repair phase can relocate one or two prior purchases when a dead end occurs.

Even with repair, failure is not a proof. Return:

- `heuristic` for a complete feasible plan.
- `infeasible` only when an analytical contradiction is proven, such as a locked purchase exceeding card capacity or total purchases exceeding total available capacity.
- `unresolved` when search ends without a complete plan and no proof exists.

### Exact ILP

Binary assignment variables represent indivisible purchase routing. Additive rewards and cashflow are constant coefficients. Dated ceilings are linear. Aggregate utilization/risk and bonus utility use capped sets of reachable spend totals with one binary state selected per card. Every state coefficient is precomputed by the same pure Python evaluator; scenarios exceeding the state cap fall back honestly rather than approximating.

CBC optimal status supports an `optimal` label. CBC infeasible status supports an `infeasible` label. A timeout with an incumbent is not proven optimal; for predictable API behavior, return the independently verified greedy plan as `heuristic_fallback` and include the timeout issue rather than exposing an ambiguous incumbent in the first version.

### Frontier

Weighted sums find supported solutions but discrete assignment sets can contain unsupported nondominated plans. The UI text should use "sampled strategy frontier" and expose active original goals, the top two or three swept goals, attempted/successful weight points, and `complete_frontier=false`. Dominance filtering itself is exact only over sampled plans and the swept-goal metrics.

## 7. Intent-model review

The proposed SFT task is appropriately narrow. The output space is small, structured, and externally verifiable. Reverse generation from a sampled intent avoids subjective manual labeling, but it introduces two risks:

1. **Language leakage:** paraphrases of the same latent vector split across train/test inflate performance.
2. **Generator style bias:** a single big model may create repetitive phrasing unlike real users.

Mitigations:

- Split latent intent IDs before generating paraphrases.
- Hash normalized descriptions and reject duplicates across splits.
- Vary explicit style controls and keep adversarial examples test-only.
- Manually inspect stratified samples without changing their labels.
- Record generator model and prompt version.

The model should output absolute ISO dates because the system prompt supplies a reference date. Post-processing should not invent dates from raw user text after model failure.

The equal-weight fallback is operationally robust but semantically uncertain. Because this project uses synthetic advice only, visible fallback plus manual controls is acceptable. A production financial product should fail closed or require confirmation rather than silently optimize with guessed preferences.

## 8. Evaluation review

Weight MAE is useful but not sufficient: several weight vectors can lead to the same best card, and a small error near a decision boundary can change the result. Downstream match directly measures decision stability under parser error.

The headline metric should use multiple fixed single-purchase probes, not one scenario/card recommendation. A single probe can make one dominant card insensitive to most goals and inflate match rate. The probe suite must include cases where each major objective can change the winner.

Monthly agreement is valuable but should be reported separately because exact allocation matching is a stricter, higher-dimensional target. Recommended secondary metric:

$$
agreement = \frac{\# purchases assigned to the same card}{\# purchases}
$$

Evaluation must cache raw outputs, name the actual model, disable fallback, and report failures rather than replacing them with default intents.

## 9. Explanation review

Faithfulness requires the engine to provide the comparison facts. The explanation module should not call scoring functions again because configuration or state can drift. For a single purchase, the runner-up is the second feasible ranked candidate. For a monthly assignment, "why not Card B" must compare the chosen plan against a feasible one-purchase relocation from the final state, not a stateless card score.

If no feasible alternative exists, that fact is more useful than a fabricated score gap. Constraint slack comes from the analyzer. Slack of zero is binding; small positive slack is near-binding.

## 10. Highest remaining risks

| Risk | Consequence | Control |
|---|---|---|
| Utility calibration feels arbitrary | Weight sliders produce unintuitive plans | Central config, Sarah snapshots, raw-metric frontier, disclose assumption |
| Greedy dead end | Demo has no plan before ILP | Locked-first ordering, bounded repair, curated feasible default, honest status |
| CBC unavailable on event machine | Exact path fails | Startup health check and greedy fallback |
| Frontier latency | Demo stalls | 5 two-goal points or 15 three-goal points, timeout, cache identical scenarios |
| LLM emits subtly invalid numbers | Engine receives unsafe values | Strict finite validation and ppm conversion |
| Generated test leakage | Inflated Freesolo result | Split by latent intent before paraphrasing |
| UI duplicates calculations | Explanation mismatch | HTTP-only UI and structured engine fields |
| Demo depends on external endpoint | Live failure | Manual controls and clearly labeled fixture/default path |

## 11. Revisit triggers

Do not casually reopen decisions during the hackathon. Revisit only when one of these occurs:

- A hand-calculated test disproves a formula.
- The Sarah scenario cannot express the intended mortgage-versus-reward tradeoff under any reasonable weights.
- CBC cannot represent the shared aggregate objective without a mismatch to greedy scoring.
- Freesolo's actual schema or inference contract conflicts with the provider abstraction.
- Measured latency breaks the two-minute demo.

When revisiting, update this file, the canonical plan, integration contracts, and affected tests together.
