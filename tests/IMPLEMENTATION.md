# Test Strategy and Implementation Guide

## 1. Mission and boundary

Tests are the integration mechanism for parallel work. They verify shared contracts, pure financial arithmetic, solver honesty, parser attribution, explanation faithfulness, API serialization, and the complete synthetic demo. A contributor should be able to run the tests for one module without credentials or a live server.

The suite must distinguish:

- **Contract tests:** observable shapes and semantics shared across modules.
- **Unit tests:** one pure function/class boundary.
- **Oracle tests:** optimized results checked against enumeration or hand calculation.
- **Integration tests:** adjacent real modules combined.
- **End-to-end smoke tests:** API/UI workflow with fixture providers.
- **External tests:** real model endpoints, opt-in and excluded from default pytest.

Do not make default tests network-dependent, order-dependent, time-dependent, or reliant on wall-clock dates.

## 2. Directory layout

```text
tests/
  IMPLEMENTATION.md
  __init__.py
  conftest.py
  fixtures/
    README.md
    cards.py
    scenarios.py
    engine_results.py
    intent_outputs.py
  unit/
    engine/
    data/
    intent/
    explain/
    eval/
    ui/
  contract/
    test_domain_schema.py
    test_api_schema.py
    test_dependency_direction.py
  integration/
    test_engine_explain.py
    test_api_engine.py
    test_parser_api.py
    test_eval_downstream.py
  oracle/
    test_ilp_bruteforce.py
    test_hand_calculations.py
  e2e/
    test_sarah_api_flow.py
    test_streamlit_smoke.py
  external/
    test_freesolo_smoke.py
    test_general_model_smoke.py
```

Create test files only as implementation reaches their gate. Do not add empty tests that pass without assertions.

## 3. Pytest markers

Register these markers in `pyproject.toml` when their first tests are added:

- `slow`: local exact/frontier tests that may take more than one second.
- `external`: requires network and credentials; never runs by default.
- `ui`: Streamlit AppTest or browser-level checks.
- `oracle`: brute-force/hand-calculation comparison.

Default CI/local command excludes external tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not external" -q
```

External smoke tests require an explicit environment opt-in in addition to credentials, for example `RUN_EXTERNAL_TESTS=true`, and skip otherwise. A missing credential must not fail the ordinary suite.

## 4. Fixture principles

### Small over realistic

Unit/oracle fixtures use 2-3 cards and 1-6 purchases so expected behavior is hand-checkable. The full Sarah scenario belongs in integration/e2e tests, not every unit test.

### Builders, not mutable globals

Provide pytest fixtures/functions returning fresh Pydantic objects. Prefer explicit builders with defaults:

```python
def make_card(**overrides) -> Card: ...
def make_purchase(**overrides) -> Purchase: ...
def make_intent(**weights_or_constraints) -> Intent: ...
```

Do not create a generalized factory framework. A few readable helpers are enough.

### Fixed dates

Use explicit dates such as `date(2026, 8, 1)`. Do not call `date.today()` in deterministic tests. Parser relative-date prompts receive a fixed `reference_date`.

### No shared mutation

Every test receives independent lists/models. When testing what-if or UI edits, assert the original objects remain deeply equal to pre-call copies.

### Expected values

Expected rewards/utilization/dates are literals or independently hand-derived expressions. Do not calculate expected values by calling the same helper under test.

## 5. Contract tests

Contract tests should fail early when parallel branches drift.

Also add a small repository-doc test or release script that extracts relative Markdown links and asserts every target exists. Documentation is part of the parallel contract, so stale module links fail G0/G8 rather than surprising a contributor.

### Domain schema snapshot

Generate canonical JSON schema from domain/result Pydantic models and compare with a reviewed snapshot under `tests/fixtures/schema/`. Normalize nondeterministic ordering if necessary, but do not delete meaningful fields. A deliberate schema change updates:

1. Models.
2. Integration contracts.
3. Snapshot.
4. API wrappers/examples.
5. Affected module guides.

### Enum and issue stability

Assert exact values for:

- Six goals.
- Reward types.
- Solver methods/statuses, including `unresolved`.
- Stable optimization issue codes.
- Explanation kinds/tones.

These values are serialized and cannot be casually renamed.

### Dependency direction

Write a lightweight AST-based test over project imports:

- `engine` imports no project package.
- `data` imports only engine among project packages.
- `intent` imports only engine domain models.
- `explain` imports engine models, not scoring/solver modules.
- `api` may import engine/data/intent/explain.
- `ui` must not import engine/data/intent/explain; it may import only UI modules plus third parties/stdlib.
- `eval` may import engine/data/intent, not API/UI.

Avoid adding a dependency-analysis package; Python `ast` is sufficient.

### API schema snapshot

After G4, snapshot `create_app(...).openapi()`. Strip only known nondeterministic metadata. Test all endpoint paths, operation IDs, request models, response models, status descriptions, and `schema_version` examples.

## 6. Financial correctness tests

### Integer invariant helper

Implement a recursive assertion for fields whose names end in `_cents`, `_millicents`, `_bps`, `_ppm`, `_points`, or `_days`:

- Value is `int` or permitted `None`.
- Value is not `bool`.
- No float appears anywhere in raw factor, objective, metric, slack, or delta results.

Intent input weights are the one documented exception before quantization.

Add direct Pydantic cases proving `1000.0`, `1000.5`, `True`, and string `"1000"` are rejected for integer money fields under strict configuration; do not merely inspect already-valid result objects.

### Reward table tests

Use parameterized cases:

- Exact cashback amount.
- Floor of fractional cent.
- Exact category match.
- Base fallback and base reward type.
- `other` as a literal category versus base fallback.
- Points floor before millicent conversion.
- Zero rate.
- Large cents without overflow concerns in Python.

### Date/cashflow tests

- Purchase before, on, and after statement day.
- Due day after close in same month.
- Due day before/equal close rolls to next month.
- December-to-January rollover.
- Leap-year February, though days are capped at 28.
- Carrying value floors and remains integer.

### Utilization tests

- Zero limit returns diagnostic without division.
- Display bps floor.
- Hard ceiling uses exact cross multiplication and rejects a case that displayed floor could incorrectly accept.
- Every piecewise band boundary and one interior point.
- Starting penalty subtraction.
- Imported over-limit state remains diagnosable.

### Signup tests

- No bonus.
- Already achieved.
- Purchase before/on/after deadline.
- Partial progress cap.
- Exact completion and over-completion.
- 20/80 progress/completion pool reconciliation.
- Existing prior bonus is not counted as newly projected reward.
- ILP `hit`, exact capped `progress`, and floor-linearized `progress_points` match the pure evaluator around `remaining - 1`, `remaining`, and `remaining + 1` cents.

## 7. Feasibility tests

Test stable issue codes and affected IDs, not full prose.

- Duplicate card/purchase IDs.
- Unknown lock/forced bonus card.
- Forced card without bonus.
- Locked purchase over capacity.
- One purchase fits no card.
- Aggregate credit capacity contradiction.
- Full-horizon utilization capacity contradiction.
- Dated cutoff includes purchases on cutoff and excludes later ones.
- Forced bonus has too little eligible spend.
- Forced bonus has enough total spend but not enough card capacity.
- Feasible assignment reports exact credit/utilization slack.

Include at least one scenario that passes all necessary analytical checks but is globally infeasible due to indivisible assignment; this confirms only ILP/oracle may prove it and prevents overclaiming by simple checks.

## 8. Optimization oracle

### Brute-force enumerator

Implement a test-only enumerator for tiny scenarios:

1. Enumerate Cartesian product of card IDs for unlocked purchases.
2. Apply locks.
3. Analyze full feasibility.
4. Evaluate each feasible plan through the shared pure evaluator.
5. Select highest utility with the same stable assignment tie-break.

Although it shares evaluator/feasibility code, it independently validates ILP assignment search and linearization. Pair it with hand-calculated tests for evaluator formulas.

### ILP matrix

Generate deterministic small cases covering:

- Additive rewards only.
- Credit-limit packing.
- Dated utilization.
- Piecewise utilization boundary.
- Risk headroom.
- Partial bonus progress.
- Soft bonus hit.
- Forced bonus hit.
- Locks.
- Equal-utility tie.
- Proven infeasible indivisible packing.

Assert exact assignment map, status, and total utility against brute force. Run enough cases to catch coefficient/sign errors without property-test dependencies; a small seeded loop over generated integer cases is acceptable.

### Greedy relationship

For every feasible oracle case:

- Complete greedy result is feasible.
- If greedy completes, ILP utility is greater than or equal.
- Greedy repeat is identical.
- Local search does not reduce initial greedy utility.
- A heuristic dead end without proof reports `unresolved`.

Do not assert greedy always completes arbitrary feasible instances.

## 9. Parser and data-generation tests

Fixture provider outputs cover valid JSON, fence extraction, missing/unknown fields, malformed JSON, nonfinite numbers, wrong cards, timeout, and explicit provider error.

Generation tests assert:

- Seeded latent ppm and constraints.
- Latent-level split before paraphrase generation.
- No normalized duplicate across splits.
- Assistant target validates through engine `Intent`.
- Cache/resume avoids repeated provider calls.
- Manifest hash changes when content changes.
- No fixture-generated corpus can be labeled production.

No network mock should be so high-level that HTTP request shape/auth/retry classification goes untested once real adapters exist.

## 10. Explanation faithfulness tests

Construct engine result fixtures with distinctive values so source mix-ups are visible.

- Every line's `raw_value` equals its `source_path`.
- Money/percentage/days formatting.
- Dominant contribution uses corresponding raw factor.
- Runner-up delta sign.
- Monthly alternative comes from final-state trace.
- Infeasible alternative shows no fake utility comparison.
- Binding zero and near-binding positive slack.
- Partial bonus wording.
- `optimal`, `heuristic`, `heuristic_fallback`, `infeasible`, and `unresolved` disclosures.
- Sampled-frontier incompleteness statement.

One integration test passes actual engine output through explanation and recursively verifies all source paths resolve.

## 11. API tests

Use app-factory injection and TestClient. No live Uvicorn process is needed.

- Health ready/degraded states.
- Demo scenario contract.
- All route happy paths.
- Domain failure under HTTP 200.
- Request bounds and reference errors under 422.
- Provider fallback and no-fallback infrastructure statuses.
- Repeated deterministic response equality.
- Secret sentinel absent from any error body/log capture.
- OpenAPI snapshot.

At least one API flow uses real data loader, engine, and explanation modules together.

## 12. UI tests

Keep most UI behavior in pure formatting/state helpers. Use Streamlit AppTest for high-value smoke behavior only.

- Dollar/cents conversion via Decimal.
- Goal normalization and constraint request mapping.
- API client status handling.
- App initializes Sarah scenario.
- Manual/fallback source is visible.
- Domain failures do not render a success dashboard.
- Solver availability controls exact option.
- Submit action makes one request.

Visual overlap/responsive behavior requires manual browser verification because AppTest is not a layout engine. Record the viewport checklist in the release gate rather than claiming automated pixel coverage.

## 13. Evaluation harness tests

Hand-calculate a fixture matrix with perfect, shifted, boundary-crossing, constraint-missing, malformed, and timeout runners. Assert:

- Invalid outputs stay in denominators.
- Valid count accompanies MAE.
- Constraint atom counts.
- Probe card matches.
- Cluster bootstrap seed reproducibility.
- Monthly agreement/unavailable counts.
- Runner/model/prompt/dataset attribution checks.
- Fixture report cannot be emitted as final report.

These tests must pass before spending credits on external evaluation.

## 14. End-to-end Sarah smoke

At G4, a TestClient-based flow:

1. Load demo scenario.
2. Select mortgage preset/manual intent.
3. Allocate greedily.
4. Assert complete feasible status and all applicable utilization ceilings.
5. Assert explanation cites rent and at least one alternative.
6. Select travel preset.
7. Reallocate and assert at least three assignments differ.

At G5, repeat with ILP and sampled strategies; run rent what-if. Avoid asserting arbitrary exact card choices unless they are part of the reviewed demo contract. Assert meaningful behavioral invariants.

## 15. Test commands by gate

```powershell
# G1
.\.venv\Scripts\python.exe -m pytest tests/unit/engine/test_models.py tests/unit/data -q

# G2
.\.venv\Scripts\python.exe -m pytest tests/unit/engine/test_scoring.py tests/unit/engine/test_objective.py -q

# G3
.\.venv\Scripts\python.exe -m pytest tests/unit/engine tests/unit/explain tests/integration/test_engine_explain.py -q

# G4
.\.venv\Scripts\python.exe -m pytest tests/contract tests/integration tests/e2e/test_sarah_api_flow.py -q

# G5
.\.venv\Scripts\python.exe -m pytest -m oracle -q

# G6
.\.venv\Scripts\python.exe -m pytest tests/unit/intent tests/unit/eval tests/integration/test_eval_downstream.py -q

# Release
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest -m "not external" -q
```

Commands become runnable as named files are implemented. Until then, use narrower paths that exist rather than adding placeholder passes.

## 16. Failure triage

When a shared test fails on a parallel branch:

1. Determine whether canonical contract or local implementation changed.
2. Do not update snapshots just to make tests green.
3. If contract change is intended, update the canonical files/checklist in one coordinated change.
4. If implementation is wrong, keep contract tests unchanged.
5. Record an externally blocked test as skipped with a precise reason; never `xfail` an ordinary known bug indefinitely.

## 17. Completion checklist

- Default suite is offline and deterministic.
- Shared schema/dependency tests catch branch drift early.
- Financial expected values are independently hand-checkable.
- ILP search is checked against brute force.
- Invalid model outputs remain visible in metrics.
- Explanation source paths reconcile.
- Sarah behavior is tested at API level.
- External tests require explicit opt-in and never hide fallback.
