# Payment Optimization Engine

A hackathon prototype that separates language understanding from financial calculation:

- A deterministic engine recommends a card and allocates a synthetic month of purchases.
- A small SFT model parses natural-language goals into validated preferences and constraints.
- A template layer explains actual solver output.
- FastAPI and Streamlit provide the demo workflow.

No real accounts, credentials, personally identifying information, or money movement are used.

## Start here

1. Read [PLAN.md](PLAN.md) for scope, ownership, sequencing, and integration gates.
2. Read [docs/INTEGRATION_CONTRACTS.md](docs/INTEGRATION_CONTRACTS.md) before changing shared types.
3. Read [docs/PARALLEL_WORKFLOW.md](docs/PARALLEL_WORKFLOW.md) before creating parallel branches.
4. Read the `IMPLEMENTATION.md` in the module you own.
5. Check [docs/LOGIC_REVIEW.md](docs/LOGIC_REVIEW.md) before revisiting a resolved design decision.
6. Use [FAILURE_MODES.md](FAILURE_MODES.md) for domain, solver, provider, and demo recovery behavior.

## Module map

| Module | Responsibility |
|---|---|
| `engine` | Domain models, integer scoring, constraints, optimization, and what-if |
| `data` | Validated synthetic card and purchase scenarios |
| `intent` | LLM provider boundary, output validation, SFT data generation |
| `explain` | Structured templates derived from engine facts |
| `api` | FastAPI schemas and orchestration |
| `ui` | Streamlit HTTP client and operational demo |
| `eval` | Frozen model comparisons and downstream decision metrics |
| `tests` | Unit, property, oracle, contract, and end-to-end verification |

## Environment

Python 3.11 or newer is supported. On Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Current implementation

The deterministic engine is implemented through the greedy monthly-allocation milestone: shared contracts, fixed-point weights, scoring, feasibility, exact single-purchase recommendation, aggregate plan evaluation, bounded repair/local search, and final-state alternatives. Exact ILP, sampled strategies, what-if, synthetic scenario files, parser/eval adapters, explanations, API, and UI remain pending.

Validate the current module:

```powershell
uv sync --extra dev
uv run python -m pytest tests/unit/engine -q
uv run python -m ruff check engine tests/unit/engine
```

The generated Windows `pytest.exe` launcher may be denied in some environments; `uv run python -m pytest` is the verified invocation.
