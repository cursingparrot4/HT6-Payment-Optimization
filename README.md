# Payment Optimization Engine

A hackathon prototype that separates language understanding from financial calculation:

- A deterministic engine recommends a card and allocates a synthetic month of purchases using timestamped public product terms.
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
| `data` | Official product references plus validated synthetic accounts, purchases, and probes |
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

The deterministic engine module is complete: shared contracts, fixed-point weights, scoring, feasibility, exact single-purchase recommendation, greedy repair/local search, exact all-binary PuLP/CBC allocation with brute-force parity checks, sampled strategy frontier, reoptimized what-if, and final-state alternatives. The sourced eight-product catalog, synthetic Sarah scenario, and five eval probes are also implemented. Parser/eval adapters, explanation templates, API, and UI remain pending.

Normal CBC requests run in an isolated process with a configurable hard wall limit of at most 60 seconds. A timeout or native solver failure returns a verified, explicitly labeled heuristic fallback instead of hanging the caller.

Validate the current module:

```powershell
uv sync --extra dev
uv run python -m pytest tests/unit/engine tests/oracle -q
uv run python -m ruff check engine tests/unit/engine tests/oracle
```

The generated Windows `pytest.exe` launcher may be denied in some environments; `uv run python -m pytest` is the verified invocation.
