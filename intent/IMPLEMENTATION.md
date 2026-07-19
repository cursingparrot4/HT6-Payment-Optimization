# Intent Parser and SFT Data Implementation Guide

## Implementation status

Implemented and validated locally without credentials:

- Strict provider/parse/result models with safe metadata allowlists and attribution invariants.
- Versioned JSON-only system prompt with reference date and minimal card context.
- Safe `Card` to `IntentCardContext` derivation containing only ID, name, and active-bonus state.
- Async provider protocols plus deterministic fixture intent/paraphrase providers.
- Strict one-object/fenced-JSON extraction, non-standard-number rejection, known-key validation, missing-goal repair, normalization warnings, absolute date validation, and active-bonus card validation.
- Visible equal-weight/no-constraint fallback; no-fallback failures remain measurable; async cancellation propagates.
- Seeded balanced/sparse/two-goal/constraint-heavy latent intent generation with exact ppm vectors.
- Latent-level deterministic 85/15 split before paraphrasing and global normalized cross-split dedupe.
- Internal system/user/assistant JSONL records, atomic writes, safe provider cache, corrupted-cache regeneration, SHA-256 train/test/combined hashes, and reproducibility manifests.
- Fixture datasets explicitly prohibited from production claims; non-fixture production claims require 800-2,000 accepted records.
- AST enforcement that intent code imports no scoring, solver, optimizer, explanation, or money-calculation module.

Current focused gate: `uv run python -m pytest tests/unit/intent -q` and `uv run python -m ruff check intent tests/unit/intent`.

Externally blocked and deliberately not guessed:

- Freesolo authentication, upload schema, supported base model, training request, and inference response contract.
- General-model provider/model selection for real offline paraphrase generation and prompted comparison.
- Real 800-2,000-row generation, SFT run, endpoint integration, and measured three-model eval.

## 1. Mission and boundary

The intent module turns a user's natural-language goal into the engine's validated `Intent` contract. It also creates supervised fine-tuning examples by sampling structured intents first and asking a general model to paraphrase them.

The module owns uncertainty in language. It does not calculate rewards, choose cards, run the optimizer, create financial explanations, or repair an intent based on the optimizer's preferred answer.

The module may import `Goal`, `Constraint`, and `Intent` from `engine.models`. It must not import recommendation, allocation, scoring, or explanation code.

External state at scaffold time:

- Freesolo account/docs: unavailable.
- Freesolo model/endpoint: unavailable.
- General-model API key: unavailable.

Implement provider-independent code and fixture-backed tests first. Leave concrete endpoint payload details gated behind documentation confirmation.

That provider-independent checkpoint is now complete. Concrete HTTP adapters now exist in
`providers.py`: `GeminiIntentProvider` (Gemini REST, structured JSON) and `FreesoloIntentProvider`
(the trained SLM over an OpenAI-compatible chat-completions surface). Both conform to the
`IntentProvider` protocol on this module's models — typed `IntentProviderUnavailableError` /
`IntentProviderTimeoutError` / `IntentProviderError`, `ProviderResponse` with the metadata
allowlist, and the shared `build_intent_system_prompt`. They are opt-in: each self-reports
unavailable until its API env vars are set, and the API selects one via
`CARDIQ_INTENT_PROVIDER=freesolo|gemini` (default `fixture`). Live measured training and the
three-model eval still require real Freesolo credentials/endpoint (see `training/`).

## 2. File ownership

```text
intent/
  IMPLEMENTATION.md
  __init__.py
  models.py             # provider metadata, parse result, latent examples
  prompts.py            # versioned system prompts and schema rendering
  providers.py          # protocols, fixture providers, Gemini + Freesolo HTTP adapters
  parser.py             # JSON extraction, validation, fallback
  sampling.py           # seeded latent intent and constraint sampling
  gen_data.py           # paraphrase orchestration, split, dedupe, JSONL
  manifests.py          # hashes and reproducibility metadata
  training/
    README.md            # confirmed Freesolo steps once access exists
    run_manifest.json    # created only for a real run
```

Keep HTTP transport details inside providers. Parser and dataset code operate against protocols and are fully testable without network access.

## 3. Shared engine contract

The only model output accepted downstream is:

```json
{
  "weights": {
    "max_cashback": 0.1,
    "max_travel": 0.1,
    "credit_health": 0.45,
    "hit_signup_bonus": 0.25,
    "max_cashflow": 0.05,
    "min_risk": 0.05
  },
  "constraints": {
    "max_utilization_bps": 3000,
    "max_utilization_until": "2026-10-18",
    "must_hit_bonus_card_ids": ["aurora-bonus"]
  }
}
```

Training targets contain every weight key even though parser post-processing can fill omitted keys with zero. The trained model should learn the canonical form rather than rely on repair.

The parser does not emit ppm. `engine.objective` quantizes the validated intent. This separation lets the same JSON contract remain legible for SFT and UI sliders.

## 4. Intent result and provider models

Define these intent-owned types:

### ProviderResponse

- `raw_text: str`
- `provider_name: str`
- `model_id: str`
- `latency_ms: int | None`
- `response_format: str` such as `text` or `json_schema`
- `cached: bool`
- `metadata: dict[str, str | int | bool]` with an allowlist

Never include prompts containing secrets, authorization headers, API keys, or complete raw vendor objects.

### ParseWarning

Stable codes:

- `json_extracted_from_fence`
- `missing_goal_filled_zero`
- `weights_normalized`
- `unknown_goal_rejected`
- `unknown_constraint_rejected`
- `provider_unavailable`
- `provider_timeout`
- `invalid_json`
- `schema_validation_failed`
- `fallback_equal_weights`

### ParseIntentResult

- `intent: Intent | None`
- `source: str` (`freesolo`, `prompted`, `fixture`, or `fallback`)
- `provider_name: str | None`
- `model_id: str | None`
- `used_fallback: bool`
- `valid_model_output: bool`
- `warnings: list[ParseWarning]`
- `raw_output_available: bool`

Do not return raw model text from the public API by default. Eval receives it directly through the runner/cache boundary.

## 5. Provider protocols

Use two narrow async protocols rather than one generic chat wrapper.

```python
class IntentProvider(Protocol):
    name: str
    model_id: str

    async def generate_intent(
        self,
        text: str,
        reference_date: date,
        card_context: tuple[IntentCardContext, ...],
    ) -> ProviderResponse: ...

class ParaphraseProvider(Protocol):
    name: str
    model_id: str

    async def generate_paraphrases(
        self,
        latent: LatentIntent,
        count: int,
    ) -> ProviderResponse: ...
```

`IntentCardContext` contains only synthetic card ID/name and whether an active bonus exists. Do not pass balances, limits, or rates to the language model; the model extracts intent, not decisions.

Provider implementations:

1. `FixtureIntentProvider`: maps a few exact demo fixture IDs/texts to raw JSON and can deliberately emit malformed cases for tests. Its source is visibly `fixture`.
2. `FixtureParaphraseProvider`: emits deterministic canned phrasings for pipeline tests only. It cannot be used to claim a real dataset size or model result.
3. `FreesoloIntentProvider`: add only after current authentication, request, response, and structured-output docs are confirmed.
4. `GeneralModelIntentProvider`: prompted comparison/fallback adapter after a provider is chosen.
5. `GeneralModelParaphraseProvider`: offline reverse-generation adapter, potentially same transport but a distinct protocol role.

Use `httpx.AsyncClient` injected into HTTP adapters. Configure connect/read timeouts, bounded retries for 429/5xx, and no automatic retry for 4xx schema/auth failures. Tests use `httpx.MockTransport` or protocol fakes.

## 6. Prompt contract

Version prompts as constants such as `INTENT_SYSTEM_PROMPT_V1` and include the version in provider/eval metadata.

System prompt requirements:

- State that output is one JSON object and no prose.
- Include the six exact goal keys and field meanings.
- Require finite nonnegative weights; recommend they sum to 1.
- Define utilization values as basis points with an example (`30%` -> `3000`).
- Require an absolute ISO date for `max_utilization_until`.
- Supply the reference date explicitly.
- Supply only valid synthetic card IDs for must-hit bonus extraction.
- Distinguish preferences from hard language. "I prefer low utilization" raises a weight; "never exceed 30%" creates a constraint.
- State that unspecified hard constraints are null/empty.
- Do not ask the model to recommend a card or calculate money.

The parser should validate output, not depend on the model perfectly following prose.

## 7. JSON extraction and validation

Implement `parse_provider_output` as a deterministic pipeline:

1. Reject an empty/whitespace output.
2. If the complete trimmed string parses as one JSON object, use it.
3. Otherwise recognize one Markdown JSON fence and extract its contents, adding a warning.
4. Do not search arbitrary prose for multiple possible objects. Ambiguity is a parse failure.
5. Parse with standards-compliant JSON and reject `NaN`, positive infinity, and negative infinity.
6. Require root object keys `weights` and `constraints` only, unless an explicitly versioned migration allows more.
7. Reject unknown goal/constraint keys. Silent dropping can turn a misunderstood hard constraint into unconstrained advice.
8. Fill omitted known goal keys with zero and warn.
9. Reject negative/nonfinite values and all-zero weights.
10. Normalize weights to a unit sum using `Decimal(str(value))` before constructing `Intent`; add a warning when input is not already within a strict decimal tolerance.
11. Validate constraint relationships and ISO dates through Pydantic.
12. Validate forced IDs against provided card context and require that each has a bonus.

Do not infer relative dates in this pipeline. The model receives a reference date and must output an absolute date. A failed date extraction triggers fallback rather than a guessed deadline.

## 8. Runtime parser behavior

Public entry point:

```python
async def parse_intent(
    text: str,
    reference_date: date,
    card_context: tuple[IntentCardContext, ...],
    provider: IntentProvider,
    *,
    allow_fallback: bool,
) -> ParseIntentResult: ...
```

Rules:

- Reject blank user text at API validation rather than calling a model.
- Call exactly the provided provider; provider chaining is owned by API configuration, not hidden in this function.
- On valid output, return `used_fallback=false`, even if normalization warnings exist.
- On provider or parse failure with `allow_fallback=true`, construct equal importance as `{goal: 1.0 for goal in Goal}`, let `Intent` normalize it, return no constraints, set `used_fallback=true`/`valid_model_output=false`, and emit visible warnings. Engine quantization deterministically yields `166667` ppm for the first four enum goals and `166666` for the final two.
- On failure with `allow_fallback=false`, return a parse result with `intent=None` or raise a typed `IntentParseError` according to the caller contract. Eval must be able to count failure without receiving a default intent.
- Never log raw user text at info level. Debug logging is opt-in and synthetic-demo-only.

Do not hard-code six rounded `0.1667` values because they drift from a unit sum. Build equal positive importance and use the same normalization path as all other intents; engine ppm quantization handles the nonterminating sixths deterministically.

## 9. Offline/manual demo behavior

Before external access exists, the UI offers:

- Manual goal sliders and hard-constraint controls as the reliable path.
- A small set of named intent presets loaded from synthetic scenario data.
- Optional fixture parser examples clearly labeled "Development fixture, not trained model."

Do not implement a keyword heuristic and present it as AI. A heuristic may exist as an explicitly named fixture for UI development, but Freesolo submission claims begin only after a real endpoint and frozen eval run exist.

## 10. Reverse synthetic-data generation

### Latent intent model

Each sampled latent record contains:

- Stable `latent_id` derived from seed/index.
- Reference date.
- Six ppm weights summing exactly to one million.
- Constraint object.
- Allowed card context.
- Sampling regime (`balanced`, `sparse`, `two_goal`, `constraint_heavy`).
- Requested language styles.

Store the exact target JSON generated from ppm as decimal weights with a canonical key order.

### Weight sampling

Use a seeded NumPy `Generator`, never global RNG state. Suggested mixture:

- 40% balanced: Dirichlet alpha around `1.5`.
- 30% sparse: choose a dominant goal receiving 65-90%, distribute remainder with low-alpha Dirichlet.
- 20% two-goal tension: choose two goals receiving at least 80% combined.
- 10% near-equal/adversarial precision.

Dirichlet floats are acceptable in offline label sampling; immediately quantize them to ppm with the shared deterministic helper. Record the seed and NumPy version.

### Constraint sampling

Sample constraints independently but with valid combinations:

- No hard constraint: roughly 50%.
- Utilization ceiling: choose from realistic integer bps such as 2000, 2500, 3000, 4000.
- Dated ceiling: when chosen, sample an ISO date after reference date and within a configured horizon.
- Must-hit bonus: select only from supplied cards with active, unmet bonuses.
- Combined utilization plus bonus: include enough examples to teach tension.

Avoid labels that are impossible because the parser should represent user intent even when the later scenario may be infeasible; however, card IDs must be valid.

### Split before paraphrasing

This ordering is mandatory:

1. Sample all latent intents.
2. Stable-shuffle latent IDs with the seed.
3. Assign 85% train and 15% test by latent ID.
4. Ask the provider for 1-3 paraphrases inside each assigned split.
5. Never move a paraphrase independently across splits.

This prevents near-identical language for one target vector from leaking into both sets.

### Style diversity

Request controlled styles, for example:

- Concise direct instruction.
- Conversational paragraph.
- Messy punctuation/abbreviation.
- Goal with explicit percentages.
- Deadline stated naturally relative to supplied date.
- Conflicting preference language.

Do not ask the generator to alter the target. Include target JSON in the prompt and require paraphrases only.

### Dedupe

Normalize only for duplicate detection:

- Unicode normalize.
- Lowercase.
- Collapse whitespace.
- Strip surrounding punctuation.

Hash normalized text. Reject exact normalized duplicates globally, including across splits. Optional near-duplicate detection may use token shingles or edit similarity, but do not add a large embedding dependency for the hackathon. Log rejection counts.

### Adversarial test examples

Add a small hand-written test-only set covering:

- Ambiguous "keep it low" without a numeric ceiling.
- Negation: "I do not care about cashback."
- Multiple dates.
- Unknown card nickname.
- Impossible/contradictory wording.
- Prompt injection asking for prose or a card recommendation.
- Zero/negative percentages in the text.

Gold labels are reviewed by both implementers. Keep them outside training data.

## 11. JSONL assembly and Freesolo gating

Maintain an internal canonical message record:

```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "{...}"}
  ],
  "metadata": {
    "latent_id": "latent-000001",
    "split": "train",
    "prompt_version": "intent-v1"
  }
}
```

Do not assume this is Freesolo's accepted upload schema. Once docs are available, add a pure exporter from internal records to the confirmed schema and a validation command. Never put `metadata` into an upload if the platform does not allow it.

Target 800-2000 accepted description/intent pairs after dedupe. Do not count failed provider calls or fixture paraphrases toward the claimed production dataset.

## 12. Caching, retries, and manifests

Cache key inputs:

- Provider and model ID.
- Prompt version.
- Canonical latent target.
- Requested style/count.

Cache raw text plus safe response metadata; never cache secrets/headers. Use atomic file replacement so an interrupted run does not corrupt cache.

Retry only transient failures with exponential backoff and bounded attempts. Respect provider retry headers when documented. A resumed run should reuse cache and produce byte-identical accepted records for the same seed/provider outputs.

Manifest fields:

- Schema and prompt versions.
- Seed and generation configuration.
- Provider/model IDs.
- Latent train/test counts.
- Accepted/rejected/retried counts.
- Dedupe counts.
- SHA-256 for canonical train/test/adversarial files.
- Generation start/end metadata may be recorded, but hashes exclude nondeterministic timestamps.

## 13. Training run record

After Freesolo access is confirmed, record:

- Platform documentation version/date.
- Upload schema.
- Base SLM ID and revision.
- SFT method (never RL).
- Hyperparameters actually used.
- Dataset hash and row counts.
- Training run ID/status.
- Output model ID/endpoint.
- Inference request/response contract.

Secrets remain environment variables. The committed run manifest may contain IDs and public configuration, not keys.

## 14. Tests

### Parser

- Exact JSON and fenced JSON.
- Empty/prose/multiple-object/malformed output.
- Missing key fill, unknown key rejection.
- Nonfinite, negative, all-zero, and non-normalized weights.
- Constraint relationship and card-ID validation.
- Provider timeout/error with fallback on and off.
- Equal fallback contains all six goals and warning metadata.
- Repeated parse is deterministic for same fixture response.

### Providers

- Request includes reference date, exact schema, and allowed card IDs.
- Timeouts/retry classification.
- No secret appears in `repr`, logs, response model, or cache fixture.
- Fixture source cannot be mislabeled as trained.

### Generation

- Same seed yields same latent labels and split.
- Ppm vectors sum exactly.
- Constraint combinations are valid.
- No latent ID crosses splits.
- Global dedupe catches cross-split duplicates.
- JSONL assistant content validates as `Intent`.
- Manifest hashes match files.
- Resume uses cache without provider call.

## 15. Completion checkpoints

### Local, no credentials

- Provider protocols and fixture providers implemented.
- Parser/fallback tests pass.
- Seeded latent generation, split, dedupe, JSONL, cache, and manifest tests pass.
- Eval can inject fixture model runners.
- UI can display source and fallback status honestly.

### External access

- Current Freesolo schema and endpoint are documented.
- Real general-model generation completes with a reviewed dataset.
- SFT run is reproducible from recorded metadata.
- Trained endpoint passes schema smoke tests.
- Frozen eval runs with fallback disabled.
