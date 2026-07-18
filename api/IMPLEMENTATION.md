# FastAPI Implementation Guide

## 1. Mission and boundary

The API is a thin orchestration and serialization layer. It validates request shape, selects configured providers/solvers, calls the deterministic engine, asks the explanation layer to structure the result, and returns versioned JSON.

It does not duplicate financial calculations, maintain user accounts, persist portfolios, train models, or translate domain infeasibility into server errors.

## 2. Files

```text
api/
  IMPLEMENTATION.md
  __init__.py
  main.py          # create_app and exported app
  settings.py      # environment-backed settings
  schemas.py       # request/response wrappers
  dependencies.py  # provider, config, fixture dependencies
  services.py      # orchestration functions
  errors.py        # typed infrastructure exception handlers
```

Keep route handlers small. `services.py` may combine modules but cannot calculate scores.

## 3. Application factory

```python
def create_app(
    settings: Settings | None = None,
    intent_provider: IntentProvider | None = None,
) -> FastAPI: ...

app = create_app()
```

The factory enables isolated TestClient apps with fixture providers. Do not read environment variables at import time beyond constructing the default exported app. Settings use Pydantic settings only if added explicitly; otherwise a small Pydantic model plus `os.environ`/dotenv is sufficient. Avoid an unnecessary dependency.

Startup/lifespan checks:

- Synthetic catalog and Sarah scenario load.
- Engine config validates.
- CBC availability is probed and recorded; failure does not stop greedy endpoints.
- Configured intent provider is constructed without making a billed inference call.
- Missing external credentials mark provider unavailable; development fixture/manual paths remain healthy.

## 4. Limits and input safety

This is a local synthetic demo, but bound expensive inputs:

- Goal text: 1-2,000 characters.
- Cards: 1-8.
- Purchases: 1-60.
- Reward rules per card: at most 20.
- Forced bonus card IDs: at most number of cards.
- Frontier requested representatives: 1-5.
- Frontier internal grid: no more than 15 solves.
- Card/purchase/name/category ID lengths: bounded in domain models.

Pydantic rejects malformed structure with HTTP 422. Do not accept arbitrary file paths or provider endpoint URLs in request bodies.

## 5. Versioned response envelope

Every endpoint uses the same top-level generic envelope; do not put `schema_version` inside endpoint data:

```python
DataT = TypeVar("DataT")

class ApiResponse(BaseModel, Generic[DataT]):
    schema_version: Literal["1.0"] = "1.0"
    data: DataT
    warnings: list[ApiWarning] = Field(default_factory=list)
```

Endpoint-specific data models contain the result/explanation fields. Envelope warnings describe orchestration/provider fallback; domain result warnings remain inside the result where they are attributable.

Do not add request timestamps or random response IDs; deterministic engine requests should canonicalize to deeply equal JSON in tests. Infrastructure logs may include a generated correlation ID without putting it into the response.

## 6. Shared request models

Use domain models directly inside wrappers:

```python
class PortfolioRequest(BaseModel):
    cards: list[Card]

class ScenarioRequest(PortfolioRequest):
    purchases: list[Purchase]
    intent: Intent
```

Add model validators for collection-level limits and duplicate IDs where this produces earlier 422 feedback. The engine still validates independently because it is callable without HTTP.

`SolverPreference` is `greedy` or `ilp`; do not accept `optimal` as a requested method because optimality is a result status, not an algorithm.

## 7. Endpoints

### `GET /health`

Return:

- Process status.
- Schema version.
- Fixture readiness and scenario count.
- Greedy readiness.
- CBC availability.
- Intent provider name/model/readiness.
- Whether fallback is enabled.

Never include secrets or the provider endpoint query credentials. HTTP 200 means the app can serve at least manual/greedy demo behavior. Include component-level degraded states.

### `GET /demo-scenario`

Although not required by the original five endpoints, add this local-demo endpoint so the UI does not read JSON directly. Return Sarah cards, purchases, reference date, and named manual intent presets. It contains synthetic data only.

### `POST /parse-intent`

Request:

- `text`.
- `reference_date`.
- Minimal card contexts or a known scenario ID.
- Optional provider selector only from a server-configured allowlist; never arbitrary URL/model.
- Optional `allow_fallback`, defaulting to server policy. In production-like/eval mode the server may force false.

`data` contains `ParseIntentResult`. With allowed fallback, provider errors return HTTP 200 plus fallback intent/warnings. With fallback disabled, unavailable provider returns 503 and invalid model output returns typed HTTP 502. Eval calls providers directly and never routes through runtime fallback.

### `POST /recommend`

Request: cards, one purchase, intent.

Service flow:

1. Call engine recommendation.
2. Call `explain_recommendation` using exactly that result.
3. Return both result and explanation.

Domain `infeasible` returns HTTP 200. It is a valid answer.

### `POST /allocate`

Request: cards, purchases, intent, solver preference.

`data` contains allocation and explanation. For ILP timeout/error, engine may return `heuristic_fallback`; API does not relabel it. For heuristic `unresolved`, preserve that status and explanation.

### `POST /frontier`

Request: scenario, solver preference, maximum representatives.

Return frontier result and structured explanation. Include attempted grid count, selected goals, result statuses, and `complete_frontier=false`. If some sweep solves fail but enough successful plans remain, return the partial sampled result plus warnings. If none succeeds, return the domain failure under HTTP 200.

### `POST /what-if`

Request: base scenario, purchase ID, override card ID, solver preference.

Return base result, override result, deltas, changed assignments, and explanation. Validate IDs before solve. An infeasible override is a valid HTTP 200 result.

## 8. Orchestration and dependency injection

`dependencies.py` owns singleton immutable config and provider construction. Avoid global mutable scenario or HTTP-client state. An injected shared `httpx.AsyncClient` may be lifespan-managed for external provider efficiency.

`services.py` functions should look like:

```python
def allocate_service(request: AllocateRequest, config: EngineConfig) -> AllocateResponse:
    result = allocate_month(...)
    explanation = explain_allocation(result, ...)
    return AllocateResponse(result=result, explanation=explanation)
```

No service catches broad exceptions and turns them into an empty plan. Catch only typed provider, data, solver-infrastructure, and explanation-contract errors at the appropriate layer.

## 9. Error policy

| Condition | HTTP | Body behavior |
|---|---:|---|
| Malformed request/Pydantic validation | 422 | FastAPI detail |
| Domain infeasible/unresolved | 200 | Typed result/issues/explanation |
| Allowed parser fallback used | 200 | Intent plus warning/source |
| Required provider unavailable | 503 | Stable infrastructure error code |
| Upstream malformed model output, no fallback | 502 | Stable provider-output error |
| Corrupt committed synthetic fixture | 500/health degraded | Stable data-load error; no raw stack in response |
| Explanation contract mismatch | 500 | Internal contract error, logged |
| Unexpected error | 500 | Generic message, detailed local log |

Do not expose exception representations that may contain requests, environment data, or provider payloads.

## 10. Logging and privacy

- Log route, status, component, and duration at info level.
- Do not log full goal text, card objects, raw model output, or environment values by default.
- Debug logging may log synthetic IDs and issue codes.
- Provider adapter logs model/provider identity and error class, never authorization headers.
- Add no telemetry SDK during the hackathon.

All demo data is synthetic, but use patterns that would not leak real data if the prototype evolves.

## 11. CORS and serving

Streamlit calls the API server-side from Python, so browser CORS is not required for the chosen architecture. Do not enable wildcard CORS by default. Bind development services to `127.0.0.1` unless the team explicitly needs LAN demo access.

Run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Use a second port only if 8000 is occupied and pass it through `PAYMENT_ENGINE_API_URL`.

## 12. OpenAPI and schema stability

- Give every request/response an explicit model and operation ID.
- Add endpoint summaries, domain-status notes, and examples using synthetic data.
- Do not expose internal utility config as editable request data in v1.
- Snapshot `app.openapi()` after G4. Deliberate schema changes update snapshot and integration contracts.
- Keep all endpoint paths unprefixed for the hackathon contract; use `schema_version` in payloads.

## 13. Tests

Use `fastapi.testclient.TestClient` with fixture providers and small scenarios.

- App factory and health in ready/degraded modes.
- Demo scenario returns valid synthetic domain models.
- Every endpoint success shape and `schema_version`.
- Pydantic limits, duplicate IDs, unknown references.
- Domain infeasible/unresolved remains HTTP 200.
- Greedy/ILP status is preserved exactly.
- Parser valid, normalized, fallback, unavailable, malformed-output paths.
- Explanation values correspond to result values.
- API repeated deterministic requests deep-equal.
- OpenAPI generation and schema snapshot.
- Unexpected exception response does not leak a secret sentinel.

Mock provider network, but use the real engine for at least one recommend/allocate contract test.

## 14. Completion checklist

- Route handlers contain orchestration only.
- Request bounds protect solver/demo latency.
- Domain and infrastructure failures remain distinct.
- Health exposes degraded optional components.
- OpenAPI documents all result statuses.
- No secrets/raw model data are exposed or logged.
- Streamlit can obtain all needed data through HTTP.
