# Evaluation Harness Implementation Guide

## 1. Mission and boundary

The eval module measures whether a model reliably converts language into the `Intent` contract and, most importantly, whether parser error changes deterministic payment decisions.

It compares exactly three named systems on one frozen held-out set:

1. Fine-tuned small model (submission).
2. Same base small model without fine-tuning, prompted.
3. Strong general model, prompted, as a ceiling reference.

The harness never silently falls back, edits model outputs to match gold decisions, or excludes invalid outputs from end-to-end success rates.

## 2. Files

```text
eval/
  IMPLEMENTATION.md
  __init__.py
  models.py          # eval records, runner output, metric/report models
  runners.py         # protocol and provider adapters
  dataset.py         # frozen held-out loading and hashing
  scenarios.py       # probe suite and monthly verification scenario
  metrics.py         # pure metric calculations and bootstrap intervals
  harness.py         # orchestration and caching
  report.py          # JSON and Markdown rendering
  cache/             # gitignored raw named-model responses
  reports/
    README.md
```

The module may import engine public APIs, data loaders, and intent parser validation. Production API/UI never import eval.

## 3. Frozen evaluation unit

One eval example contains:

- `example_id`
- User description
- Gold `Intent`
- Reference date
- Allowed synthetic card context
- Split/source (`generated_test` or `adversarial_test`)
- Latent intent ID when generated

Freeze and hash the complete ordered set before any model run. Stable-sort by `example_id`. The same prompt version, card context, reference date, and parser rules apply to all models.

Do not tune prompts on the held-out examples after seeing trained-model results. If a prompt contract bug requires a change, increment the prompt version and rerun all three systems.

## 4. Runner protocol and attribution

```python
class ModelRunner(Protocol):
    runner_id: str
    provider_name: str
    model_id: str
    model_role: ModelRole  # trained_slm, base_slm, big_prompted
    prompt_version: str

    async def run(self, example: EvalExample) -> RunnerOutput: ...
```

`RunnerOutput` contains raw text, success/error category, provider latency metadata, and cache state. It does not contain a fallback intent. The harness passes raw text through the same strict parser post-processing used at runtime with fallback disabled.

The report fails validation if:

- Two rows use the same `runner_id` for different model identities.
- A required model role is absent.
- Any runner records fallback use.
- Dataset or prompt hashes differ across roles.
- A fixture provider is presented as a trained/base/big result.

## 5. Cache design

External calls are expensive and rate-limited, so cache raw responses by:

```text
sha256(dataset_hash + example_id + runner_id + model_id + prompt_version + request_schema_hash)
```

Each cache record includes safe identity metadata, raw output, and error class. It excludes API keys and authorization headers. Writes are atomic. A changed prompt/model/schema produces a new key.

`--refresh` may bypass cache for one named runner. Never partially overwrite a frozen report; write a new run directory and update a `latest` pointer/file only after report validation succeeds.

## 6. Metric denominators

Invalid outputs are central evidence, not missing data.

- **Valid JSON/schema rate:** denominator is every held-out example.
- **Weight MAE:** report over valid predictions only, with valid count beside it. Do not compare MAE without its coverage.
- **Constraint accuracy:** invalid output counts as all relevant fields incorrect.
- **Downstream match:** invalid output counts as a mismatch for every probe.
- **Monthly agreement:** invalid output counts as zero agreement.

Also report generated and adversarial subsets separately to reveal brittleness.

## 7. Valid-output metric

Track two nested rates:

1. JSON object parse rate.
2. Full `Intent` schema-valid rate after known-key, finite-number, date, and card-reference validation.

The headline "valid JSON" table may show schema-valid rate because that is what the engine can consume, but raw JSON parse rate should remain in the detailed report.

Warnings such as normalized weights do not make output invalid. Unknown hard-constraint fields do, because dropping them could hide user requirements.

## 8. Weight error

Use macro mean absolute error over six normalized goals for schema-valid outputs:

$$
MAE = \frac{1}{6N_{valid}} \sum_{i=1}^{N_{valid}} \sum_{g=1}^{6}
|\hat{w}_{i,g} - w_{i,g}|
$$

Compute with `Decimal` or integer ppm to make results reproducible. Recommended implementation converts gold and predicted weights to ppm and divides the final integer absolute-error sum for display.

Optionally report per-goal MAE to show systematic confusion. KL divergence is not required in the first pass because zeros require smoothing and are less intuitive for judges. Add it only with an explicit epsilon and exact definition.

## 9. Constraint extraction metrics

Report exact match for each field:

- `max_utilization_bps`
- `max_utilization_until`
- `must_hit_bonus_card_ids` as a set

Report whole-constraint exact match as all three fields matching.

For precision/recall/F1, convert gold and prediction to atomic facts:

```text
max_utilization_bps=3000
max_utilization_until=2026-10-18
must_hit_bonus_card_id=aurora-bonus
```

Compute micro counts across all examples. An incorrect numeric/date value is one false positive plus one false negative. Invalid output contributes all gold atoms as false negatives.

This is stricter and clearer than giving partial numeric credit to an incorrect hard limit.

## 10. Headline downstream match

### Why multiple probes

One fixed recommendation can be insensitive to most weight differences. Use a fixed suite of at least five hand-verified synthetic probes:

- Rent reward versus bonus/utilization.
- Grocery category rate versus credit health.
- Travel point value versus cashback.
- Dining reward versus float.
- Large purchase capacity/risk versus reward.

For each eval example and each probe:

1. Run exact single-purchase enumeration with gold intent.
2. Run the same enumeration with predicted intent.
3. Compare winner card IDs.
4. Count invalid prediction as mismatch.

$$
DownstreamMatch =
\frac{\sum_{i,p} 1[goldWinner_{i,p}=predWinner_{i,p}]}{N_{examples} \times N_{probes}}
$$

Probe construction requirements:

- Gold recommendation is feasible for every valid gold intent.
- Stable tie-break is enabled.
- Across a controlled matrix of one-hot and balanced intents, every probe has at least two possible winners and the suite exercises all goals except any that are demonstrably redundant.
- Probe JSON and engine config hashes are frozen in the report.

This measures decision equivalence, not whether the model itself recommended a card. The model never emits a card choice.

## 11. Confidence interval

Use a deterministic bootstrap over example IDs, keeping all probes for a sampled example together. This cluster bootstrap avoids treating correlated probe outcomes as independent.

- Seed: fixed and recorded, for example `42`.
- Resamples: at least `1,000` for the final report; `200` is acceptable for local iteration.
- Interval: percentile 2.5th and 97.5th percentiles.

Report absolute rate and interval, not a significance claim unless a proper paired comparison is added.

## 12. Secondary monthly agreement

Use one small frozen scenario with enough tradeoff to change assignments and few enough purchases for fast exact ILP. For each valid prediction:

1. Solve with gold intent.
2. Solve with predicted intent.
3. Compare card ID per purchase.

$$
Agreement_i =
\frac{\# matching\ purchase\ assignments}{\# scenario\ purchases}
$$

Report mean agreement over all examples, with invalid outputs assigned zero. Also report exact-plan match rate.

Use the same exact solver configuration and timeout for gold/predicted. If either solve is not `optimal`, mark that example's monthly metric unavailable and report the count; do not compare a heuristic plan against an exact plan as if equivalent. Keep this scenario small enough that unavailability should be zero.

The single-purchase downstream metric remains the headline because it is exact, fast, and easy to explain.

## 13. Harness flow

```text
load and hash dataset
  -> load and hash probes/config/prompt
  -> validate three named runners
  -> run/cache raw outputs
  -> parse with fallback disabled
  -> compute parser metrics
  -> run gold recommendations once per unique gold intent/probe
  -> run predicted recommendations
  -> compute downstream and monthly metrics
  -> bootstrap intervals
  -> validate report provenance
  -> write JSON report
  -> render Markdown table/narrative
```

Optimize by caching gold engine outputs keyed by canonical intent ppm and scenario hash. Engine calls are local and deterministic, so optimization should not complicate attribution.

## 14. Report schema

Top-level provenance starts with `report_schema_version: "1.0"` and includes:

- Report schema version.
- Evaluation date as metadata.
- Dataset path/hash and counts.
- Prompt version/hash.
- Engine version/commit when Git exists.
- Canonical engine-config hash, covering utilization bands, signup 20/80 calibration, carry rate, risk thresholds, solver limits, and frontier settings.
- Probe/monthly scenario hashes.
- Bootstrap seed/resamples.

Per runner:

- Role, provider, model ID, runner ID.
- Total, generated, adversarial counts.
- JSON parse and schema-valid rates.
- Weight MAE overall/per-goal with valid count.
- Constraint exact/micro metrics.
- Downstream match and 95% interval.
- Monthly mean/exact agreement and unavailable count.
- Error-category counts.
- Fallback count, required to be zero.

Never write the desired conclusion first and fit values into it. The narrative is conditional:

- If trained SLM beats base and approaches big: emphasize efficient specialization.
- If trained beats base but trails big materially: emphasize improvement and identify remaining errors.
- If trained does not beat base: report it honestly, inspect data/prompt/training, and do not claim success.

## 15. Local development without credentials

Implement `FixtureModelRunner` instances that return predetermined valid, malformed, and semantically wrong outputs. Use them only to test harness mechanics.

Required local fixture cases:

- Perfect prediction.
- Valid but shifted weights with same downstream decisions.
- Small weight error that crosses one probe boundary.
- Correct weights but missed hard constraint.
- Malformed JSON.
- Unknown field.
- Provider timeout.

Expected metrics are hand-calculated in tests. Fixture reports must contain `synthetic_fixture=true` and cannot be rendered into the submission report path.

## 16. Tests

### Dataset/provenance

- Stable order/hash independent of working directory.
- Duplicate example/latent IDs rejected.
- Train/test latent leakage rejected when train manifest is supplied.
- Prompt/probe/config mismatch stops comparison.

### Metrics

- Exact hand-calculated valid rates and ppm MAE.
- Invalid denominator behavior.
- Constraint atom precision/recall edge cases including empty sets.
- Downstream matching with deterministic engine fixtures.
- Cluster bootstrap deterministic for seed.
- Monthly agreement and unavailable handling.

### Runners/cache

- Cache keys change on model/prompt/schema change.
- Resume avoids calls.
- Secret fields never serialize.
- Runner-role identity validation.
- Fallback count must remain zero.

### Reports

- JSON validates against report model.
- Markdown values equal JSON source.
- Fixture report cannot overwrite final report.
- Missing required model role prevents final status.

## 17. Completion checklist

### Before external access

- Frozen dataset/probe loaders and hashes work.
- Fixture runners exercise every failure path.
- Metric tests are hand-verifiable.
- Downstream probes react to different intents.
- JSON and Markdown report generation agree.

### Final run

- Freesolo trained and base endpoint identities are confirmed.
- Big-model identity is confirmed.
- All three use the same frozen set and prompt contract.
- Fallback is disabled and count is zero.
- Raw outputs are cached and attributable.
- Metrics and intervals are generated, not hand-entered.
- Claims in README/Devpost match measured results.
