# Failure Modes and Reliability Contract

This document defines expected behavior when inputs, constraints, solvers, model providers, fixtures, or UI integration fail. It is part of the product design, not post-hoc demo messaging.

## 1. Reliability principles

1. Never crash the money path because an LLM failed.
2. Never claim infeasibility or optimality without an appropriate proof.
3. Never silently relax a hard constraint.
4. Never convert an unknown value into zero and present it as measured.
5. Never hide fallback/provider identity in the UI or evaluation.
6. Preserve the last valid synthetic input and explain what action failed.
7. Use stable machine-readable issue codes; prose may improve without breaking consumers.
8. External services are optional for the Chexy demo and mandatory only for named Freesolo measurements.

## 2. Arithmetic and input failures

### Float or invalid money enters a domain model

**Detection:** domain models forbid extra fields and use strict integer annotations for money/rates/days, rejecting floats, booleans, numeric strings, negatives, and invalid ranges while still accepting ISO date strings from JSON.

**Behavior:** API returns HTTP 422 with field location. Direct engine callers receive validation error before optimization.

**Recovery:** user corrects input. No rounding/coercion is performed silently.

### Zero-limit card

**Detection:** limit is exactly zero.

**Behavior:** card remains visible for imported-state diagnostics but is excluded from new assignments with `zero_credit_limit`. Utilization reporting uses a defined sentinel/diagnostic path and never divides by zero.

**Recovery:** use another card or correct synthetic limit.

### Current balance already exceeds limit

**Detection:** `current_balance_cents > credit_limit_cents`.

**Behavior:** no new purchase may use that card; issue `card_already_over_limit`. Existing state remains visible. Other cards may still form a valid plan.

**Recovery:** correct input or remove the card from the planning portfolio. The engine does not assume an unmodeled payment.

### Point value uncertain

**Detection:** every points/miles card uses a static fixture value.

**Behavior:** output marks travel value as a static assumption. It does not model redemption inventory, transfer partners, or dynamic award prices.

**Recovery:** edit the synthetic point value and rerun. This is sensitivity analysis, not a provider lookup.

## 3. Constraint failures

### Unknown locked card or forced bonus card

**Detection:** cross-reference validation.

**Behavior:** structured `unknown_locked_card` or `unknown_bonus_card`; no solve begins. Domain response may be `infeasible` because the required assignment references no valid option.

**Recovery:** unlock/select a known card.

### Forced card has no bonus

**Detection:** forced card exists but `signup_bonus` is null.

**Behavior:** `card_has_no_bonus`; no silent conversion to a soft preference.

**Recovery:** remove the hard bonus requirement or choose an active bonus card.

### Purchase exceeds every card's capacity

**Detection:** exact individual capacity checks before solve.

**Behavior:** analytically proven `infeasible`, affected purchase/card capacities, suggestion to reduce amount/unlock/relax ceiling/add capacity. No split purchase is attempted.

### Utilization ceiling conflicts with required purchases

**Detection:** cross-multiplied per-card ceiling and aggregate necessary-capacity checks, then ILP if needed.

**Behavior:** `utilization_ceiling_exceeded` or `no_feasible_assignment`, with dated/full-horizon scope and required/available cents. The ceiling is never softened automatically.

**Recovery:** user explicitly changes/removes the ceiling or cutoff.

### Forced signup bonus unreachable

**Detection:** deadline-eligible spend and card capacity are less than remaining required spend, or ILP proves conflict with other hard constraints.

**Behavior:** `bonus_deadline_passed` or `bonus_target_unreachable`; no partial plan is presented as satisfying the requirement.

**Recovery:** remove must-hit status, add eligible synthetic purchases, change balances/limits, or choose another bonus.

## 4. Search and solver failures

### Greedy search dead-end

**Detection:** no feasible candidate and bounded relocation repair cannot complete a plan.

**Behavior:**

- If an analytical contradiction is found: `infeasible`.
- Otherwise: `unresolved` with `heuristic_dead_end`.

The UI says the heuristic did not find a plan, not that no plan exists.

**Recovery:** run exact ILP, change solver choice, or relax an explicitly named hard constraint.

### CBC unavailable

**Detection:** startup health probe or solver invocation.

**Behavior:** health marks exact solver degraded. ILP request runs the verified greedy path and returns `heuristic_fallback` with `solver_error`, when greedy succeeds. The UI disables or annotates exact selection.

**Recovery:** install/fix CBC/PuLP environment or continue with honestly labeled heuristic.

### Exact solver timeout/error

**Detection:** CBC status/exception plus a caller-side process watchdog. CBC's internal timer is not trusted as the only boundary because the bundled Windows build can remain blocked in `cbc.wait()`.

**Behavior:** normal exact solves run in an isolated worker. The wall watchdog is configurable from 1 to a hard maximum of 60 seconds. On timeout the parent terminates the complete Python/CBC process tree, discards any unverified incumbent, and runs the independent greedy allocator. Successful result is `heuristic_fallback`; failure preserves `unresolved` or proven `infeasible`. Include `solver_timeout`/`solver_error` warning.

**Recovery:** reduce the scenario/frontier grid, choose a lower demo timeout, or use greedy. The maximum cannot be raised above 60 seconds without a reviewed code change.

### CBC proves infeasible

**Detection:** `LpStatusInfeasible` under validated model.

**Behavior:** `infeasible`, empty assignment, diagnostic analyzer issues/suggestions. If analyzer cannot isolate one minimal conflict, state that the combined hard constraints conflict rather than inventing a single cause.

### Solver returns result inconsistent with analyzer

**Detection:** every result is independently rechecked through pure feasibility and objective evaluation.

**Behavior:** treat as `solver_error`; do not expose the invalid assignment. Attempt verified greedy fallback. Log local details for debugging.

## 5. Parser/provider failures

### Provider unavailable, unauthorized, or timed out

**Runtime demo with fallback enabled:** return equal weights/no constraints, `used_fallback=true`, provider warning, and manual editing controls. Money engine remains available.

**Runtime with fallback disabled:** return typed upstream error; do not call engine with guessed intent.

**Evaluation:** count as invalid output/mismatch. Never substitute a default or another provider.

### Malformed/ambiguous JSON

**Detection:** strict one-object extraction and schema validation.

**Behavior:** same fallback/no-fallback policy as provider failure. Multiple JSON objects are ambiguous and rejected.

### Unknown goal or constraint field

**Detection:** strict known-key validation.

**Behavior:** reject rather than silently discard, because an unknown hard constraint may be safety-relevant.

### Nonfinite, negative, or all-zero weights

**Detection:** strict finite/nonnegative and positive-sum checks.

**Behavior:** reject model output. Non-unit positive values may be normalized with warning.

### Hallucinated bonus card

**Detection:** card-context validation.

**Behavior:** reject intent; do not drop the forced card silently.

### Equal-weight fallback misunderstood as safe advice

**Risk:** fallback is operationally robust but not a personalized financial-safety policy.

**Control:** always show fallback source/warning and editable weights. All data is synthetic. Production evolution should require user confirmation or fail closed.

## 6. Synthetic data failures

### Corrupt/mismatched committed fixture

**Detection:** loader validates schema, IDs, references, and synthetic marker.

**Behavior:** health reports fixture degraded; `/demo-scenario` fails with a stable data-load error. Do not serve partially loaded scenario.

**Recovery:** fix fixture and rerun loader tests.

### Generated corpus interrupted/rate-limited

**Detection:** provider error/retry counts and missing accepted examples.

**Behavior:** atomic cache/manifest writes permit resume. Failed rows are not counted as examples. Final manifest remains incomplete until target acceptance criteria are met.

### Train/test leakage

**Detection:** latent-ID split validation and global normalized-description hashes.

**Behavior:** generation/eval gate fails. Do not train/report until regenerated.

## 7. Explanation failures

### Missing source field or ID mismatch

**Detection:** explanation contract lookup.

**Behavior:** typed internal contract error; API returns generic 500 and logs source path. Do not emit a partially fabricated decision card.

### Alternative trace absent

**Detection:** successful assignment has other cards but no alternatives.

**Behavior:** explanation may state comparison unavailable only for a known older schema during migration; v1 contract tests otherwise fail. Do not recompute statelessly.

### Partial progress described as reward

**Detection:** wording/template tests.

**Behavior:** release gate fails. Correct template to distinguish qualifying spend from earned bonus.

## 8. API/UI failures

### API unavailable

**Behavior:** Streamlit shows one service error and exact local start command. Forms that require API are disabled. It does not fall back to local duplicate logic.

### API schema version mismatch

**Behavior:** UI stops rendering affected response and asks for matching services; no best-effort field guessing.

### Domain infeasible/unresolved

**Behavior:** HTTP 200 with typed result. UI renders issues/suggestions instead of empty metrics/charts.

### Stale result after input edit

**Behavior:** clear results on submitted changed inputs or visibly mark stale. Never imply an old plan corresponds to new controls.

### Frontier partially succeeds

**Behavior:** show successful sampled plans, attempted/successful count, warnings, and incomplete disclosure. If no solve succeeds, show domain failure only.

## 8b. CardIQ payment-lifecycle failures (implemented in `api/`)

The shipped CardIQ layer adds a simulated payment lifecycle on top of the engine.
Its failure contract:

### Duplicate payment request

**Detection:** every `POST /api/payments/{id}/pay` carries a client-generated
idempotency key; the `transactions.idempotency_key` column is UNIQUE.

**Behavior:** a repeated key returns the original transaction with `duplicate: true`
and logs `duplicate_blocked`. A second synthetic charge is never created.

### Card declined / insufficient credit / locked / expired

**Detection:** simulator scenario, with defensive pre-checks — a locked, expired, or
over-limit card overrides an optimistic scenario at pay time.

**Behavior:** the transaction moves to `failed` through validated state transitions,
the card's `recent_failures` increments (penalizing it in future rankings), and the
card-selection algorithm reruns excluding the failed card to recommend the backup.
No retry or switch happens without user approval.

### Network timeout / unknown authorization status

**Detection:** simulator scenario ends in `status_uncertain`.

**Behavior:** the transaction parks; the UI states "Payment status is uncertain.
CardIQ will verify the original transaction before attempting another charge."
There is no automatic retry. Verification either resumes the original charge
(confirmed — no second charge) or marks it failed (not found — safe to retry with a
new idempotency key).

### Illegal state transition

**Detection:** every transition is validated against `ALLOWED_TRANSITIONS` in
`api/state_machine.py`; simulator scripts are tested to be legal walks.

**Behavior:** an illegal transition raises before any state is written.

## 9. Privacy/security posture

- Persona, account, purchase, bonus-progress, and demo text fixtures are synthetic. Product names and ordinary reward terms are public reference facts sourced from official issuer pages and timestamped.
- No banking login, card number, government ID, address, or real transaction feed is accepted.
- Secrets live in environment variables and `.env` is ignored.
- Provider caches omit headers/secrets.
- Default logs omit raw text and full financial objects.
- API accepts no arbitrary provider URL or file path.
- External calls are only intent parsing/offline generation; cards and purchase math do not need to leave the local process.

This is a prototype, not production financial advice or a payment processor. Threat modeling for real PII, authentication, authorization, encryption-at-rest, audit retention, and regulatory controls would be mandatory before production use.

## 10. Demo recovery matrix

| Demo failure | Immediate recovery |
|---|---|
| Freesolo unavailable | Show warning; use manual preset/sliders |
| Prompted fallback unavailable | Use manual preset/sliders |
| CBC unavailable/timeout | Show heuristic fallback status |
| Frontier too slow/partial | Show available sampled plans or skip to what-if |
| Hard constraints infeasible | Use failure block as reliability demo, then relax explicitly |
| Streamlit loses API | Restart API using displayed command; engine tests remain independent |

Rehearse at least one degraded parser run and one exact-solver fallback before judging. Failure behavior should look intentional because it is.

## 11. Release checklist

- Zero-limit, over-limit, no-capacity, forced-bonus, and conflicting-constraint tests pass.
- Greedy dead-end versus proven infeasibility wording is correct.
- CBC timeout/unavailable path returns verified fallback or honest unresolved result.
- Parser fallback source is visible; eval fallback count is zero.
- No generated corpus leakage.
- Explanation source-path tests pass.
- API error bodies/log captures contain no secret sentinel.
- Sarah demo works with network disabled through manual intent controls.
