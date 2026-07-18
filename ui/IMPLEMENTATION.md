# Streamlit UI Implementation Guide

> **Status (2026-07-18):** this guide planned a Streamlit demo, but the shipped UI is the
> SwitchPay web app in `ui/web` — Next.js 14 + TypeScript + Tailwind, run with
> `npm run dev` (see `ui/web/README.md`). It talks to the FastAPI backend over HTTP and
> follows this guide's core boundary rule: it never recomputes financial metrics, it only
> renders structured API output. The Streamlit-specific file layout, run contract, and
> visual direction below were superseded by the shipped design and are kept for reference
> only.

## 1. Mission and boundary

The UI is the operational demo surface for the synthetic payment planner. The first screen is the actual tool: portfolio and goal controls alongside the monthly plan. It is not a marketing landing page.

The UI communicates with FastAPI over HTTP. It does not import engine scoring/optimization functions, open JSON fixtures directly, call model providers, or recompute financial metrics. Its job is input editing, request orchestration, state management, rendering, and accessible interaction.

## 2. Files

```text
ui/
  IMPLEMENTATION.md
  __init__.py
  app.py             # page config, data boot, top-level layout
  api_client.py      # typed-ish HTTP boundary and error mapping
  state.py           # session-state keys and reset helpers
  components.py      # reusable rendering functions
  forms.py           # portfolio, purchase, intent, constraint forms
  charts.py          # utilization/bonus/frontier charts
  styles.py          # scoped CSS and design tokens
```

Keep one Streamlit page with tabs rather than a multipage navigation hierarchy. This makes the two-minute demo predictable.

## 3. Run contract

The API must be running first:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\streamlit.exe run ui/app.py --server.address 127.0.0.1 --server.port 8501
```

Read API base URL from `PAYMENT_ENGINE_API_URL`, default `http://127.0.0.1:8000`. On startup, call `/health` with a short timeout and show one actionable service-error panel if unavailable. Do not expose a stack trace.

## 4. Visual direction

This is a financial operations tool: compact, calm, and highly scannable rather than decorative.

- Use an off-white work surface, near-black text, green for positive value, amber for cautions, red only for blocking errors, and a restrained blue for selection/navigation.
- Avoid a one-color theme, purple gradients, dark-mode default, oversized hero type, floating decorative blobs, and nested cards.
- Use square/low-radius controls and panels (`4-8px` radius).
- Bundle or select a purposeful readable font with a legal local fallback; do not rely on a remote font for demo correctness.
- Use full-width unframed sections. Cards are reserved for individual decision records and error/what-if result blocks.
- Motion is limited to Streamlit progress/spinner states and a brief result reveal; no generic hover animation is necessary.
- Keep numeric columns tabular and right-aligned.

Define tokens in `styles.py` and scope CSS to stable custom wrappers where possible. Streamlit internal class names can change; avoid brittle broad selectors.

## 5. Stable layout

Desktop:

- Narrow left sidebar: scenario/portfolio controls and service status.
- Main header row: product name, solver status badge, and compact action controls.
- Goal/constraint editor band.
- Tabs: `Month plan` (default), `One purchase`, `Strategies`, `What-if`.

Narrow viewport:

- Sidebar remains collapsible.
- Goal controls stack.
- Dataframes use horizontal scrolling rather than compressed unreadable cells.
- Decision-card metric rows wrap without overlapping.
- Charts use full available width and fixed minimum height.

Do not place explanatory onboarding prose in a visible feature tour. Tooltips/help text on technical controls are acceptable.

## 6. Session state

Use named constants for keys:

- `scenario`
- `cards_draft`
- `purchases_draft`
- `goal_text`
- `intent_draft`
- `parse_result`
- `solver_preference`
- `allocation_response`
- `recommendation_response`
- `frontier_response`
- `what_if_response`
- `last_request_hash`

Initialization flow:

1. Health check.
2. Load `/demo-scenario` once and copy it into editable draft state.
3. Initialize intent from named `mortgage` preset; mark source `manual preset`.
4. Do not solve automatically on every widget change. Use explicit `Plan month` and related form-submit actions.

Preserve drafts across ordinary Streamlit reruns. Use this reset matrix:

| Change | Clear |
|---|---|
| Scenario reset | Card/purchase drafts, parse result, all solver responses, request hash; then initialize the scenario's manual intent preset |
| Card or purchase form submitted | Recommendation, allocation, frontier, and what-if responses |
| Goal text edited but not parsed | Nothing yet; mark existing parse/result state stale visually |
| Parse succeeds or falls back | Intent draft and all solver responses |
| Manual weight/constraint form submitted | All solver responses; retain goal text/parse source for provenance |
| Solver preference changes | Allocation, frontier, and what-if responses |

Never ask for confirmation during the scripted reset; provide one explicit `Reset Sarah scenario` command.

## 7. Portfolio editor

The default demo should work without editing. Put edits behind a compact expander/sidebar section.

Allow light editing only:

- Current balance.
- Credit limit.
- Statement/due day.
- Signup spend-so-far.
- Purchase amount/date/category/lock.

Do not build a full reward-rule authoring UI in the hackathon. Show rules read-only in a compact table or popover. Validate monetary input as dollars for humans but convert with `Decimal` to integer cents before request submission. Never use `float * 100`; Streamlit numeric widgets may produce floats, so pass through decimal strings and reject more than two currency decimal places.

Use familiar icons only when Streamlit supports them reliably; label destructive/reset commands clearly. Every unfamiliar icon has a tooltip/help label.

## 8. Intent editor

### Natural-language input

Provide a text area prefilled with Sarah's mortgage goal. `Parse goal` calls `/parse-intent` with reference date and card context.

Show parser source next to the result:

- `Trained SLM` when real Freesolo is active.
- `Prompted fallback` only when explicitly enabled.
- `Development fixture` for fixture provider.
- `Manual/default fallback` with a visible caution when parsing failed.

Never hide fallback behind a normal success color.

### Weight controls

Render six compact sliders/number inputs as relative importance, not fake probabilities the user must manually sum. On submit:

1. Require at least one positive importance.
2. Normalize to a unit sum with `Decimal`.
3. Show normalized percentages beside labels.
4. Send all six keys.

Use concise goal labels: Cashback, Travel value, Credit health, Signup bonus, Cashflow, Headroom risk.

### Hard constraints

Render separately from weights:

- Toggle for per-card utilization ceiling plus percentage input.
- Optional cutoff date when ceiling is enabled.
- Multi-select only among cards with active bonuses for must-hit constraints.

After parsing, display active constraints as compact chips/labels in a full-width band. Do not render them as sliders; this distinction is central to the demo narrative.

## 9. Month plan tab

Primary action: `Plan month` with solver segmented control (`Exact ILP`, `Fast heuristic`). Disable exact option or mark unavailable when health says CBC is absent.

Result order:

1. Status line: method/status and warnings.
2. Four compact metrics: projected reward value, max card utilization, bonus state, total float days.
3. Per-card utilization and bonus-progress bars.
4. Assignment table sorted by date, with recurring rent visually marked.
5. Highlighted decision cards for rent and top two largest purchases.
6. Remaining per-purchase explanations in expanders.
7. Failure/relaxation block instead of empty charts when infeasible/unresolved.

Use engine values exactly. Format through UI helpers only; do not derive projected totals by summing table rows.

### Progress bars

Stable dimensions prevent layout shifts:

- Utilization bar domain is 0-100%; overlay/annotate hard ceiling when active.
- Bonus bar domain is 0-required spend and distinguishes prior spend from planned eligible spend if API exposes both.
- Do not cap textual utilization at 100% for already-invalid imported state even if the visual bar caps.
- Provide text values for accessibility and precision.

## 10. One purchase tab

Select an existing purchase or enter a synthetic one-off. Use a form so edits submit together. Render:

- Winner card and exact/locked status.
- Raw factor lines.
- Runner-up comparison or infeasible alternative.
- Candidate ranking in an optional compact table.
- Constraint exclusions.

Do not label internal utility as a percentage score such as `91/100` unless a separately defined normalization exists. Prefer raw factors and, if shown, label utility points explicitly.

## 11. Strategies tab

Button: `Generate sampled strategies`. Do not recompute automatically on slider drag.

Render:

- Disclosure: selected goals, attempted weight settings, successful plans, exact/heuristic statuses, and incomplete-frontier notice.
- A 2D scatter only when two selected dimensions can be plotted clearly. Choose x/y from returned selected goals and label units/directions.
- For three goals, use two axes plus table columns rather than misleading 3D graphics.
- A table of 3-5 representative plans with label, key raw metrics, and solver status.
- Expandable allocation/explanation for each point.

Use Altair explicitly for deterministic axis titles/tooltips and accessible marks; add it as a direct dependency. Do not imply upper/right is always better when an axis is a minimized penalty; label `lower is better`.

## 12. What-if tab

One supported workflow:

1. Default purchase is recurring rent.
2. Select an override card other than its current/base assignment.
3. Click `Reoptimize with override`.
4. Show base versus override deltas and which other purchases moved.

Clarify that the selected purchase is locked and the remainder is reoptimized. If infeasible, show blocking issues/suggestions and keep the valid base summary visible.

No chat interface or multi-turn conversational what-if is in scope.

## 13. API client

Use one cached `httpx.Client` or simple request helper with:

- Base URL from environment.
- Connect/read timeouts; frontier gets a slightly longer read timeout.
- Typed `ApiUnavailable`, `ApiValidationError`, and `ApiResponseError` exceptions.
- `raise_for_status` followed by top-level `schema_version`, `data`, and `warnings` envelope checks.
- No automatic retry for allocation/frontier POSTs beyond a safe connection failure policy; duplicate calls are deterministic but unnecessary.

Do not log complete request bodies. A response with domain `infeasible` is a successful HTTP response and should not raise.

## 14. Loading, empty, and error states

- Disable a form's submit button while its request is active where Streamlit supports it.
- Show operation-specific spinner text (`Solving 15 sampled strategies...`).
- Keep the prior successful result visible only if clearly marked stale after inputs change; simplest first version clears it on submit.
- Empty state tells the user which action is available, not a product feature tour.
- API unavailable state gives exact local start command.
- Provider fallback warning stays visible next to editable weights.
- Solver timeout fallback warning stays visible above plan metrics.

## 15. Accessibility and text integrity

- Do not rely on color alone; every tone has a text label/icon.
- Charts have titles, axis labels, and tabular alternatives.
- Controls have unique labels/help.
- Long card/purchase names wrap.
- Numeric inputs show units.
- Avoid tiny text below 12px.
- Verify no clipped text at 1280x800 and a narrow mobile-like width.

## 16. Tests and verification

### Pure helpers

- Dollar string to cents and back without float drift.
- Weight normalization and all-zero rejection.
- Constraint form mapping.
- Status/tone mapping for all result states.

### API client

- Health/demo scenario/success/domain failure/infrastructure failure.
- Schema-version mismatch.
- Timeout behavior.
- No request-secret logging.

### Streamlit AppTest

Use `streamlit.testing.v1.AppTest` where practical:

- App boots against mocked client/scenario.
- Default Month plan tab and Sarah data render.
- Fallback source warning appears.
- Infeasible/unresolved response does not render empty success metrics.
- Exact solver option reflects health.
- Form submission calls correct endpoint once.

### Manual visual check

- Desktop and narrow widths.
- Mortgage and travel intents.
- Long names and maximum warnings.
- Strategy chart nonempty and correctly directed.
- Rent what-if and infeasible override.
- No overlapping controls/text or nested-card appearance.

## 17. Completion checklist

- First viewport is the working planner, not a landing page.
- All engine facts arrive over API and remain unrecomputed.
- Parser and solver fallbacks are visible.
- Hard constraints are visually distinct from preferences.
- Rent anchors the monthly and what-if story.
- Sampled frontier limitations are explicit.
- Desktop/narrow layouts remain readable.
- Full Sarah script can be performed in about two minutes.
