# SwitchPay Web UI

Next.js 14 (App Router) + TypeScript + Tailwind frontend for SwitchPay. It renders
structured output from the FastAPI backend and never recomputes financial metrics —
all scoring comes from the deterministic engine via the API.

## Run

The backend must be running first (from the repo root):

```bash
.venv/bin/uvicorn api.main:app --port 8000
```

Then:

```bash
npm install
npm run dev   # http://localhost:3000
```

`next.config.mjs` rewrites `/api/*` to `http://127.0.0.1:8000/api/*`, so the browser
talks to same-origin paths and no API URL configuration is needed in development.

## Pages

- `/` — dashboard: priority-ordered payment routing (drag or arrow-reorder, saved via
  `PUT /api/payment-priorities`), off-optimal impact, recommended switches, alerts,
  utilization, and totals
- `/cards` — synthetic card wallet with add/edit/remove
- `/payments` — recurring payment CRUD
- `/payments/[id]` — full card ranking, scoring breakdown, switch approval flow
- `/tracker` — payment simulator and state-machine timeline (success, declines,
  timeouts, uncertain-authorization verification, duplicate-click idempotency demo)

## Structure

- `src/lib/api.ts` — typed API client, money/percent formatting, idempotency keys
- `src/components/` — `Sidebar` (top navigation bar) and shared UI primitives
- `src/app/` — one directory per page, all client components

Everything displayed is synthetic. Type-check with `npx tsc --noEmit`.
