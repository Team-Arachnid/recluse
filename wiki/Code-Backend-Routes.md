# Code Reference — API Route Modules

This page documents every module under `backend/app/routes/`: the router aggregator, the shared 501 helper, and the six route modules that declare the full v1 API surface. Read it if you are calling the API, generating frontend types from its OpenAPI schema, or implementing one of the endpoints in a later phase. As of Phase 0 the entire surface below is registered and visible in `/docs`, and exactly one endpoint has a real implementation — `GET /api/v1/health`, which lives in `app/main.py` rather than here. Every route in this package answers HTTP 501 with a machine-readable body naming the phase that fills it in. For the modules those endpoints will drive, see [Alert Pipeline Modules](Code-Backend-Pipeline.md).

| File | Lines | Role |
| --- | --- | --- |
| `backend/app/routes/__init__.py` | 48 | The `not_implemented` helper and the aggregated `api_router` |
| `backend/app/routes/alerts.py` | 45 | Alert queue, detail, verdicts, host correlation |
| `backend/app/routes/score.py` | 25 | Batch flow scoring |
| `backend/app/routes/stream.py` | 26 | Server-sent-events live alert feed |
| `backend/app/routes/metrics.py` | 48 | Model metrics, threshold what-ifs, drift, model registry |
| `backend/app/routes/analytics.py` | 37 | Aggregate analytics and MITRE coverage |
| `backend/app/routes/replay.py` | 35 | Replay controls and live-capture ingest |

---

## How paths are assembled

No route module hardcodes `/api/v1`. Each module declares a bare router, `app/routes/__init__.py` includes all six into one `api_router`, and `create_app()` in `app/main.py` mounts that aggregate under the configured prefix:

```python
app.include_router(health_router, prefix=settings.api_v1_prefix)
app.include_router(api_router, prefix=settings.api_v1_prefix)
```

`settings.api_v1_prefix` defaults to `/api/v1` and is overridable via `IDS_API_V1_PREFIX` — see [Configuration](Configuration.md). So a decorator path of `""` on a router with `prefix="/alerts"` serves `/api/v1/alerts`, and `metrics.py`, which declares no router prefix at all, spells its paths out in full (`/metrics/model`, `/models`) and still lands under `/api/v1`. The tables below give the full served path for the default prefix.

---

## routes/__init__.py

**Path:** `backend/app/routes/__init__.py` — defines the shared 501 helper and assembles the six route modules into the single `api_router` that `main.py` mounts.

### What it does

Phase 0 registers the entire v1 surface from BUILD_PROMPT.md Part 8 rather than adding endpoints phase by phase. The reason is in the docstring: the OpenAPI schema — and therefore the TypeScript types the frontend generates from it with `npm run gen:types` — exists from the start. The frontend can be built against the real contract before the handlers behind it are written, and the two cannot silently drift.

The second half of the module is the `not_implemented` helper. It builds a `NotImplementedResponse` (declared in `app/schemas.py`) and returns it inside a `JSONResponse` with `status_code=501`. Every stub handler in this package is one line calling it.

```python
def not_implemented(endpoint: str, phase: str) -> JSONResponse:
    """Return a 501 naming the endpoint and the phase that implements it."""
    body = NotImplementedResponse(
        detail=f"Not implemented yet. Arrives in {phase}.",
        phase=phase,
        endpoint=endpoint,
    )
    return JSONResponse(status_code=501, content=body.model_dump())
```

The exact body shape is three string fields:

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "GET /alerts"
}
```

`detail` is the human sentence, `phase` is the same phase label as a separate machine-readable field, and `endpoint` is the specific endpoint that was called — including path parameters, because handlers like `get_alert` pass an interpolated string such as `GET /alerts/42`.

**Why a machine-readable 501 rather than a 404 or fake data.** A 404 says the route does not exist, which is false and unhelpful: a client cannot distinguish "you typed the path wrong" from "this ships in Phase 5", and neither can a developer reading a log. A 501 is the status code for a recognised request the server does not yet implement, which is precisely the situation. Returning plausible sample data would be worse still — a frontend built against invented alerts looks finished, demos convincingly, and hides the fact that nothing behind it works. With this body, a caller can tell "not built yet" from "built and broken", and can branch on `phase` programmatically instead of regex-matching an error string. No route in this codebase ever returns invented data.

There is one ordering subtlety in the file. The six route modules are imported *after* `not_implemented` is defined, with an explicit `# noqa: E402` and a comment explaining it: each route module does `from app.routes import not_implemented`, so the helper has to exist on the package before the submodules are loaded, or the import cycle fails.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `not_implemented` | function | `not_implemented(endpoint: str, phase: str) -> JSONResponse` | Builds the shared 501 response naming the endpoint and the phase that implements it. |
| `api_router` | constant | `api_router: APIRouter` | Aggregate router including `alerts`, `score`, `metrics`, `analytics`, `replay` and `stream`; mounted by `create_app()` under `settings.api_v1_prefix`. |
| `__all__` | constant | `__all__ = ["api_router", "not_implemented"]` | The package's public surface. |

### Notes

- The helper takes `phase` as a free-form string rather than an enum. Each route module holds its own `PHASE` constant, and `metrics.py` and `replay.py` pass the phase literal per handler because their endpoints land in different phases.
- `body.model_dump()` is passed to `JSONResponse` as a plain dict, so the response is serialised by FastAPI's JSON encoder rather than by the Pydantic model's own response machinery. That is what lets a 501 be returned from a handler with no declared success `response_model`.
- The router include order in `api_router` is `alerts`, `score`, `metrics`, `analytics`, `replay`, `stream`, which is the order the endpoints appear in the generated OpenAPI document.
- Status: **implemented.** The helper and the router assembly are real; the handlers they serve are not.

---

## routes/alerts.py

**Path:** `backend/app/routes/alerts.py` — the triage endpoints: the alert queue, one alert's full detail, the analyst verdict, and other alerts from the same host.

### What it does

This is the module the Triage Queue and Alert Detail screens are built against. `router = APIRouter(prefix="/alerts", tags=["alerts"])`, so every path here is relative to `/api/v1/alerts`.

The four endpoints trace an analyst's working loop: list the queue, open one alert, record a judgement on it, and check whether the same host has been noisy. `GET /alerts/{alert_id}` is the heaviest of them — its summary declares it returns explanation, narrative, remediation and the raw flow, which means it is the endpoint that consumes almost all of [the alert pipeline modules](Code-Backend-Pipeline.md) at read time.

The module defines two constants reused by every handler: `PHASE = "Phase 5 (backend API)"` and `STUB = {501: {"model": NotImplementedResponse}}`. `STUB` is passed as `responses=` on each decorator so the 501 body is documented in OpenAPI with its real schema, not as an undocumented error.

| Method | Path | Handler | Status today | Phase | Purpose |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/alerts` | `list_alerts` | 501 stub | Phase 5 (backend API) | Filter, sort and cursor-paginate the alert queue |
| GET | `/api/v1/alerts/{alert_id}` | `get_alert` | 501 stub | Phase 5 (backend API) | Alert detail: explanation, narrative, remediation, raw flow |
| POST | `/api/v1/alerts/{alert_id}/verdict` | `submit_verdict` | 501 stub | Phase 5 (backend API) | Record an analyst verdict (TP / FP / UNSURE) with an optional note |
| GET | `/api/v1/alerts/{alert_id}/related` | `related_alerts` | 501 stub | Phase 5 (backend API) | Other alerts from the same source host in a 24h window |

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `router` | constant | `router = APIRouter(prefix="/alerts", tags=["alerts"])` | Router for the alert endpoints. |
| `PHASE` | constant | `PHASE = "Phase 5 (backend API)"` | Phase label passed to `not_implemented` by every handler here. |
| `STUB` | constant | `STUB = {501: {"model": NotImplementedResponse}}` | OpenAPI `responses` fragment documenting the 501 body. |
| `list_alerts` | route handler | `@router.get("") def list_alerts()` | Queue listing. Returns `not_implemented("GET /alerts", PHASE)`. |
| `get_alert` | route handler | `@router.get("/{alert_id}") def get_alert(alert_id: int)` | Alert detail. Returns `not_implemented(f"GET /alerts/{alert_id}", PHASE)`. |
| `submit_verdict` | route handler | `@router.post("/{alert_id}/verdict") def submit_verdict(alert_id: int)` | Records a verdict. Returns `not_implemented(f"POST /alerts/{alert_id}/verdict", PHASE)`. |
| `related_alerts` | route handler | `@router.get("/{alert_id}/related") def related_alerts(alert_id: int)` | Same-host correlation. Returns `not_implemented(f"GET /alerts/{alert_id}/related", PHASE)`. |

### Notes

- `alert_id: int` is already typed, so FastAPI validates and coerces the path parameter today. A request to `/api/v1/alerts/abc` returns 422 from validation, not 501 — the stub is only reached once the path parses.
- The three handlers that take `alert_id` interpolate it into the `endpoint` field of the 501 body, so a log line names the exact resource that was asked for.
- The verdict vocabulary is `Verdict = Literal["TP", "FP", "UNSURE"]` in `app/schemas.py`; the request body model for it is defined in Phase 5. Verdicts are the label source that Phase 7 active learning consumes.
- The queue is cursor-paginated rather than offset-paginated because new alerts arrive continuously and offset paging would skip or repeat rows under a live feed.
- Status: **stub — all four handlers return 501, land in Phase 5 (backend API).**

---

## routes/score.py

**Path:** `backend/app/routes/score.py` — the batch scoring endpoint, the programmatic way into the model.

### What it does

One endpoint, one rule, stated in the module docstring: batch always. The endpoint accepts a list of flow records and scores them as a single matrix. Per-row `predict()` is roughly 50x slower and makes the replay demo stutter, which is why the API shape itself forbids single-row scoring rather than merely discouraging it — there is no `/score/one`.

This is also the endpoint that anything outside the project uses. Replay and live capture call the inference path directly in-process; an external tool posts here.

| Method | Path | Handler | Status today | Phase | Purpose |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/v1/score` | `score_flows` | 501 stub | Phase 5 (backend API) | Score a batch of flow records as one matrix |

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `router` | constant | `router = APIRouter(prefix="/score", tags=["scoring"])` | Router for the scoring endpoint. |
| `PHASE` | constant | `PHASE = "Phase 5 (backend API)"` | Phase label for the stub response. |
| `score_flows` | route handler | `@router.post("") def score_flows()` | Batch scoring. Returns `not_implemented("POST /score", PHASE)`. |

### Notes

- The decorator path is `""` against a router prefix of `/score`, so the served path is exactly `/api/v1/score` with no trailing slash.
- The request and response models are not declared yet. They depend on the Phase 1 feature contract, and declaring a `FlowRecord` before that contract is frozen would put a guess into the generated frontend types.
- Scoring never loads a model. The bundle is loaded once in the lifespan context in `app/main.py` and parked on `app.state.bundle`; no request handler trains, fits or reloads.
- Status: **stub — returns 501, lands in Phase 5 (backend API).**

---

## routes/stream.py

**Path:** `backend/app/routes/stream.py` — the server-sent-events feed that pushes alerts to every connected dashboard.

### What it does

This is step 7 of the alert pipeline, the push. The docstring argues the transport choice rather than assuming it: the feed is one-directional, `EventSource` is built into the browser, FastAPI does SSE in about ten lines, and there is no reconnect logic to write. WebSockets would add a bidirectional protocol, a handshake and a reconnect state machine for a feature that only ever sends server to client.

The Phase 5 checkpoint in BUILD_PROMPT.md Part 8 exercises this endpoint directly: start a replay at 10x, watch alerts arrive over `curl -N localhost:8000/api/v1/stream`, and confirm dedupe is collapsing bursts.

| Method | Path | Handler | Status today | Phase | Purpose |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/stream` | `stream_alerts` | 501 stub | Phase 5 (backend API) | Live alert feed over server-sent events |

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `router` | constant | `router = APIRouter(prefix="/stream", tags=["stream"])` | Router for the SSE feed. |
| `PHASE` | constant | `PHASE = "Phase 5 (backend API)"` | Phase label for the stub response. |
| `stream_alerts` | route handler | `@router.get("") def stream_alerts()` | The SSE feed. Returns `not_implemented("GET /stream", PHASE)`. |

### Notes

- The handler is currently a plain `def` returning a `JSONResponse`. The Phase 5 implementation returns a streaming response with content type `text/event-stream` and will be `async`.
- This is the one endpoint whose 501 is slightly awkward for clients: a browser `EventSource` treats a 501 as a connection error and retries. That is acceptable and visible, which is the point of not faking a feed.
- CORS matters here specifically, because the dashboard opens this connection from the Vite dev server origin. Allowed origins come from `IDS_CORS_ORIGINS`, default `http://localhost:5173,http://127.0.0.1:5173`.
- Status: **stub — returns 501, lands in Phase 5 (backend API).**

---

## routes/metrics.py

**Path:** `backend/app/routes/metrics.py` — model evaluation numbers, threshold what-ifs, drift snapshots and the model registry.

### What it does

This module backs the Model Performance and Drift screens. Its docstring states the headline-metric rule: PR-AUC is the headline. Accuracy may appear in a table but never as a headline number, because on 99% benign traffic a model that always answers benign scores 99% accurate and detects nothing.

Unlike the other modules, this router declares no prefix — `router = APIRouter(tags=["metrics"])` — so each decorator carries its full path. That is deliberate: the four endpoints do not share one path root. Three sit under `/metrics`, and `/models` is a sibling resource, exactly as listed in BUILD_PROMPT.md Part 8.

It is also the first module where the endpoints land in different phases, so `PHASE` is not a module constant here; each handler passes its own literal.

`threshold_what_if` is the one handler in the package with a validated query parameter today: `t: float = Query(..., ge=0.0, le=1.0)`. It is required, and a value outside `[0.0, 1.0]` is rejected with 422 before the stub is reached. It exists so an analyst can ask what the alert volume would be at a candidate threshold rather than discovering it by shipping one — the false-positive budget in `app/config.py` (`max_alerts_per_day`, `target_fpr`) is the constraint that question is asked against.

| Method | Path | Handler | Status today | Phase | Purpose |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/metrics/model` | `model_metrics` | 501 stub | Phase 5 (backend API) | Per-class metrics, PR and ROC curves, and the LOAO table |
| GET | `/api/v1/metrics/threshold?t=` | `threshold_what_if` | 501 stub | Phase 5 (backend API) | Recompute projected alert volume at a candidate threshold |
| GET | `/api/v1/metrics/drift` | `drift_metrics` | 501 stub | Phase 7 (drift and active learning) | PSI per feature over time |
| GET | `/api/v1/models` | `list_models` | 501 stub | Phase 7 (drift and active learning) | Model registry with champion and challenger versions |

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `router` | constant | `router = APIRouter(tags=["metrics"])` | Prefix-free router; each handler declares its full path. |
| `STUB` | constant | `STUB = {501: {"model": NotImplementedResponse}}` | OpenAPI `responses` fragment documenting the 501 body. |
| `model_metrics` | route handler | `@router.get("/metrics/model") def model_metrics()` | Returns `not_implemented("GET /metrics/model", "Phase 5 (backend API)")`. |
| `threshold_what_if` | route handler | `@router.get("/metrics/threshold") def threshold_what_if(t: float = Query(..., ge=0.0, le=1.0, description="Candidate threshold"))` | Returns `not_implemented(f"GET /metrics/threshold?t={t}", "Phase 5 (backend API)")`. |
| `drift_metrics` | route handler | `@router.get("/metrics/drift") def drift_metrics()` | Returns `not_implemented("GET /metrics/drift", "Phase 7 (drift and active learning)")`. |
| `list_models` | route handler | `@router.get("/models") def list_models()` | Returns `not_implemented("GET /models", "Phase 7 (drift and active learning)")`. |

### Notes

- LOAO is leave-one-attack-out, the Phase 4 evaluation that measures whether Stage 2 catches families it was never trained on. `GET /metrics/model` is where that table is served from; see [ML Models](ML-Models.md).
- `threshold_what_if` echoes the validated `t` back into the 501 `endpoint` field, so the stub response confirms the parameter was parsed as a float.
- `/models` returns a registry with champion and challenger versions, which is why it is Phase 7 work: there is nothing to compare until drift monitoring has motivated a retrain.
- Status: **stub — all four handlers return 501. Two land in Phase 5 (backend API), two in Phase 7 (drift and active learning).**

---

## routes/analytics.py

**Path:** `backend/app/routes/analytics.py` — aggregate views for whoever is not working the queue.

### What it does

The module docstring names its audience and its rule in two sentences: this screen is for people not working the queue, and there is no accuracy hero tile here either. If the screen needs one big number, it is alerts per analyst hour or the unclassified-anomaly rate — both operational measures, both meaningful on imbalanced data, unlike accuracy.

`router = APIRouter(prefix="/analytics", tags=["analytics"])`. The summary endpoint takes a validated range: `range: str = Query("24h", pattern="^(24h|7d|30d|all)$")`. The pattern is a closed set, so an unsupported window is rejected with 422 rather than silently falling back to a default — a chart labelled "last 24h" that is actually showing something else is worse than an error.

`mitre-coverage` serves technique counts for the coverage heatmap, drawn from the same static lookup described in [Alert Pipeline Modules](Code-Backend-Pipeline.md).

| Method | Path | Handler | Status today | Phase | Purpose |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/analytics/summary?range=` | `analytics_summary` | 501 stub | Phase 5 (backend API) | Alerts over time, family mix, top hosts and sources, SOC throughput |
| GET | `/api/v1/analytics/mitre-coverage` | `mitre_coverage` | 501 stub | Phase 5 (backend API) | Technique counts for the coverage heatmap |

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `router` | constant | `router = APIRouter(prefix="/analytics", tags=["analytics"])` | Router for the analytics endpoints. |
| `PHASE` | constant | `PHASE = "Phase 5 (backend API)"` | Phase label passed by both handlers. |
| `STUB` | constant | `STUB = {501: {"model": NotImplementedResponse}}` | OpenAPI `responses` fragment documenting the 501 body. |
| `analytics_summary` | route handler | `@router.get("/summary") def analytics_summary(range: str = Query("24h", pattern="^(24h\|7d\|30d\|all)$", description="Time range"))` | Returns `not_implemented(f"GET /analytics/summary?range={range}", PHASE)`. |
| `mitre_coverage` | route handler | `@router.get("/mitre-coverage") def mitre_coverage()` | Returns `not_implemented("GET /analytics/mitre-coverage", PHASE)`. |

### Notes

- `range` shadows the Python builtin of the same name inside the handler. It is kept because the query parameter name is part of the agreed API contract in BUILD_PROMPT.md Part 8, and the builtin is not used in that scope.
- The default is `"24h"`, so `GET /api/v1/analytics/summary` with no query string is valid.
- The coverage heatmap will always have an uncovered region, because `UNCLASSIFIED_ANOMALY` maps to no technique. That gap is information, not a rendering bug.
- Status: **stub — both handlers return 501, land in Phase 5 (backend API).**

---

## routes/replay.py

**Path:** `backend/app/routes/replay.py` — traffic source controls: start and stop a dataset replay, and start a live capture.

### What it does

The docstring describes the module as traffic source controls, and live capture in Phase 9. That grouping is the point — replay and live ingest are two sources feeding one identical scoring path, so they share a control surface rather than each growing their own.

Like `metrics.py`, this router declares no prefix (`router = APIRouter(tags=["traffic"])`) because `/replay/*` and `/ingest/*` are different path roots, and its handlers land in different phases: the two replay controls in Phase 5, the ingest control in Phase 9.

`POST /replay/start` takes a speed of 1, 10 or 100 and a dataset. `POST /ingest/start` begins scoring a live capture, and its summary carries the constraint inline — authorised networks only. See [live_capture.py](Code-Backend-Pipeline.md) for what that precondition means in practice.

| Method | Path | Handler | Status today | Phase | Purpose |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/v1/replay/start` | `replay_start` | 501 stub | Phase 5 (backend API) | Start replaying held-out test flows at 1x, 10x or 100x |
| POST | `/api/v1/replay/stop` | `replay_stop` | 501 stub | Phase 5 (backend API) | Stop the active replay |
| POST | `/api/v1/ingest/start` | `ingest_start` | 501 stub | Phase 9 (real traffic) | Begin scoring a live capture (authorised networks only) |

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `router` | constant | `router = APIRouter(tags=["traffic"])` | Prefix-free router; each handler declares its full path. |
| `STUB` | constant | `STUB = {501: {"model": NotImplementedResponse}}` | OpenAPI `responses` fragment documenting the 501 body. |
| `replay_start` | route handler | `@router.post("/replay/start") def replay_start()` | Returns `not_implemented("POST /replay/start", "Phase 5 (backend API)")`. |
| `replay_stop` | route handler | `@router.post("/replay/stop") def replay_stop()` | Returns `not_implemented("POST /replay/stop", "Phase 5 (backend API)")`. |
| `ingest_start` | route handler | `@router.post("/ingest/start") def ingest_start()` | Returns `not_implemented("POST /ingest/start", "Phase 9 (real traffic)")`. |

### Notes

- There is no `POST /ingest/stop` in Phase 0, mirroring the missing `stop_ingest` in `app/live_capture.py`.
- The request body models for `replay/start` — speed and dataset — are not declared yet; they arrive with the Phase 5 implementation.
- All three handlers are `POST` because they change server state, even `replay/stop`, which takes no payload.
- Nothing on this router blocks or shapes traffic. Both sources are read-only observation, and `IDS_ALLOW_AUTO_BLOCK` is rejected at startup by a validator in `app/config.py`.
- Status: **stub — all three handlers return 501. Two land in Phase 5 (backend API), one in Phase 9 (real traffic).**

---

## The full v1 surface at a glance

| Method | Path | Module | Status today | Phase |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | `app/main.py` | implemented | Phase 0 |
| POST | `/api/v1/score` | `routes/score.py` | 501 | Phase 5 |
| GET | `/api/v1/alerts` | `routes/alerts.py` | 501 | Phase 5 |
| GET | `/api/v1/alerts/{alert_id}` | `routes/alerts.py` | 501 | Phase 5 |
| POST | `/api/v1/alerts/{alert_id}/verdict` | `routes/alerts.py` | 501 | Phase 5 |
| GET | `/api/v1/alerts/{alert_id}/related` | `routes/alerts.py` | 501 | Phase 5 |
| GET | `/api/v1/stream` | `routes/stream.py` | 501 | Phase 5 |
| GET | `/api/v1/metrics/model` | `routes/metrics.py` | 501 | Phase 5 |
| GET | `/api/v1/metrics/threshold` | `routes/metrics.py` | 501 | Phase 5 |
| GET | `/api/v1/metrics/drift` | `routes/metrics.py` | 501 | Phase 7 |
| GET | `/api/v1/analytics/summary` | `routes/analytics.py` | 501 | Phase 5 |
| GET | `/api/v1/analytics/mitre-coverage` | `routes/analytics.py` | 501 | Phase 5 |
| POST | `/api/v1/replay/start` | `routes/replay.py` | 501 | Phase 5 |
| POST | `/api/v1/replay/stop` | `routes/replay.py` | 501 | Phase 5 |
| POST | `/api/v1/ingest/start` | `routes/replay.py` | 501 | Phase 9 |
| GET | `/api/v1/models` | `routes/metrics.py` | 501 | Phase 7 |

`GET /api/v1/health` is the only implemented endpoint and is defined on `health_router` in `app/main.py`, not in this package. It returns `HealthResponse` with exactly three fields — `status`, `model_version`, `uptime_s` — and reports `model_version: "unloaded"` until Phase 2 writes a real artifact bundle.

---

## Related pages

- [API Reference](API-Reference.md) — the endpoints described for callers rather than maintainers
- [Alert Pipeline Modules](Code-Backend-Pipeline.md) — explain, mitre, remediation, dedupe, drift, replay, live capture
- [Backend Core](Code-Backend-Core.md) — `main.py`, `config.py`, `schemas.py`, `inference.py`, `db.py`, `models.py`
- [Configuration](Configuration.md) — `IDS_API_V1_PREFIX`, `IDS_CORS_ORIGINS` and the rest
- [Frontend Screens](Frontend-Screens.md) — which screen consumes which endpoint
- [Roadmap](Roadmap.md) — what each phase delivers
