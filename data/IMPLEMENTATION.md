# Synthetic Data Implementation Guide

## 1. Mission and boundary

The data module owns realistic-but-fake inputs that exercise the engine and tell the demo story. It provides validated loaders for a reusable card catalog and named scenarios. It never contains real cardholder data, credentials, copied issuer terms, or a hidden source of business logic.

Data depends on `engine.models` for validation. The engine must not import data.

## 2. Files

```text
data/
  IMPLEMENTATION.md
  __init__.py
  loaders.py
  cards.json
  scenarios/
    sarah_august_2026.json
    eval_probes.json
  generated/          # gitignored SFT output
  cache/              # gitignored provider cache
```

`cards.json` is the complete synthetic catalog. Scenario files reference card IDs and contain purchases plus optional default intents. `loaders.py` resolves references and validates the assembled `Scenario` model.

## 3. Catalog design

Create 6-8 clearly synthetic cards. Names must not contain actual issuer/product names. Use IDs that remain stable even if display names change.

Recommended catalog roles:

| ID | Role | Behavior to exercise |
|---|---|---|
| `harbor-rent` | Rent-oriented cash card | Strong rent cashback, moderate limit |
| `summit-journey` | Travel points card | Travel/dining multipliers, higher point value |
| `aurora-bonus` | Signup-bonus card | Large remaining threshold and near deadline |
| `maple-everyday` | Flat cashback | Reliable baseline/runner-up |
| `cedar-starter` | Low-limit card | Capacity/utilization warnings |
| `metro-table` | Dining specialist | Category tradeoff |
| `lakeview-market` | Grocery specialist | Category tradeoff |
| `northstar-flex` | High-limit low-reward card | Credit-health/headroom option |

Vary:

- Limits from roughly `$1,500` to `$15,000`.
- Current utilization from near zero to roughly 35%.
- Statement days across the month.
- Due days that create visibly different float for the scenario dates.
- Cashback versus point/mile base types.
- Static point values from `0.8` to `1.5` cents represented in millicents.
- Bonus presence and deadline.

Do not optimize fixture values to imitate a real issuer. Add a catalog-level note that reward structures are fabricated for demonstration and may be inspired only by common public patterns such as category multipliers.

## 4. Sarah scenario

Use a fixed future planning month so tests never depend on wall-clock time:

- `reference_date`: `2026-07-18`.
- Purchase horizon: August 2026.
- Mortgage/utilization demo cutoff: `2026-10-18` or another explicit future date supplied in intent.
- Portfolio: 4 cards selected from the catalog.
- Purchases: 18-22 items totaling enough to create routing tension without making the default scenario infeasible.

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
2. **Travel story:** removing the hard ceiling and prioritizing travel changes at least three assignments and may move rent only when travel/bonus utility justifies it.

Do not hard-code an expected card for every purchase until scoring defaults are implemented. Once Phase 2 stabilizes, store expected high-level invariants in tests rather than embedding solver output in JSON.

## 5. JSON shapes

### Card catalog

```json
{
  "schema_version": "1.0",
  "synthetic": true,
  "attribution": "Fabricated reward structures for demonstration.",
  "cards": []
}
```

### Scenario

```json
{
  "schema_version": "1.0",
  "id": "sarah-august-2026",
  "name": "Sarah's August plan",
  "synthetic": true,
  "reference_date": "2026-07-18",
  "card_ids": [
    "harbor-rent",
    "summit-journey",
    "aurora-bonus",
    "cedar-starter"
  ],
  "purchases": [],
  "demo_intents": {
    "mortgage": {},
    "travel": {}
  }
}
```

`demo_intents` contains explicit validated intents for offline reliability and testing. It does not pretend to be LLM output. The UI may load one as a manual preset.

## 6. Loader contract

Public functions:

```python
def load_card_catalog(path: Path | None = None) -> list[Card]: ...

def load_scenario(
    scenario_id: str = "sarah-august-2026",
    data_root: Path | None = None,
) -> Scenario: ...

def list_scenarios(data_root: Path | None = None) -> list[ScenarioMetadata]: ...
```

Implementation rules:

1. Resolve default paths relative to `loaders.py`, never current working directory.
2. Read UTF-8 with Python's JSON parser.
3. Reject duplicate catalog card IDs before building a lookup.
4. Reject missing or duplicate scenario card references.
5. Reject duplicate purchase IDs.
6. Validate every object through Pydantic.
7. Return fresh model instances/lists; callers may safely copy/update without contaminating later requests.
8. Raise a typed `DataLoadError` containing path and stable reason code for malformed repository data. API startup health translates it; do not expose a raw stack trace to UI.

No loader performs scoring or silently repairs invalid values.

## 7. Eval probes

`eval_probes.json` should define small deterministic scenarios where intent can change the best card. Include at least:

- Rent: rent cashback versus bonus progress versus utilization.
- Grocery: category cashback versus low utilization.
- Travel: point value versus cash baseline.
- Dining: specialist points versus cashflow timing.
- Large one-off: capacity/risk versus reward.

Each probe contains cards (or card IDs), one purchase, and no gold answer. During evaluation the engine derives the gold recommendation from the gold intent, preventing stale hand-labeled cards.

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
- Every object is marked/documented synthetic.
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
- No real brand, PII, credential, or copied terms appear.
- Tests establish synthetic, integer, and temporal invariants.
- Generated/cached data locations and manifest rules are documented.
