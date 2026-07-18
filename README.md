# SwitchPay — Payment Optimization Engine

**SwitchPay** (Hack the 6ix, Chexy track) is a card-switching and payment-routing system:
it reevaluates the best funding card before every major recurring payment (rent, tuition,
utilities, insurance, taxes) and safely adapts when rewards, welcome bonuses, balances,
limits, deadlines, or card availability change. All data is synthetic; no real money moves.

## Run SwitchPay

Backend (FastAPI + SQLite, reuses the deterministic `engine` scoring):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn api.main:app --port 8000
```

Frontend (Next.js + TypeScript + Tailwind):

```bash
cd ui/web
npm install
npm run dev   # http://localhost:3000
```

Open http://localhost:3000 — the dashboard's **Reset demo** button loads the canonical
scenario: a $2,400 monthly rent and three cards where the Aeroplan card wins this month
(its welcome bonus completes), the 2% cashback card takes over next month, and the 1.5%
card serves as backup when the others fail. The dashboard also shows priority-aware
routing: drag bills to reorder them, and higher-priority bills reserve credit headroom
and bonus progress before lower ones are scored, with any "off optimal" loss quantified
per payment.

Key properties:

- **Deterministic recommendations.** All reward, bonus, utilization, headroom, and fee math
  is integer-cent arithmetic in `engine/scoring.py` + `api/recommender.py`. No AI model
  performs arithmetic or makes the final financial decision.
- **Explicit payment state machine** (`api/state_machine.py`): scheduled → authorization
  pending → authorized → processing → recipient paid → reconciled, with failed and
  status-uncertain branches. The simulator can trigger declines, insufficient credit,
  locked/expired cards, network timeouts, duplicate requests, and unknown authorization.
- **Idempotency keys** make double-pay clicks return the original transaction instead of
  creating a second charge. Uncertain payments are never blindly retried — SwitchPay
  verifies the original transaction first. Confirmed declines rerun card selection and
  recommend the backup card without duplicating the payment.
- **User-approved switches.** Every card switch requires explicit approval; every payment
  action is written to an audit log. Cards are stored as fake `synthetic_tok_*` tokens —
  no real card numbers exist anywhere.

Validate the SwitchPay layer: `.venv/bin/python -m pytest tests/unit -q`

---

The sections below describe the broader hackathon prototype this repo also hosts, which
separates language understanding from financial calculation:

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

| Module | Responsibility | Status |
|---|---|---|
| `engine` | Domain models, integer scoring, constraints, optimization, and what-if | Complete: greedy, exact PuLP/CBC ILP, sampled strategy frontier, what-if |
| `data` | Official product references plus validated synthetic accounts, purchases, and probes | Implemented: sourced 8-card catalog (`data/cards.json`), loaders, Sarah scenario, eval probes; also holds SwitchPay's runtime SQLite (`data/switchpay.db`, gitignored) |
| `intent` | LLM provider boundary, output validation, SFT data generation | Pending (guide only) |
| `explain` | Structured templates derived from engine facts | Pending (guide only) |
| `api` | FastAPI orchestration: SwitchPay product endpoints plus engine `/api/recommend`, `/api/allocate`, `/api/demo-scenario` | Implemented |
| `ui` | SwitchPay web app (Next.js + TypeScript + Tailwind, in `ui/web`); the original guide planned Streamlit | Implemented |
| `eval` | Frozen model comparisons and downstream decision metrics | Pending (guide only) |
| `tests` | Unit, property, oracle, contract, and end-to-end verification | `tests/unit/engine`, `tests/unit/data`, `tests/unit/api`, `tests/oracle` implemented |

## Environment

Python 3.11 or newer is supported. On Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Current implementation

The deterministic engine module is complete: shared contracts, fixed-point weights, scoring, feasibility, exact single-purchase recommendation, greedy repair/local search, exact all-binary PuLP/CBC allocation with brute-force parity checks, sampled strategy frontier, reoptimized what-if, and final-state alternatives. The sourced eight-product catalog, synthetic Sarah scenario, and five eval probes are also implemented in `data/`.

Normal CBC requests run in an isolated process with a configurable hard wall limit of at most 60 seconds. A timeout or native solver failure returns a verified, explicitly labeled heuristic fallback instead of hanging the caller.

On top of the engine, the SwitchPay product layer is implemented end to end: the FastAPI backend (`api/`) with card/payment CRUD, priority-aware routing (`PUT /api/payment-priorities`, `build_priority_plan`), switch recommendations, the payment state machine with idempotency and failover, and versioned engine endpoints (`/api/recommend`, `/api/allocate`, `/api/demo-scenario`); and the Next.js UI (`ui/web`).

Still pending from the original plan: the intent parser (`intent/`), the templated explanation layer (`explain/`) as a separate module, and model evaluation (`eval/`). SwitchPay's recommendation explanations are currently templated inside `api/recommender.py`.

Validate everything implemented:

```bash
.venv/bin/python -m pytest tests/unit tests/oracle -q   # engine + data + SwitchPay layers
.venv/bin/python -m ruff check api engine data tests/unit tests/oracle
```

On Windows with uv: `uv sync --extra dev`, then `uv run python -m pytest tests/unit -q` (the generated `pytest.exe` launcher may be denied in some environments; `uv run python -m pytest` is the verified invocation).
