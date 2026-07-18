# Parallel Development Workflow

This repository is designed for two implementers. Parallel speed comes from freezing contracts and integrating frequently, not from letting branches independently redefine shared behavior.

## 1. Before coding

The directory currently may not be a Git repository. Initialize and publish a shared remote before both people begin:

```powershell
git init
git add .
git commit -m "Scaffold implementation contracts"
```

Only run these commands when the team is ready to establish history. Do not have both contributors start from uncommitted copies.

Both contributors read:

1. [PLAN.md](../PLAN.md), sections A-G.
2. [INTEGRATION_CONTRACTS.md](INTEGRATION_CONTRACTS.md).
3. [LOGIC_REVIEW.md](LOGIC_REVIEW.md).
4. Their owned module guides.

## 2. Ownership map

| Implementer | Primary write ownership | Shared review |
|---|---|---|
| A - deterministic product | `engine/`, `data/`, `explain/`, `api/`, `ui/` | Domain/API contracts and Sarah behavior |
| B - language/eval | `intent/`, `eval/` | Intent schema, provider metadata, frozen eval scenarios |

`tests/` is organized by owning module; each implementer writes tests with their code. Contract/integration tests are reviewed by both.

Shared high-conflict files:

- `PLAN.md`
- `pyproject.toml`
- `docs/INTEGRATION_CONTRACTS.md`
- `engine/models.py`
- `api/schemas.py`
- root `README.md`

Coordinate before editing these. Prefer one owner making a shared-file change while the other rebases afterward.

## 3. Branches and commits

Suggested branches:

- `engine-foundation` then short feature branches such as `greedy-allocation`, `api-ui`, `ilp-frontier`.
- `intent-eval-foundation` then `freesolo-adapter` after access.

Keep commits small and gate-aligned. Good commit boundaries:

- Domain models + schema tests.
- Scoring/objective + hand calculations.
- Parser validation + fixture provider tests.
- Greedy allocator + repair tests.
- Dataset sampling/split + manifest tests.
- Explanation models/templates.
- API route group + OpenAPI update.

Do not mix an unrelated refactor into a contract change. Do not commit credentials, generated caches, virtual environments, or model outputs without a deliberate artifact decision.

## 4. Integration order

### G0-G1: merge shared contract first

Implementer A lands:

- `engine/models.py`.
- Weight quantization helper.
- Synthetic loader skeleton.
- Domain schema/contract tests.

Implementer B reviews `Goal`, `Constraint`, `Intent`, and parser result expectations before merge. After G1, B develops against the merged types; do not copy them into `intent`.

### G2-G3 and G6-local in parallel

Implementer A works through scoring, feasibility, recommendation, greedy, and explanation.

Implementer B works through fixture providers, strict parser, latent sampling/split/dedupe, and fixture eval runners.

Integration touchpoint: API parse response needs `ParseIntentResult`; agree and merge that model before API wiring.

### G4 browser loop

A integrates API/UI with manual preset and fixture provider. B supplies one stable fixture provider and parser response examples, not a half-finished network adapter.

### G5/G7

A adds ILP/frontier/what-if while B uses external access for generation/SFT/eval. These paths share engine public APIs but should not require shared implementation edits.

## 5. Contract change protocol

Before changing a serialized field, enum, interpretation, or public signature:

1. Open a short coordination note/message stating current behavior, proposed behavior, and affected modules.
2. Identify the canonical owner.
3. Update model, integration contract, schema snapshot, owner guide, and tests in one branch.
4. Merge that branch before dependent branches implement the new shape.
5. Rebase both branches and run contract tests.

Do not maintain temporary aliases across a 1-2 day hackathon unless needed to keep a live demo branch running. It is cheaper to coordinate one schema migration than support two variants.

## 6. Daily/gate sync

At each gate, exchange only high-signal facts:

- Commit hash.
- Gate tests run and result.
- Public contract changes.
- Blockers/external access status.
- Next owned deliverable.

Recommended sync format:

```text
Gate: G2
Commit: <hash>
Passed: scoring/objective/model tests
Contract delta: RawFactorBreakdown.signup_progress_cents added
Blocked: none
Next: recommender + feasibility
```

If Git is unavailable, record this in a shared note, but establish version control as early as possible.

## 7. Merge verification

After merging either workstream:

1. Install/update editable dependencies only when `pyproject.toml` changed.
2. Run schema/dependency contract tests first.
3. Run tests for changed modules.
4. Run adjacent integration tests.
5. Run Sarah smoke after G4.
6. Never resolve a failing snapshot by blindly regenerating it.

At end of day one, the shared branch should contain a complete greedy/API/Streamlit path even if ILP and external model work remain on branches. Protecting a demonstrable baseline is more valuable than merging unfinished stretch work.

## 8. External-access handoff

When credentials/docs arrive, Implementer B records:

- Platform doc URL/version/date.
- Environment variable names, never values.
- Confirmed upload and inference schemas.
- Supported base model IDs.
- Training/run metadata requirements.

Review adapter contract before spending generation/training credits. Run one smoke example, inspect raw/cache redaction, then scale generation.

## 9. Definition of a clean handoff

A module is ready for another contributor when:

- Public models/functions match integration contracts.
- Focused tests pass offline.
- No TODO changes observable behavior without an issue/note.
- Example request/result exists in guide or test.
- Failure states are represented, not only happy path.
- Generated/external artifacts are clearly separated from source.
- The commit is pushed and its gate/status communicated.
