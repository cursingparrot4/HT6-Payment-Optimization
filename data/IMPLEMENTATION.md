# Product Reference and Synthetic Scenario Implementation Guide

## Implementation status

Implemented and validated:

- Eight Canadian card product definitions sourced from official issuer pages and verified on `2026-07-18`.
- Explicit issuer/network/reward-program provenance, source coverage, static point-value basis, engine assumptions, and unmodeled terms.
- Strict separation between public product terms and synthetic account state.
- Sarah's August 2026 scenario with four sourced products, twenty synthetic purchases, synthetic limits/balances/cycle dates, and one explicitly synthetic signup bonus.
- Five downstream eval probes with intent-sensitive winners.
- UTF-8 path-independent loaders, typed failures, cross-reference validation, and deep-copy isolation.

Current focused gate: `uv run python -m pytest tests/unit/data -q` and `uv run python -m ruff check data tests/unit/data`. The Sarah suite includes greedy behavior checks and bounded 10-second exact requests for both demo intents; exact timeout is acceptable only as an explicit successful `heuristic_fallback`.

## 1. Mission and boundary

The data module owns two deliberately separate layers:

1. **Public product reference data:** product names, issuer/network, ordinary annual fee, published earn rules, official source URLs, and a verification date.
2. **Synthetic scenario data:** persona, balances, limits, statement/due days, bonus progress, purchases, service qualification, and optimization intents.

Public product facts are paraphrased from official issuer pages; they are not endorsements or complete legal card agreements. Every simplification required by the engine is recorded beside the product. No real cardholder data, credentials, card numbers, account details, or transaction histories are stored.

Data depends on `engine.models` for validation. The engine must not import data.

## 2. Files

```text
data/
  IMPLEMENTATION.md
  __init__.py
  models.py
  loaders.py
  cards.json
  SOURCES.md
  scenarios/
    sarah_august_2026.json
    eval_probes.json
  generated/          # gitignored SFT output
  cache/              # gitignored provider cache
```

`cards.json` contains product definitions and provenance, never account state. Scenario/probe files reference product IDs and supply synthetic account fields. `loaders.py` combines the two into fresh engine `Card` objects.

## 3. Catalog design

The agreed catalog contains:

| Product ID | Public product | Engine role |
|---|---|---|
| `rbc-ion-plus` | RBC ION+ Visa | Everyday category points |
| `rbc-avion-visa-infinite` | RBC Avion Visa Infinite | Travel earn and optimistic static travel value |
| `td-rewards-visa` | TD Rewards Visa Card | Broad category multipliers with documented caps |
| `td-aeroplan-visa-infinite` | TD Aeroplan Visa Infinite Card | Airline points with dynamic value simplified to a static assumption |
| `amex-cobalt` | American Express Cobalt Card | High dining/grocery points value |
| `amex-gold-rewards` | American Express Gold Rewards Card | Travel/everyday points and Sarah's synthetic bonus account |
| `scotia-momentum-visa-infinite` | Scotia Momentum Visa Infinite Card | Grocery/recurring cashback with documented annual caps |
| `rogers-red-world-elite` | Rogers Red World Elite Mastercard | Flat cashback under a synthetic qualifying-service assumption |

Each `ProductDefinition` requires official issuer URLs and rejects non-issuer hosts. It records:

- Published annual fee and ordinary earn rates.
- Reward type and program.
- Static point valuation plus its official or assumed basis.
- Volatile public offer summary for provenance only.
- Engine assumptions.
- Unmodeled terms such as caps, MCCs, portal qualification, foreign currency, service status, and redemption bonuses.

Do not put limits, balances, statement days, due days, or bonus progress in `cards.json`.

## 4. Sarah scenario

Use a fixed future planning month so tests never depend on wall-clock time:

- `reference_date`: `2026-07-18`.
- Purchase horizon: August 2026.
- Mortgage/utilization demo cutoff: `2026-10-18` or another explicit future date supplied in intent.
- Portfolio: RBC Avion Visa Infinite, American Express Gold Rewards, Scotia Momentum Visa Infinite, and Rogers Red World Elite.
- Purchases: 20 items totaling `$5,890`.

Required purchases:

- `$2,200` recurring rent on August 1.
- At least four groceries purchases.
- At least three dining purchases.
- One travel purchase large enough to favor the travel card.
- Utilities, transit, and an `other` purchase to exercise base rates.
- One large one-off purchase.
- At least one purchase on or immediately after a card statement day for cashflow contrast.

The initial balances and limits must support these two acceptance stories:

1. **Mortgage story:** high credit-health weight plus a 30% per-card ceiling produces a complete plan, keeps every applicable card at/below the ceiling, and still makes visible progress toward the bonus if feasible.
2. **Travel story:** removing the hard ceiling and prioritizing travel changes at least three assignments.

Do not hard-code an expected card for every purchase until scoring defaults are implemented. Once Phase 2 stabilizes, store expected high-level invariants in tests rather than embedding solver output in JSON.

## 5. JSON shapes

### Product catalog

```json
{
  "schema_version": "1.0",
  "document_type": "product_catalog",
  "verified_on": "2026-07-18",
  "terms_notice": "Public terms plus documented engine assumptions.",
  "products": []
}
```

### Scenario

```json
{
  "schema_version": "1.0",
  "id": "sarah-august-2026",
  "name": "Sarah's August plan",
  "document_type": "scenario",
  "synthetic_persona": true,
  "persona_label": "Sarah (synthetic)",
  "reference_date": "2026-07-18",
  "accounts": [],
  "purchases": [],
  "demo_intents": {
    "mortgage": {},
    "travel": {}
  }
}
```

`accounts` contains only synthetic account state and may attach an explicitly synthetic one-stage bonus. `demo_intents` contains explicit validated presets for offline reliability; neither pretends to be LLM output or real user data.

## 6. Loader contract

Public functions:

```python
def load_product_catalog(path: Path | None = None) -> ProductCatalog: ...

def load_card_catalog(path: Path | None = None) -> list[ProductDefinition]: ...

def load_scenario(
    scenario_id: str = "sarah-august-2026",
    data_root: Path | None = None,
) -> LoadedScenario: ...

def list_scenarios(data_root: Path | None = None) -> list[ScenarioMetadata]: ...
```

Implementation rules:

1. Resolve default paths relative to `loaders.py`, never current working directory.
2. Read UTF-8 with Python's JSON parser.
3. Reject duplicate product IDs and non-official source hosts.
4. Reject missing or duplicate scenario card references.
5. Reject duplicate purchase IDs.
6. Validate public product and synthetic account objects through separate Pydantic contracts.
7. Return fresh models; scenario mutation cannot contaminate catalog definitions or later loads.
8. Raise a typed `DataLoadError` containing path and stable reason code for malformed repository data. API startup health translates it; do not expose a raw stack trace to UI.

No loader performs scoring or silently repairs invalid values.

## 7. Eval probes

`eval_probes.json` should define small deterministic scenarios where intent can change the best card. Include at least:

- Rent: rent cashback versus bonus progress versus utilization.
- Grocery: category cashback versus low utilization.
- Travel: point value versus cash baseline.
- Dining: specialist points versus cashflow timing.
- Large one-off: capacity/risk versus reward.

Each probe contains sourced product IDs, synthetic account states, one synthetic purchase, and no gold card answer. During evaluation the engine derives the gold recommendation from the gold intent, preventing stale hand labels.

Avoid a probe where one card dominates every objective; it contributes little information to downstream match.

## 8. Synthetic SFT data directories

Generated training/eval files are outputs, not source fixtures:

```text
data/generated/
  manifest.json
  train.jsonl
  test.jsonl
  adversarial_test.jsonl
```

The directory is gitignored by default because provider-generated corpora may be large and regenerated. For submission, decide explicitly whether final frozen datasets should be force-added or attached as release artifacts. The manifest records seed, latent count, accepted example count, split counts, provider/model, prompt version, schema version, and SHA-256 hashes.

Provider response caches go under `data/cache/` and must never include authorization headers.

## 9. Validation tests

- Catalog and Sarah scenario load from any working directory.
- Every product has official issuer provenance and every account/person/purchase is marked/documented synthetic.
- IDs are unique and references resolve.
- All money/rates/value fields are integers, not booleans or floats.
- Card days are 1-28.
- At least one card covers each required category through a rule or base.
- Sarah has exactly one recurring rent purchase for the what-if control.
- Sarah purchase dates remain inside August 2026.
- Demo intents include all six goals and valid card references.
- The default mortgage and travel scenarios are feasible once the allocator exists.
- Eval probes produce at least two distinct winners across a controlled intent matrix.

## 10. Coordination rules

- Engine contributor owns model semantics; data contributor adapts JSON to them.
- Data contributor may propose model changes but cannot add private fields to JSON and bypass Pydantic.
- Intent contributor uses only catalog card IDs supplied by generation config when sampling forced bonus constraints.
- Eval contributor treats scenario files as immutable during a frozen run and records their hash.
- UI contributor obtains scenarios through API; it does not open JSON directly.

## 11. Completion checklist

- Catalog includes all planned behavioral roles.
- Sarah scenario supports both demo narratives without hidden mutation.
- Loaders resolve paths robustly and validate all cross-references.
- Real product names/ordinary terms are sourced and timestamped; no copied long-form terms appear.
- No real PII, credential, card number, account state, or transaction appears.
- Tests establish synthetic, integer, and temporal invariants.
- Generated/cached data locations and manifest rules are documented.
