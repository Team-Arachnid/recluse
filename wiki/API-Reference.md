# API Reference

Every HTTP endpoint the Recluse backend exposes, what it returns today, and what it will return once the phase that owns it is built. Written for anyone calling the API directly — frontend work, scripts, `curl` during a demo — and for reviewers checking that the documented surface matches the code. Facts here are taken from `backend/app/main.py`, `backend/app/routes/`, `backend/app/schemas.py` and `backend/app/config.py` as they stand at Phase 0.

---

## Conventions

| Item | Value | Source |
| --- | --- | --- |
| Base URL (local dev) | `http://127.0.0.1:8000` | `IDS_HOST`, `IDS_PORT` |
| Version prefix | `/api/v1` | `Settings.api_v1_prefix`, env var `IDS_API_V1_PREFIX` |
| Request content type | `application/json` | FastAPI default |
| Response content type | `application/json` | FastAPI default |
| Interactive docs | `GET /docs` | `docs_url="/docs"` in `create_app()` |
| ReDoc | `GET /redoc` | FastAPI default; `create_app()` does not pass `redoc_url=None` |
| Machine schema | `GET /openapi.json` | `openapi_url="/openapi.json"` in `create_app()` |
| API title / version | `Recluse API` / `0.1.0` | `f"{settings.app_name} API"`, `app.__version__` |
| API description | `API_DESCRIPTION` | `backend/app/main.py`; the two-stage summary plus "The system alerts, ranks and explains. It never blocks traffic." |

The prefix is not hardcoded anywhere. `create_app()` mounts both routers with `prefix=settings.api_v1_prefix`, so changing `IDS_API_V1_PREFIX` moves the whole surface. `/docs`, `/redoc` and `/openapi.json` sit at the application root and are *not* prefixed — the schema URL is `/openapi.json`, not `/api/v1/openapi.json`. The frontend type generator (`frontend/scripts/generate-types.mjs`) reads exactly that URL. The prefix is total, not decorative: `GET /health` without it returns 404, which `backend/tests/test_health.py::test_health_is_mounted_under_the_configured_prefix` asserts.

One caveat when moving the prefix: the browser does not read `IDS_API_V1_PREFIX`. `frontend/src/lib/env.ts` prefixes every request with `VITE_API_BASE_URL`, which defaults to `/api/v1` and is relative on purpose so the Vite dev proxy and a production reverse proxy both work without a rebuild. The two values must be changed together, and both live in the repo-root `.env` that the backend and Vite share (`frontend/vite.config.ts` sets `envDir` to the repo root).

CORS is enabled only when `IDS_CORS_ORIGINS` is non-empty. Allowed methods are `GET`, `POST`, `PATCH`, `DELETE`, `OPTIONS`; credentials are allowed; all headers are allowed. In development the Vite dev server proxies `/api` to the backend, so the browser is same-origin and never needs CORS at all.

### Access control

**There is none today.** No endpoint requires authentication, no route declares a dependency — `grep -rn 'Depends(' backend/app` returns nothing — and there is no rate limiting. The only access control in the application is the CORS middleware described above, and that is a browser-origin restriction rather than an authorisation boundary: anything that can reach the port can call every endpoint.

Nothing in Phases 0 through 9 schedules an auth layer, so deploying this beyond a local machine or a trusted lab network means putting one in front of it. Said plainly here because a SOC-facing API that serves alert data is exactly the kind of surface a reader assumes is gated.

### The 501 contract

Every endpoint a later phase implements is **registered now** and answers `501 Not Implemented` with a machine-readable body. This is deliberate: a caller can tell "not built yet" from "built and broken", the OpenAPI schema is complete from day one, and no route ever returns invented data.

The body is `NotImplementedResponse` from `backend/app/schemas.py`, built by `not_implemented()` in `backend/app/routes/__init__.py`:

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "GET /alerts"
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `detail` | string | Human-readable, always `Not implemented yet. Arrives in <phase>.` |
| `phase` | string | The build phase that implements this endpoint |
| `endpoint` | string | The endpoint as the route reports itself, path parameters resolved |

`not_implemented(endpoint: str, phase: str) -> JSONResponse` builds a `NotImplementedResponse`, dumps it with `.model_dump()` and returns `JSONResponse(status_code=501, content=...)`. Because it returns a response object directly rather than going through a `response_model`, `NotImplementedResponse` documents the schema but never validates the body at runtime.

The `endpoint` value is the concrete call, not the template: requesting `/api/v1/alerts/42` returns `"endpoint": "GET /alerts/42"`. Endpoints with query parameters echo them, for example `"endpoint": "GET /metrics/threshold?t=0.87"`. Note that the echo is the *parsed* value, not the raw query string — `metrics.py` interpolates a `float`, so `?t=1` comes back as `"GET /metrics/threshold?t=1.0"`.

The client layer already handles this, although no screen exercises it yet: the dashboard's only query today is `useHealth()` in `frontend/src/api/queries.ts`, and `/health` never answers 501. `ApiError.isNotImplemented` in `frontend/src/api/client.ts` returns true for status 501. `createQueryClient()` in `frontend/src/api/queryClient.ts` declines to retry it — its `retry` predicate returns `false` for any `ApiError` that is `isNotImplemented`, `false` for any `ApiError` with `status < 500`, and otherwise retries while `failureCount < 2`. Retrying a route that does not exist yet only delays the message the UI wants to show.

### Wire vocabularies (`backend/app/schemas.py`)

The wire contracts live in one file, and its module docstring states the rule that makes it worth reading: these models are the source of truth for the frontend's TypeScript types, which are generated from this app's OpenAPI schema (`npm run gen:types`) rather than hand-written. A vocabulary change therefore reaches the frontend through a regeneration, never by editing `frontend/src/types/api.d.ts` by hand.

Eight `Literal` type aliases, kept in step with the `CheckConstraint`s in `backend/app/models.py`:

| Alias | Members |
| --- | --- |
| `AlertKind` | `"KNOWN"`, `"UNCLASSIFIED_ANOMALY"` |
| `AlertFamily` | `"dos"`, `"ddos"`, `"brute_force"`, `"port_scan"`, `"web_attack"`, `"botnet"`, `"infiltration"` |
| `Severity` | `"low"`, `"medium"`, `"high"`, `"critical"` |
| `AlertStatus` | `"open"`, `"in_review"`, `"closed"`, `"dismissed"` |
| `Verdict` | `"TP"`, `"FP"`, `"UNSURE"` |
| `DetectionStage` | `"stage1_supervised"`, `"stage2_anomaly"` |
| `AlertSource` | `"replay"`, `"live"`, `"api"` |
| `HealthStatus` | `"ok"`, `"degraded"` |

The file contains exactly **two** Pydantic models today:

- `HealthResponse` — `status: HealthStatus`, `model_version: str`, `uptime_s: float` with `ge=0`. Each field carries a `Field(description=...)`, and `model_version` and `uptime_s` carry `examples` (`["unloaded", "rf-20260921-1"]` and `[12.482]`), which is what `/docs` renders. It also sets `model_config = ConfigDict(protected_namespaces=())`: Pydantic v2 reserves the `model_` prefix, and `model_version` is part of the agreed health contract, so the namespace guard is lifted on that model rather than the field being renamed.
- `NotImplementedResponse` — `detail: str`, `phase: str` (described as the build phase that implements the endpoint), `endpoint: str`.

There is no request model of any kind yet, because no route parses a body. The aliases exist ahead of the models they will type so the vocabulary is fixed in one place before six phases start using it.

### Route modules (`backend/app/routes/`)

`backend/app/routes/__init__.py` holds the package docstring explaining the 501 policy, the `not_implemented()` helper, and the assembly of `api_router`. Two details are easy to trip over:

- It exports `__all__ = ["api_router", "not_implemented"]`, and it imports the six route modules **below** the helper definition with a `# noqa: E402`, commented "Imported after the helper so the route modules can use it without a cycle". Moving those imports to the top of the file breaks the package.
- `api_router` is a bare, prefixless `APIRouter()`. Every path segment above `/api/v1` comes either from a sub-router's own `prefix` or from the path string on the decorator, and the sub-routers are included in a fixed order (alerts, score, metrics, analytics, replay, stream).

Each route module is a thin file of stubs. `alerts.py`, `analytics.py`, `score.py` and `stream.py` each define a module-level `PHASE` constant; `alerts.py`, `analytics.py`, `metrics.py` and `replay.py` each define `STUB = {501: {"model": NotImplementedResponse}}`. `metrics.py` and `replay.py` pass their phase string inline instead of via a constant, because their routes belong to different phases — drift and the registry are Phase 7, ingest is Phase 9.

Every route also carries a `summary`, which is the one-line title `/docs` shows, and FastAPI derives an operation ID from the handler name and path — `list_alerts_api_v1_alerts_get`, `get_alert_api_v1_alerts__alert_id__get`, and so on. Those operation IDs are what name the entries under `operations` in `frontend/src/types/api.d.ts`, so they are the names a frontend reader actually imports.

### Application setup (`backend/app/main.py`)

The application factory and the one implemented endpoint live in the same small module.

| Symbol | Signature | What it does |
| --- | --- | --- |
| `API_DESCRIPTION` | `str` | The Markdown description rendered at the top of `/docs`: the two-stage summary, and the sentence "The system alerts, ranks and explains. It never blocks traffic." This is where the never-blocks rule is stated to every API reader. |
| `_configure_logging()` | `-> None` | `logging.basicConfig` at `settings.log_level` with a fixed format. Private; called from the lifespan. |
| `lifespan(app)` | `-> AsyncIterator[None]` | The async context manager passed to `FastAPI(lifespan=...)`. |
| `health_router` | `APIRouter(tags=["system"])` | Carries `GET /health` alone, mounted separately from `api_router`. |
| `health(request)` | `-> HealthResponse` | The handler. Reads `request.app.state.bundle` and `request.app.state.started_at`. |
| `create_app()` | `-> FastAPI` | Builds the app: title, `API_DESCRIPTION`, `version=__version__` (`0.1.0`, from `backend/app/__init__.py`), the lifespan, `docs_url`, `openapi_url`, optional CORS, then both routers at `settings.api_v1_prefix`. |
| `app` | `FastAPI` | Module-level `app = create_app()`. This is the target uvicorn is pointed at (`uvicorn app.main:app`). |

The lifespan runs, in order: `_configure_logging()`, `settings.ensure_directories()`, `app.state.started_at = time.monotonic()`, `load_bundle(settings.artifacts_path)` parked on `app.state.bundle`, then two log lines — one naming the app, env, database backend and model version, one printing the false-positive budget. A `SchemaHashMismatch` raised by `load_bundle()` is intentionally fatal and takes the process down at boot; the comment in the source says so.

Because `create_app()` is a function rather than a module-level expression only, the tests can build a fresh app per test (`test_api_surface.py` does exactly that) while uvicorn uses the single module-level instance.

---

## Endpoint index

All sixteen endpoints from BUILD_PROMPT.md Part 8 are registered in the running app. **Nothing in Part 8 is specified-but-unrouted.** Exactly one endpoint is implemented.

That claim is machine-checked. `backend/tests/test_api_surface.py` holds `EXPECTED_ROUTES`, the same sixteen `(method, path)` pairs as the table below, and runs five tests over them:

- `test_route_is_documented` is parametrised over the sixteen pairs and asserts each appears in `create_app().openapi()` — read from the schema rather than from `app.routes`, because the schema is what the frontend generates types from, and a route missing there is a route the client cannot see.
- `test_unimplemented_routes_answer_501_with_a_phase` calls all fifteen non-health routes (substituting `1` for `{alert_id}` and passing `t=0.87` to the threshold route) and asserts a 501 whose `phase` starts with `"Phase "` and whose `endpoint` is non-empty.
- `test_openapi_schema_is_served` fetches `/openapi.json` over the client and asserts `openapi` starts with `3.` and `info.title == "Recluse API"`.
- `test_no_route_mentions_blocking` fetches the same schema and asserts no path contains `block`, `drop` or `quarantine`. See [Explicitly absent](#explicitly-absent).
- `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` pickles a bundle whose `schema_hash` does not match `["a", "b"]` and asserts `load_bundle()` raises `SchemaHashMismatch`.

The `client` fixture in `backend/tests/conftest.py` is session-scoped and enters the `TestClient` context manager, which is what makes the lifespan — artifact loading and the schema-hash check — actually run during the suite. The `api_prefix` fixture returns `settings.api_v1_prefix`, so the tests never hardcode `/api/v1` either.

| Method | Path | Purpose | Status today | Phase |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | Liveness, loaded model version, uptime | **Implemented** | 0 |
| POST | `/api/v1/score` | Score a batch of flow records | Registered, 501 | 5 |
| GET | `/api/v1/alerts` | Filter, sort, cursor-paginate the queue | Registered, 501 | 5 |
| GET | `/api/v1/alerts/{alert_id}` | Detail: explanation, narrative, remediation, raw flow | Registered, 501 | 5 |
| POST | `/api/v1/alerts/{alert_id}/verdict` | Record TP / FP / UNSURE with a note | Registered, 501 | 5 |
| GET | `/api/v1/alerts/{alert_id}/related` | Same source host, 24h window | Registered, 501 | 5 |
| GET | `/api/v1/stream` | Live alert feed over SSE | Registered, 501 | 5 |
| GET | `/api/v1/metrics/model` | Per-class metrics, PR/ROC curves, LOAO table | Registered, 501 | 5 |
| GET | `/api/v1/metrics/threshold` | Projected alert volume at a candidate threshold | Registered, 501 | 5 |
| GET | `/api/v1/metrics/drift` | PSI per feature over time | Registered, 501 | 7 |
| GET | `/api/v1/analytics/summary` | Alerts over time, family mix, top hosts, throughput | Registered, 501 | 5 |
| GET | `/api/v1/analytics/mitre-coverage` | Technique counts for the coverage heatmap | Registered, 501 | 5 |
| POST | `/api/v1/replay/start` | Start a dataset replay at 1x / 10x / 100x | Registered, 501 | 5 |
| POST | `/api/v1/replay/stop` | Stop the active replay | Registered, 501 | 5 |
| POST | `/api/v1/ingest/start` | Begin scoring a live capture | Registered, 501 | 9 |
| GET | `/api/v1/models` | Registry, champion and challenger | Registered, 501 | 7 |

The phase strings above are the literal values the routes return. `GET /metrics/drift` and `GET /models` report `"Phase 7 (drift and active learning)"`; `POST /ingest/start` reports `"Phase 9 (real traffic)"`; everything else reports `"Phase 5 (backend API)"`.

One wrinkle in the schema is worth knowing before you generate a client from it: **every stub also advertises a `200` with an unconstrained body.** The stub decorators declare `responses={501: {"model": NotImplementedResponse}}` and no `response_model`, so FastAPI emits its default success entry alongside it — `frontend/src/types/api.d.ts` renders that as `"application/json": unknown`. No stub returns 200 today. The declared codes per operation are: 200 + 501 for the parameterless stubs; 200 + 422 + 501 for the five that parse a parameter (`GET /alerts/{alert_id}`, `POST /alerts/{alert_id}/verdict`, `GET /alerts/{alert_id}/related`, `GET /metrics/threshold`, `GET /analytics/summary`); and 200 alone for `GET /health`, which is the only route with a real `response_model`.

Router registration, with the tags that group the endpoints in `/docs` and travel in the served schema:

```
create_app()
  ├─ health_router   prefix=/api/v1   tags=["system"]     →  GET /health
  └─ api_router      prefix=/api/v1   (bare APIRouter, no prefix of its own)
       ├─ alerts.router      prefix=/alerts     tags=["alerts"]     (4 routes)
       ├─ score.router       prefix=/score      tags=["scoring"]    (1 route)
       ├─ metrics.router     no prefix          tags=["metrics"]    (/metrics/*, /models)
       ├─ analytics.router   prefix=/analytics  tags=["analytics"]  (2 routes)
       ├─ replay.router      no prefix          tags=["traffic"]    (/replay/*, /ingest/start — 3 routes)
       └─ stream.router      prefix=/stream     tags=["stream"]     (1 route)
```

The seven tags in the schema are `alerts`, `analytics`, `metrics`, `scoring`, `stream`, `system` and `traffic`. `/replay/start`, `/replay/stop` and `/ingest/start` share the `traffic` tag because they are one concept — where flows come from — even though replay lands in Phase 5 and ingest in Phase 9. The prefix column cannot express that grouping, which is why the tag exists.

---

## GET /api/v1/health

The only implemented endpoint, and the Phase 0 checkpoint. It reports what is actually loaded rather than a hardcoded string.

**Status: shipped.** The handler is `health(request: Request) -> HealthResponse`, defined on `health_router` in `backend/app/main.py`, response model `HealthResponse` from `backend/app/schemas.py`. Operation ID `health_api_v1_health_get`.

Path parameters: none. Query parameters: none. Request body: none.

### Response schema

| Field | Type | Constraint | Meaning |
| --- | --- | --- | --- |
| `status` | `"ok"` \| `"degraded"` | enum | `ok` once the process is serving. `degraded` is reserved for a bundle that was found but could not be made usable — the branch exists in `ModelBundle.status` but nothing sets `_degraded` today, so at Phase 0 this field is always `ok`. Phase 2 is the first phase with an artifact bundle that could be degraded. |
| `model_version` | string | — | Version of the loaded model bundle, or `"unloaded"`. |
| `uptime_s` | number | `>= 0` | Seconds since the FastAPI lifespan started, rounded to 3 decimals. |

`status` and `model_version` both come from the `ModelBundle` parked on `app.state.bundle` during the lifespan. `uptime_s` is `time.monotonic()` measured against `app.state.started_at`, which is set in the same lifespan. The `degraded` value is specified rather than reachable: `ModelBundle.status` returns it only `if self._degraded`, and `_degraded: bool = False` is the only other occurrence of that flag in the repository — no code path sets it. Treat a hypothetical `degraded` as a contract the schema already carries, not as a state this build can report.

### Why `model_version` is `"unloaded"`

At Phase 0 there is no `backend/artifacts/preprocessing.pkl`, so `ModelBundle.load()` logs that fact and returns an empty bundle whose `version` is the module constant `UNLOADED_VERSION = "unloaded"`. That is not an error state: `status` stays `"ok"`, because the API is expected to serve health and the dashboard shell before any model exists. The first real version string arrives when Phase 2 writes `model_card.json` with a `version` field.

A *present but inconsistent* bundle is a different matter. `_verify_schema_hash()` raises `SchemaHashMismatch` during the lifespan, which takes the process down at boot. Train/serve skew produces no exception on its own, so the check is made loud on purpose.

### Status codes

| Code | When |
| --- | --- |
| 200 | Always, once the process has started |
| 422 | Not reachable — the endpoint takes no input |

The process either starts and answers 200, or fails to start at all.

### Example

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

```json
{
  "status": "ok",
  "model_version": "unloaded",
  "uptime_s": 12.482
}
```

Polling it twice and watching `uptime_s` advance is the cheapest proof the number is coming from the live process rather than a cache. The dashboard's health panel polls it every `VITE_HEALTH_POLL_MS` milliseconds (default 5000) for exactly that reason.

---

## POST /api/v1/score

Score a batch of flow records through the two-stage pipeline.

### Today

Returns 501. `score_flows()` in `backend/app/routes/score.py` takes no body and immediately calls `not_implemented("POST /score", "Phase 5 (backend API)")`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/score \
  -H 'Content-Type: application/json' -d '{}'
```

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "POST /score"
}
```

The serving-side counterpart, `ModelBundle.score_batch()` in `backend/app/inference.py`, raises `NotImplementedError` with the same convention: fusion arrives in Phase 4, Stage 1 in Phase 2, Stage 2 in Phase 3.

### Planned

Request body: a JSON array of flow records (`List[FlowRecord]`), scored as a single matrix. Each record carries the flow features `training/features.py` expects; the exact field list is fixed by the persisted `feature_order` and is not stable until Phase 1 runs.

Planned response, one result per input row in input order:

| Field | Type | Meaning |
| --- | --- | --- |
| `kind` | `"KNOWN"` \| `"UNCLASSIFIED_ANOMALY"` \| `null` | Fusion outcome; `null` for rows that raise no alert |
| `family` | attack family \| `null` | Stage 1 label; always `null` for `UNCLASSIFIED_ANOMALY` |
| `confidence` | number \| `null` | Stage 1 max attack-class probability |
| `anomaly_score` | number \| `null` | Stage 2 mean reconstruction error |
| `detection_stage` | `"stage1_supervised"` \| `"stage2_anomaly"` | Which stage fired |
| `model_version` | string | The bundle version that produced the score |

The `kind`, `family`, `detection_stage` and family vocabularies already exist as literals in `backend/app/schemas.py` and as `CheckConstraint`s in `backend/app/models.py`; see [Database Schema](Database-Schema).

Planned status codes: 200 on success, 422 for a malformed record, 503 while no model bundle is loaded.

---

## GET /api/v1/alerts

The triage queue: filtered, sorted, cursor-paginated.

### Today

501, phase `Phase 5 (backend API)`, endpoint `GET /alerts`. The handler accepts no parameters yet.

```bash
curl -s http://127.0.0.1:8000/api/v1/alerts
```

### Planned

| Parameter | In | Type | Meaning |
| --- | --- | --- | --- |
| `severity` | query | `low` \| `medium` \| `high` \| `critical` | Severity filter |
| `kind` | query | `KNOWN` \| `UNCLASSIFIED_ANOMALY` | Backs the one-click anomaly filter chip |
| `family` | query | attack family | Family filter |
| `status` | query | `open` \| `in_review` \| `closed` \| `dismissed` | Triage state |
| `since` / `until` | query | ISO 8601 timestamp | Time window |
| `cursor` | query | opaque string | Pagination cursor |
| `limit` | query | integer | Page size |

Sorted by `risk_score`, not timestamp — analysts work by risk. The supporting index `ix_alerts_status_risk_score` already exists in the initial migration, as does `ix_alerts_kind_detected_at` for the anomaly filter.

Planned response: a page object carrying the alert rows plus a next-cursor. Status codes 200 and 422.

---

## GET /api/v1/alerts/{alert_id}

Full detail for one alert: why it fired, what it likely is, how to fix it, and the raw flow.

### Today

501. Path parameter `alert_id` is typed `int` and is parsed before the stub answers, so a non-integer id returns 422 rather than 501.

```bash
curl -s http://127.0.0.1:8000/api/v1/alerts/1
```

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "GET /alerts/1"
}
```

### Planned

| Parameter | In | Type | Meaning |
| --- | --- | --- | --- |
| `alert_id` | path | integer | Primary key of the alert |

Response assembled from the `alerts` row: `explanation` (top-5 TreeSHAP contributors for Stage 1, or top-5 per-feature reconstruction errors for Stage 2), `narrative` (the templated English sentence), `mitre_technique` plus its plain-English description, `recommended_actions` (the static playbook), `raw_flow`, and `ground_truth_label` when the alert came from a replay. All of those columns exist today; nothing writes them yet.

Status codes: 200, 404 for an unknown id, 422 for a non-integer id.

See [Dashboard Screens](Frontend-Screens) for the why / what-it-is / how-to-fix ordering this response has to support, including the honest no-playbook case.

---

## POST /api/v1/alerts/{alert_id}/verdict

Record an analyst's judgement. This is the input to active learning.

### Today

501, endpoint `POST /alerts/1/verdict`. No body is read.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/alerts/1/verdict \
  -H 'Content-Type: application/json' \
  -d '{"verdict": "TP", "note": "confirmed scan from lab host"}'
```

### Planned

Request body:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `verdict` | `"TP"` \| `"FP"` \| `"UNSURE"` | yes | The judgement |
| `note` | string \| `null` | no | Free text |
| `analyst` | string \| `null` | no | Who judged it |

Writes one `analyst_verdicts` row, capturing `model_version` at verdict time so the label is auditable against the model that produced the alert. The `ck_analyst_verdicts_verdict_valid` check constraint already enforces the three allowed values at the database level.

Planned status codes: 201 on write, 404 for an unknown alert, 422 for an invalid verdict.

On the frontend this mutation invalidates the alerts query so the queue refreshes itself.

---

## GET /api/v1/alerts/{alert_id}/related

Other alerts from the same source host in a 24-hour window, so a scan-then-exploit sequence reads as one story instead of three disconnected rows.

### Today

501, endpoint `GET /alerts/1/related`.

```bash
curl -s http://127.0.0.1:8000/api/v1/alerts/1/related
```

### Planned

| Parameter | In | Type | Meaning |
| --- | --- | --- | --- |
| `alert_id` | path | integer | The anchor alert |
| `window_hours` | query | integer | Lookback, default 24 |

Backed by `ix_alerts_src_ip_detected_at`, which the initial migration creates for exactly this query. Response is a list of alert summaries. Status codes 200, 404.

---

## GET /api/v1/stream

Live alert feed over server-sent events. See [Streaming](#streaming) below for the protocol detail.

### Today

501, endpoint `GET /stream`. The stub is an ordinary JSON route — it does not open a stream, so `curl -N` returns immediately.

```bash
curl -N -s http://127.0.0.1:8000/api/v1/stream
```

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "GET /stream"
}
```

### Planned

Response content type `text/event-stream`, connection held open, one event per persisted alert. Status codes 200 and 503 (no active traffic source).

---

## GET /api/v1/metrics/model

Per-class metrics, the PR and ROC curves, and the leave-one-attack-out table.

### Today

501, endpoint `GET /metrics/model`.

```bash
curl -s http://127.0.0.1:8000/api/v1/metrics/model
```

### Planned

Planned response sections:

| Section | Contents |
| --- | --- |
| `per_class` | precision, recall, F1, support per family |
| `confusion_matrix` | labels plus counts |
| `curves.pr` / `curves.roc` | point series for both curves, rendered side by side |
| `pr_auc` | the headline metric |
| `fpr_at_threshold` | measured FPR at the operating `tau_sup` |
| `alerts_per_analyst_hour` | projected volume |
| `loao` | per held-out family: Stage 1 recall, Stage 2 recall, total, missed |
| `accuracy` | may appear in the table; never a headline |

**No numbers exist yet.** Phase 2 produces the classification report and `tau_sup`, Phase 3 the anomaly thresholds, Phase 4 the LOAO table. Until then this endpoint returns 501 rather than a plausible-looking placeholder.

---

## GET /api/v1/metrics/threshold

Recompute projected alert volume at a candidate threshold. This is what makes the draggable threshold line on the Live Traffic Monitor a real calculation rather than an animation.

### Today

501 — but the query parameter is already declared and validated. `t` is required, a float, and constrained to `[0.0, 1.0]`.

```bash
curl -s 'http://127.0.0.1:8000/api/v1/metrics/threshold?t=0.87'
```

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "GET /metrics/threshold?t=0.87"
}
```

Omitting `t`, or passing `t=1.5`, returns 422 from FastAPI's validation layer before the stub runs.

| Parameter | In | Type | Constraint | Required | Meaning |
| --- | --- | --- | --- | --- | --- |
| `t` | query | float | `>= 0.0`, `<= 1.0` | yes | Candidate threshold |

### Planned

Response: projected alerts per hour and per day at `t`, the implied false-positive rate, and the recall that rate costs. The false-positive budget it is judged against is configuration, not a constant — `max_alerts_per_day = IDS_ANALYST_CAPACITY_PER_HOUR * IDS_ANALYST_SHIFT_HOURS`, and `target_fpr = max_alerts_per_day / IDS_EXPECTED_DAILY_FLOW_VOLUME`. Both are computed properties on `Settings` and are logged at startup. With the shipped defaults (40 alerts/hour, 8 hours, 1,000,000 flows/day) that is 320 alerts/day and a target FPR of 3.2e-4.

---

## GET /api/v1/metrics/drift

Population Stability Index per feature over time.

### Today

501, and note the phase differs: `"Phase 7 (drift and active learning)"`.

```bash
curl -s http://127.0.0.1:8000/api/v1/metrics/drift
```

### Planned

PSI per feature per snapshot, against the training reference distribution, with the warning bands at 0.1 (moderate) and 0.25 (significant). `population_stability_index()` in `backend/app/drift.py` currently raises `NotImplementedError` naming Phase 7. There is no drift snapshot table in the schema yet; see [Database Schema](Database-Schema).

---

## GET /api/v1/analytics/summary

Aggregates for whoever is not working the queue: a team lead, a reviewer, or the presenter during a demo.

### Today

501, with the range already validated. `range` defaults to `24h` and is constrained by the regex `^(24h|7d|30d|all)$`.

```bash
curl -s 'http://127.0.0.1:8000/api/v1/analytics/summary?range=7d'
```

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "GET /analytics/summary?range=7d"
}
```

| Parameter | In | Type | Allowed | Default | Meaning |
| --- | --- | --- | --- | --- | --- |
| `range` | query | string | `24h`, `7d`, `30d`, `all` | `24h` | Time range |

An unrecognised range returns 422.

### Planned

Alerts over time stacked by known family versus `UNCLASSIFIED_ANOMALY`, the family breakdown for the range, top targeted hosts and ports, top source addresses, and SOC throughput (opened vs resolved, average time-to-verdict, TP/FP rate over time). No accuracy tile.

---

## GET /api/v1/analytics/mitre-coverage

Technique counts for the coverage heatmap.

### Today

501, endpoint `GET /analytics/mitre-coverage`.

```bash
curl -s http://127.0.0.1:8000/api/v1/analytics/mitre-coverage
```

### Planned

One row per technique that has fired, with a count. Sourced from the `mitre_technique` column on `alerts` and the static lookup in `backend/app/mitre.py`, whose `technique_for()` currently raises `NotImplementedError` naming Phase 5. `UNCLASSIFIED_ANOMALY` maps to no technique by design, and the heatmap has to show that absence rather than hide it.

---

## POST /api/v1/replay/start

Start streaming held-out test rows at accelerated time.

### Today

501, endpoint `POST /replay/start`. No body is read.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/replay/start \
  -H 'Content-Type: application/json' -d '{"speed": 10, "dataset": "friday"}'
```

### Planned

| Field | Type | Allowed | Meaning |
| --- | --- | --- | --- |
| `speed` | integer | `1`, `10`, `100` | Time acceleration |
| `dataset` | string | a held-out split name | Which rows to replay |

Runs as an asyncio background task scoring in batches and pushing over SSE. Rows carry real ground-truth labels, which is what lets the dashboard show predictions against truth and badge them as demo-only. Planned status codes: 202 on start, 409 if a replay is already running.

---

## POST /api/v1/replay/stop

### Today

501, endpoint `POST /replay/stop`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/replay/stop
```

### Planned

No body. 202 on stop, 409 if nothing is running.

---

## POST /api/v1/ingest/start

Begin scoring a live capture. Phase 9, and the phase string on the stub says so.

### Today

501, phase `"Phase 9 (real traffic)"`, endpoint `POST /ingest/start`.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ingest/start
```

### Planned

Accepts an interface or capture source and feeds the **same** `features.py`, the same `inference.py` and the same alert pipeline as replay. If live traffic needed its own scoring code the train/serve-skew defence would already be broken.

Two operational constraints belong in the request path, not just the documentation: capture is only ever run against a network or host the operator owns or is explicitly authorised to monitor, and a shadow-mode burn-in with a locally recomputed `tau_anom` comes before any live alert reaches the queue.

---

## GET /api/v1/models

The model registry.

### Today

501, phase `"Phase 7 (drift and active learning)"`.

```bash
curl -s http://127.0.0.1:8000/api/v1/models
```

### Planned

One entry per row of `model_versions`: `version`, `stage` (`champion` / `challenger` / `archived`), `supervised_algorithm`, `anomaly_algorithm`, `trained_at`, `trained_on`, `schema_hash`, `tau_sup`, `tau_anom`, `metrics`, `is_active`. The table exists today and is empty. Columns are documented in [Database Schema](Database-Schema).

---

## Streaming

`GET /api/v1/stream` is the live alert feed.

### Why SSE and not WebSockets

The feed is one-directional: the server pushes alerts, the browser never pushes back. `EventSource` is built into the browser, handles reconnection itself, and needs no client library. FastAPI serves SSE in roughly ten lines by returning a `StreamingResponse` over an async generator with content type `text/event-stream`. A WebSocket would add a bidirectional protocol, a handshake, a keepalive scheme and reconnect logic in exchange for a channel back to the server that nothing in the design uses.

Verdicts, replay control and filtering are all ordinary HTTP calls on other endpoints, so the second direction is already covered.

### Planned event shape

```
event: alert
data: {"id":1,"kind":"UNCLASSIFIED_ANOMALY","family":null,"severity":"high",
       "risk_score":0.94,"anomaly_score":0.212,"detected_at":"2026-01-01T00:00:00Z",
       "src_ip":"192.168.10.5","dst_ip":"192.168.10.50","occurrence_count":1,
       "model_version":"rf-20260921-1"}

event: heartbeat
data: {"ts":"2026-01-01T00:00:15Z"}
```

Alerts reach the stream *after* dedupe, not before. One compromised host emitting 5,000 flows is one incident and therefore one event with a rising `occurrence_count` — without that, the queue is unusable within thirty seconds of starting a replay. The dedupe key is already implemented: `dedupe_key(src_host, alert_class, timestamp)` in `backend/app/dedupe.py` floors the timestamp into a bucket of `IDS_DEDUPE_WINDOW_SECONDS` seconds (default 300) and returns `"<src_host>|<alert_class>|<bucket>"`.

### Verifying the stream

```bash
curl -N http://127.0.0.1:8000/api/v1/stream
```

`-N` disables curl's output buffering; without it the events sit in a buffer and the stream looks dead. Running this alongside a 10x replay, and watching burst traffic collapse into single events with incrementing counts, is the Phase 5 checkpoint.

---

## Scoring

Scoring is **batch always**. `POST /api/v1/score` takes a list of flow records and `ModelBundle.score_batch()` scores them as a single matrix.

This is a performance contract, not a style preference. Calling `predict()` once per row inside the replay loop is roughly 50x slower than one batched call: every call pays Python-level dispatch, per-call validation, and array allocation overhead that vectorised scoring pays once for the whole matrix. At 100x replay speed that difference is the gap between a feed that scrolls and a feed that stutters, which is visible to everyone watching a live demo.

The same rule applies everywhere flows are scored. Replay batches. Live capture batches. The single-flow case is a batch of one, not a separate code path.

Two related invariants:

- **Models load once, at startup.** `load_bundle(settings.artifacts_path)` is called in the FastAPI lifespan and the result is parked on `app.state.bundle`. No request handler loads, reloads, fits or trains anything.
- **The schema hash is checked before serving.** A bundle whose `schema_hash` disagrees with the recomputed hash of its `feature_order` raises `SchemaHashMismatch` during startup and the process does not come up. Feeding columns in the wrong order produces garbage scores and raises nothing, so the failure is made loud and early instead.

---

## Explicitly absent

**There is no block, drop, quarantine or deny endpoint anywhere in this API, and there will not be one.**

This is enforced rather than merely intended:

- `backend/tests/test_api_surface.py::test_no_route_mentions_blocking` walks every path in the served OpenAPI schema and asserts that none contains `block`, `drop` or `quarantine`.
- `IDS_ALLOW_AUTO_BLOCK` exists in `Settings` so the constraint is greppable rather than silently absent. Its field validator **raises** if the value is true, so the process refuses to start rather than ignoring the flag.

The reason is arithmetic. At the configured expected volume of 1,000,000 flows a day, a 0.1% false-positive rate is a thousand false alerts. Automatic containment at that rate takes production down. The system alerts, ranks and explains; a human decides what to do.

If containment were ever added, it would have to be:

| Property | Requirement |
| --- | --- |
| Manual | Triggered by an analyst action, never by a score crossing a threshold |
| Confirmed | A second explicit confirmation step naming the exact host and action |
| Scoped | A named target and a stated duration, never an open-ended rule |
| Audited | A persisted record of who acted, on which alert, under which model version, with the reason |
| Reversible | An undo path that is as easy to reach as the action itself |

Nothing in Phases 0 through 9 schedules such an endpoint. See [Anti-Patterns](Anti-Patterns) for why the auto-block button is listed there as a failure, not a feature.
