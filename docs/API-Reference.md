# API Reference

Every HTTP endpoint the Recluse backend exposes, what it returns, and -- for the three a later phase still owns -- what it will return once that phase is built. Facts here are taken from `backend/app/main.py`, `backend/app/routes/`, `backend/app/schemas.py`, `backend/app/config.py` and the Phase 5 modules behind them (`app/pipeline.py`, `app/replay.py`, `app/events.py`, `app/metrics_store.py`) as they stand at the end of **Phase 5**.

**Thirteen of the sixteen endpoints answer with real data.** The three that do not are `GET /metrics/drift` and `GET /models` (Phase 7) and `POST /ingest/start` (Phase 9); they still answer 501 with the phase that fills them in. `reports/phase5_api.md` is the measured checkpoint for the live path.

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

**There is none today.** No endpoint requires authentication and there is no rate limiting. (Phase 5 introduced `Depends(get_session)` on the database-backed routes, so the earlier `grep -rn 'Depends(' backend/app` shorthand for "no dependencies" no longer holds — but those are session dependencies, not authorisation ones.) The only access control in the application is the CORS middleware described above, and that is a browser-origin restriction rather than an authorisation boundary: anything that can reach the port can call every endpoint.

Nothing in Phases 0 through 9 schedules an auth layer, so deploying this beyond a local machine or a trusted lab network means putting one in front of it. Said plainly here because a SOC-facing API that serves alert data is exactly the kind of surface a reader assumes is gated.

### The 501 contract

Every endpoint a later phase implements is **registered now** and answers `501 Not Implemented` with a machine-readable body. This is deliberate: a caller can tell "not built yet" from "built and broken", the OpenAPI schema is complete from day one, and no route ever returns invented data.

As of Phase 5 this applies to exactly three endpoints -- `GET /metrics/drift`, `GET /models` and `POST /ingest/start`. The contract below is unchanged for them, and `backend/tests/test_api_surface.py` asserts both halves of the split: `DEFERRED_ROUTES` must answer 501 with its exact phase string, and `IMPLEMENTED_ROUTES` must not answer 501 at all. A test also asserts the two lists partition the sixteen documented pairs with no overlap, because the failure mode of splitting them is a route that quietly leaves both lists and stops being checked by either.

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

All sixteen v1 endpoints are registered in the running app. **Nothing specified is unrouted.** Thirteen are implemented; the remaining three belong to Phase 7 and Phase 9 and answer 501.

That claim is machine-checked. `backend/tests/test_api_surface.py` holds `EXPECTED_ROUTES`, the same sixteen `(method, path)` pairs as the table below, and runs five tests over them:

- `test_route_is_documented` is parametrised over the sixteen pairs and asserts each appears in `create_app().openapi()` — read from the schema rather than from `app.routes`, because the schema is what the frontend generates types from, and a route missing there is a route the client cannot see.
- `test_unimplemented_routes_answer_501_with_a_phase` calls the three still-deferred routes and asserts a 501 whose `phase` equals the **exact** string that route reports -- a stronger claim than the earlier `startswith("Phase ")`, which would have passed on a route reporting the wrong phase.
- `test_implemented_routes_do_not_answer_501` calls the thirteen implemented routes and asserts the status is anything but 501. This is the half of the split that can rot silently: moving a route out of the deferred list removes it from the 501 check, so without this a route regressing to a stub would be caught by nothing.
- `test_deferred_and_implemented_routes_partition_the_documented_surface` asserts the two lists are disjoint and together cover all sixteen pairs, so a route cannot be dropped from one without being added to the other.
- `test_openapi_schema_is_served` fetches `/openapi.json` over the client and asserts `openapi` starts with `3.` and `info.title == "Recluse API"`.
- `test_no_route_mentions_blocking` fetches the same schema and asserts no path contains `block`, `drop` or `quarantine`. See [Explicitly absent](#explicitly-absent).
- `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` pickles a bundle whose `schema_hash` does not match `["a", "b"]` and asserts `load_bundle()` raises `SchemaHashMismatch`.

The `client` fixture in `backend/tests/conftest.py` is session-scoped and enters the `TestClient` context manager, which is what makes the lifespan — artifact loading and the schema-hash check — actually run during the suite. The `api_prefix` fixture returns `settings.api_v1_prefix`, so the tests never hardcode `/api/v1` either.

| Method | Path | Purpose | Status today | Phase |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | Liveness, loaded model version, uptime | **Implemented** | 0 |
| POST | `/api/v1/score` | Score a batch of flow records | **Implemented** | 5 |
| GET | `/api/v1/alerts` | Filter, sort, cursor-paginate the queue | **Implemented** | 5 |
| GET | `/api/v1/alerts/{alert_id}` | Detail: explanation, narrative, remediation, raw flow | **Implemented** | 5 |
| POST | `/api/v1/alerts/{alert_id}/verdict` | Record TP / FP / UNSURE with a note | **Implemented** | 5 |
| GET | `/api/v1/alerts/{alert_id}/related` | Same source host, 24h window | **Implemented** | 5 |
| GET | `/api/v1/stream` | Live alert feed over SSE | **Implemented** | 5 |
| GET | `/api/v1/metrics/model` | Per-class metrics, PR/ROC curves, LOAO table | **Implemented** | 5 |
| GET | `/api/v1/metrics/threshold` | Projected alert volume at a candidate threshold | **Implemented** | 5 |
| GET | `/api/v1/metrics/drift` | PSI per feature over time | Registered, 501 | 7 |
| GET | `/api/v1/analytics/summary` | Alerts over time, family mix, top hosts, throughput | **Implemented** | 5 |
| GET | `/api/v1/analytics/mitre-coverage` | Technique counts for the coverage heatmap | **Implemented** | 5 |
| POST | `/api/v1/replay/start` | Start a dataset replay at 1x / 10x / 100x | **Implemented** | 5 |
| POST | `/api/v1/replay/stop` | Stop the active replay | **Implemented** | 5 |
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

The first endpoint implemented, and the Phase 0 checkpoint. It reports what is actually loaded rather than a hardcoded string.

**Status: shipped.** The handler is `health(request: Request) -> HealthResponse`, defined on `health_router` in `backend/app/main.py`, response model `HealthResponse` from `backend/app/schemas.py`. Operation ID `health_api_v1_health_get`.

Path parameters: none. Query parameters: none. Request body: none.

### Response schema

| Field | Type | Constraint | Meaning |
| --- | --- | --- | --- |
| `status` | `"ok"` \| `"degraded"` | enum | `ok` once the process is serving. `degraded` is reserved for a bundle that was found but could not be made usable — the branch exists in `ModelBundle.status` but nothing sets `_degraded`, so this field is always `ok` in practice. An inconsistent bundle does not produce `degraded`: it raises `SchemaHashMismatch` at startup and the process does not come up, which is the louder and more useful failure. |
| `model_version` | string | — | Version of the loaded model bundle, or `"unloaded"`. |
| `uptime_s` | number | `>= 0` | Seconds since the FastAPI lifespan started, rounded to 3 decimals. |

`status` and `model_version` both come from the `ModelBundle` parked on `app.state.bundle` during the lifespan. `uptime_s` is `time.monotonic()` measured against `app.state.started_at`, which is set in the same lifespan. The `degraded` value is specified rather than reachable: `ModelBundle.status` returns it only `if self._degraded`, and `_degraded: bool = False` is the only other occurrence of that flag in the repository — no code path sets it. Treat a hypothetical `degraded` as a contract the schema already carries, not as a state this build can report.

### When `model_version` is `"unloaded"`

With the shipped artifacts present this reports `stage1-lgbm-202609281410`. The `"unloaded"` case below is what a fresh clone sees before the training pipeline has run.

On a clean clone there is no `backend/artifacts/preprocessing.pkl` — artifacts are gitignored reproducible output — so `ModelBundle.load()` logs that fact and returns an empty bundle whose `version` is the module constant `UNLOADED_VERSION = "unloaded"`. That is not an error state: `status` stays `"ok"`, because the API is expected to serve health and the dashboard shell before any model exists.

After `make data && make train && make train-lgbm` the field carries the promoted champion's version from `model_card.json` — `stage1-lgbm-202609281410` on the run these docs report. The endpoint reads it from the bundle rather than holding a constant, and `backend/tests/test_health.py` asserts exactly that, so the test passes in both states.

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
  "model_version": "stage1-lgbm-202609281410",
  "uptime_s": 12.482
}
```

Polling it twice and watching `uptime_s` advance is the cheapest proof the number is coming from the live process rather than a cache. The dashboard's health panel polls it every `VITE_HEALTH_POLL_MS` milliseconds (default 5000) for exactly that reason.

---

## POST /api/v1/score

Score a batch of flow records through the two-stage pipeline. **Stateless: it scores and returns, and persists no alert.**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/score   -H 'Content-Type: application/json'   -d '[{"destination_port": 445, "flow_duration": 120, "total_fwd_packets": 2}]'
```

```json
{
  "results": [
    {
      "kind": "UNCLASSIFIED_ANOMALY",
      "family": null,
      "confidence": 0.0121,
      "anomaly_score": 0.1534,
      "detection_stage": "stage2_anomaly",
      "model_version": "stage1-lgbm-202609281410"
    }
  ],
  "scored": 1,
  "alerts": 1
}
```

### Request

A JSON **array** of flow records. Each record is an open mapping of feature name to number — the field list belongs to the artifact bundle's persisted `feature_order`, not to this code, which is why `FlowRecord` is a `RootModel[dict[str, FeatureValue]]` rather than a fixed-field model.

**Strings are refused.** `FeatureValue` accepts `int`, `float` and `bool` and nothing else. The reason is in `ModelBundle._verify_payload`: `build_feature_matrix` fills absent columns with `0.0` by design, but the same fill runs after `select_dtypes` has dropped every non-numeric column — so a feature sent as `"7.0"` instead of `7.0` silently becomes zero and the model scores a different flow than the one that arrived. The schema catches it as a 422 naming the field; the inference layer catches it again if anything reaches it.

### Response

One result per input row, **in input order**, including rows that raised no alert.

| Field | Type | Meaning |
| --- | --- | --- |
| `kind` | `"KNOWN"` \| `"UNCLASSIFIED_ANOMALY"` \| `null` | Fusion outcome; `null` for rows that raise no alert |
| `family` | attack family \| `null` | Stage 1 label; always `null` for `UNCLASSIFIED_ANOMALY` |
| `confidence` | number \| `null` | Stage 1 max attack-class probability |
| `anomaly_score` | number \| `null` | Stage 2 mean squared reconstruction error; `null` where Stage 2 was not consulted |
| `detection_stage` | `"stage1_supervised"` \| `"stage2_anomaly"` \| `null` | Which stage fired; `null` for a clear row |
| `model_version` | string | The bundle version that produced the score |

Plus `scored` (input rows) and `alerts` (rows whose `kind` is non-null). Both are returned so a caller can state the denominator of every rate without re-counting a list client-side.

**`detection_stage` is nullable**, which the earlier version of this table got wrong. `training/fusion.py` sets `kind`, `family` and `stage` all to `None` for a row that raised no alert, and the response is one result per input row including those rows — a non-nullable stage would make the response unserialisable for exactly the rows that are the denominator.

### Status codes

| Code | When |
| --- | --- |
| 200 | Scored. An empty array is a valid request returning `scored: 0`. |
| 422 | A record carries a non-numeric feature, or no recognised feature column at all. |
| 503 | No complete model stage is loaded, so there is nothing to score with. |

### Why it persists nothing

The documented response carries no `id`, and an `alerts` row needs a source address — `alerts.src_ip` is `NOT NULL` — which an arbitrary API caller does not supply. The dedupe/enrich/persist/push pipeline runs where a flow's origin is known: the replay path now (`app/replay.py` → `app/pipeline.py`), live capture in Phase 9. `ALERT_SOURCES` carries an `"api"` value that nothing writes yet for that reason.

---

## GET /api/v1/alerts

The triage queue: filtered, sorted by risk, keyset-paginated.

```bash
curl -s 'http://127.0.0.1:8000/api/v1/alerts?limit=2&severity=critical'
```

### Parameters

| Parameter | In | Type | Meaning |
| --- | --- | --- | --- |
| `severity` | query | `low` \| `medium` \| `high` \| `critical` | Severity filter |
| `kind` | query | `KNOWN` \| `UNCLASSIFIED_ANOMALY` | Backs the one-click anomaly filter chip |
| `family` | query | attack family | Family filter |
| `status` | query | `open` \| `in_review` \| `closed` \| `dismissed` | Triage state |
| `since` / `until` | query | ISO 8601 timestamp | Window on `detected_at` |
| `cursor` | query | opaque string | Pagination cursor from the previous page |
| `limit` | query | integer, 1–200, default 50 | Page size |

Every filter is optional and they compose. An unrecognised value is a 422 from the validation layer, because the vocabularies are `Literal`s.

### Response

`items` (a list of alert summaries), `next_cursor` and `limit`. **There is no `total`**: counting the whole filtered set on every page is the query that makes a triage queue slow, and nothing on the Triage Queue screen asks for one.

Each summary carries the columns [Dashboard Screens](Frontend-Screens.md) fixes for the queue, plus `latest_verdict` — a *derived* field, since `analyst_verdicts` is one-to-many. It is defined once, in `AlertSummary`, as the most recent verdict by `created_at`, so every endpoint returning a summary agrees what "the" verdict of an alert is.

### Ordering and pagination

**Sorted by `risk_score` descending, never by timestamp.** `ix_alerts_status_risk_score` serves it. Sorting a triage queue chronologically ranks alerts by when a packet happened rather than by what needs attention first.

**Pagination is keyset, not offset**, and the cursor encodes `(risk_score, id)`. A replay writes to this table while an analyst pages through it, and an offset page silently skips and repeats rows: one new higher-risk alert arriving between two requests shifts everything down by one, so page 2 re-shows the last row of page 1 and drops a row nobody ever saw. `backend/tests/test_alerts.py` asserts exactly that scenario.

The `id` tie breaker is load-bearing rather than cosmetic. `risk_score` is rounded to four places, so ties are common; without a second key the database may order tied rows differently on each request, and a cursor on `risk_score` alone would page straight past some of them.

A malformed cursor is **422 with a message**, not a silent reset to the top. Treating it as "start from the beginning" would make a paging bug look like a queue that keeps jumping back.

### Status codes

200, or 422 for an unrecognised filter value or a malformed cursor.

---

## GET /api/v1/alerts/{alert_id}

Full detail for one alert: why it fired, what it likely is, how to fix it, and the raw flow.

```bash
curl -s http://127.0.0.1:8000/api/v1/alerts/1
```

### Parameters

| Parameter | In | Type | Meaning |
| --- | --- | --- | --- |
| `alert_id` | path | integer | Primary key of the alert |

### Response

Everything `AlertSummary` carries, plus:

| Field | Meaning |
| --- | --- |
| `explanation` | Top-5 TreeSHAP contributors (Stage 1) or top-5 per-feature reconstruction errors (Stage 2), with an `explainer` discriminator |
| `narrative` | The templated English sentence from `app/explain.py::narrate` |
| `recommended_actions` | The static playbook plus its technique, from `app/remediation.py::advice_for` |
| `raw_flow` | The flow as it arrived, carrying the `_provenance` key |
| `ground_truth_label` | Replay only; always `null` for live capture, badged demo-only on the frontend |
| `host_prior_alert_count` | Other alerts from this source host |

One request rather than three, because the drawer answers why / what-it-is / how-to-fix in a fixed order and assembling that from several calls would let the panels disagree.

For an `UNCLASSIFIED_ANOMALY`, `family` and `mitre_technique` are both `null` and `recommended_actions.has_playbook` is `false` with a *populated* honest entry — see [the no-playbook case](Frontend-Screens.md#the-honest-no-playbook-case). An empty panel would read as a bug rather than as an admission.

### Status codes

200, 404 for an unknown id, 422 for a non-integer id.

---

## POST /api/v1/alerts/{alert_id}/verdict

Record an analyst's judgement. This is the input to active learning.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/alerts/1/verdict \
  -H 'Content-Type: application/json' \
  -d '{"verdict": "TP", "note": "confirmed scan from lab host"}'
```

### Request

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `verdict` | `"TP"` \| `"FP"` \| `"UNSURE"` | yes | The judgement |
| `note` | string \| `null` | no | Free text |
| `analyst` | string \| `null` | no | Who judged it |

### Response

**201** with the created row: `id`, `alert_id`, `verdict`, `note`, `analyst`, `model_version`, `created_at`.

`model_version` is captured **from the alert being judged**, not from whatever is currently loaded. A label attributed to the wrong model version is worse than no label: it would train the next model on a correction to a decision it never made. `ck_analyst_verdicts_verdict_valid` enforces the three allowed values at the database level as well.

**The alert's `status` is deliberately not changed.** Recording a judgement and moving an alert through triage are different actions, the spec gives this endpoint only the verdict, and a verdict that silently closed an alert would take it out of a colleague's queue mid-review.

### Status codes

201 on write, 404 for an unknown alert, 422 for an invalid verdict.

On the frontend this mutation invalidates the alerts query so the queue refreshes itself.

---

## GET /api/v1/alerts/{alert_id}/related

Other alerts from the same source host, so a scan-then-exploit sequence reads as one story instead of three disconnected rows.

```bash
curl -s 'http://127.0.0.1:8000/api/v1/alerts/1/related?window_hours=24'
```

### Parameters

| Parameter | In | Type | Meaning |
| --- | --- | --- | --- |
| `alert_id` | path | integer | The anchor alert |
| `window_hours` | query | integer, 1–720, default 24 | Lookback |

### Response

A list of alert summaries, same shape as the queue's rows, newest first. The anchor itself is excluded.

**The window is measured from the anchor's own `detected_at`, not from now, and reaches both directions.** An alert opened a week after the fact would otherwise show no context at all, and the reconnaissance that *preceded* an exploit attempt is usually the context worth most. Backed by `ix_alerts_src_ip_detected_at`, which the initial migration creates for exactly this query.

### Status codes

200, 404 for an unknown anchor id.

---

## GET /api/v1/stream

Live alert feed over server-sent events. See [Streaming](#streaming) below for the protocol detail.

```bash
curl -N http://127.0.0.1:8000/api/v1/stream
```

```
event: alert
data: {"id": 4, "kind": "UNCLASSIFIED_ANOMALY", "family": null, "severity": "high",
       "risk_score": 0.695, "anomaly_score": 0.1534,
       "detected_at": "2026-10-06T05:25:02.190827Z", "src_ip": "172.16.0.1",
       "dst_ip": "192.168.10.14", "occurrence_count": 27,
       "model_version": "stage1-lgbm-202609281410"}
```

Content type `text/event-stream`, connection held open, one `alert` event per persisted alert and a `heartbeat` every 15 seconds of silence. `-N` disables curl's output buffering; without it the events sit in a buffer and the stream looks dead.

### Status codes

| Code | When |
| --- | --- |
| 200 | A traffic source is running; the stream is open. |
| 503 | No traffic source is running. |

**The 503 is deliberate rather than a convenience.** An empty stream and a dead stream are indistinguishable from the client's side, and the first is a bug while the second is Tuesday — so a stream with nothing feeding it refuses rather than holding open a connection that can never produce an event. `app.state.broker.active_source` is what the route consults, and `POST /replay/start` is what sets it.

### Implementation notes

`app/events.py` holds one bounded `asyncio.Queue` per connection and **drops rather than blocks** when a queue is full: at 100x a backgrounded browser tab must not be able to stall the scoring loop, so the newest event is lost for that subscriber and counted in `dropped_events`. A silent drop would be a lie about what the ticker showed; a counted one is telemetry.

`publish()` is safe to call from another thread, and that is the normal case rather than the exotic one — `app/replay.py` scores each batch through `asyncio.to_thread` because scoring and explaining are CPU-bound and would otherwise stall this stream. `asyncio.Queue` is explicitly not thread-safe, and a bare cross-thread `put_nowait` can enqueue an event without ever waking the coroutine awaiting `get()`, which looks exactly like a feed that silently stopped. The broker therefore hands the enqueue to the loop that owns the queues via `call_soon_threadsafe`.

Heartbeats are emitted per connection on a read timeout, not by the broker: "quiet" is a property of one reader, and the broker has no per-connection timer to attach one to.

---

## GET /api/v1/metrics/model

Per-class metrics, the PR and ROC curves, and the leave-one-attack-out table.

```bash
curl -s http://127.0.0.1:8000/api/v1/metrics/model
```

### Response sections

| Section | Contents |
| --- | --- |
| `per_class` | precision, recall, F1, support per family |
| `labels` | row/column order of the confusion matrix |
| `confusion_matrix` | counts |
| `curves.pr` / `curves.roc` | point series for both curves, rendered side by side |
| `pr_auc` | the headline metric |
| `roc_auc`, `accuracy` | reported; **never** a headline |
| `tau_sup` | the Stage 1 operating threshold |
| `fpr_at_threshold` | measured FPR at `tau_sup` |
| `alerts_per_analyst_hour` | projected volume at `tau_sup` |
| `budget` | the false-positive budget the thresholds were cut against |
| `stage1_family_recall` / `stage2_family_recall` | per-family recall, each stage alone |
| `stage2_pr_auc` | Stage 2's PR-AUC |
| `loao` | the whole leave-one-attack-out table: per held-out family, what each stage caught |

### This endpoint serves; it does not compute

Every number comes from `metrics_supervised.json`, `metrics_anomaly.json` and `metrics_loao.json` — the artifacts `backend/training/` wrote next to the model it evaluated. **Nothing is recomputed from the database.** A figure recomputed from whatever alerts happen to be stored would be a different claim wearing the same label, and it would move every time a replay ran.

The three files load **once at startup**, beside the bundle, into `app.state.metrics` (`app/metrics_store.py`). Per-request reads would eventually let this endpoint answer from a newer file than the model that is actually serving — a card describing a champion replaced an hour ago. The cost is that a retrain needs a restart before new numbers appear, which is already true of the model itself.

Absence is not an error anywhere else in this codebase and it is not here either: a missing file leaves its section empty. But if *no* artifact is loaded the endpoint answers **503**, because an empty table would read as a model that scored zero rather than one that has not been measured.

### On `accuracy`

It is served rather than hidden. Hiding it invites the question, and the honest answer is that on 99% benign traffic a detector that always answers benign scores 99% — so PR-AUC leads and accuracy sits in the table with a caption. See [the PR-vs-ROC caption requirement](Frontend-Screens.md#the-pr-vs-roc-caption-requirement).

### Status codes

200, or 503 when no evaluation artifact is loaded.

---

## GET /api/v1/metrics/threshold

Recompute projected alert volume at a candidate threshold. This is what makes the draggable threshold line on the Live Traffic Monitor a real calculation rather than an animation.

```bash
curl -s 'http://127.0.0.1:8000/api/v1/metrics/threshold?t=0.2'
```

### Parameters

| Parameter | In | Type | Constraint | Required | Meaning |
| --- | --- | --- | --- | --- | --- |
| `t` | query | float | `>= 0.0`, `<= 1.0` | yes | Candidate threshold |

Omitting `t`, or passing `t=1.5`, returns 422 from FastAPI's validation layer.

### `t` is Stage 2's anomaly threshold

Not a Stage 1 probability. [Dashboard Screens](Frontend-Screens.md#the-draggable-threshold) draws this slider across the anomaly-score histogram and names the arithmetic it does — `ErrorHistogram.above(tau)` over the persisted benign error distribution. A Stage 1 answer is **not computable** at an arbitrary `t` from what is persisted, because the PR and ROC curves are `(recall, precision)` and `(fpr, tpr)` pairs with no threshold column to look `t` up in.

### Response

| Field | Meaning |
| --- | --- |
| `fpr` | benign rows at or above `t`, over all benign rows |
| `recall` | attack rows at or above `t`; `null` without that split loaded |
| `benign_rows` / `benign_above`, `attack_rows` / `attack_above` | the raw counts both figures come from |
| `false_alerts_per_day` | `fpr` × `IDS_EXPECTED_DAILY_FLOW_VOLUME` |
| `alerts_per_analyst_hour` | per **analyst** hour: divided by the shift length, not by 24 |
| `budget_per_day`, `target_fpr`, `within_budget` | the budget this is judged against |
| `covers_distribution` | `false` when `t` sits above the histogram's range |

Both figures are read off histograms that **share their bin edges**, so the false-positive rate and the recall describe the same point on the same axis rather than coming from two independently binned distributions whose boundaries do not line up.

The result is approximate by construction — a bin straddling `t` contributes all of itself — which is exactly the arithmetic a slider does when it projects a count from bins instead of rescoring a day of traffic on every drag.

`alerts_per_analyst_hour` divides by `IDS_ANALYST_SHIFT_HOURS` and not by 24, matching `training/metrics.py`: the shipped card's 162.56 false alerts/day reads as 20.32 an hour over an 8-hour shift. A slider and a model card quoting different volumes for one operating point is the kind of disagreement nobody notices until a SOC lead sets a threshold from the wrong one.

### The declared range, and what sits outside it

`[0.0, 1.0]` stays as declared, but Stage 2's errors reach about **1.83** on the shipped card, so the top of the attack distribution is outside what this slider can express. The response says so in `covers_distribution` rather than quietly reporting a recall for a threshold the axis cannot reach. `[0, 1]` covers the whole benign distribution plus the operating point (`tau_anom` 0.1098, `budget_tau` 0.4244), which is the part of the axis a SOC lead actually drags.

The budget it is judged against is configuration, not a constant: `max_alerts_per_day = IDS_ANALYST_CAPACITY_PER_HOUR * IDS_ANALYST_SHIFT_HOURS`, and `target_fpr = max_alerts_per_day / IDS_EXPECTED_DAILY_FLOW_VOLUME`. With the shipped defaults that is 320 alerts/day and a target FPR of 3.2e-4.

### Status codes

200, 422 for a missing or out-of-range `t`, 503 when no Stage 2 benign error distribution is loaded.

---

## GET /api/v1/metrics/drift

Population Stability Index per feature over time.

### Today

501, and note the phase differs: `"Phase 7 (drift and active learning)"`.

```bash
curl -s http://127.0.0.1:8000/api/v1/metrics/drift
```

### Planned

PSI per feature per snapshot, against the training reference distribution, with the warning bands at 0.1 (moderate) and 0.25 (significant). `population_stability_index()` in `backend/app/drift.py` currently raises `NotImplementedError` naming Phase 7. There is no drift snapshot table in the schema yet; see [Database Schema](Database-Schema.md).

---

## GET /api/v1/analytics/summary

Aggregates for whoever is not working the queue: a team lead, a reviewer, or the presenter during a demo.

```bash
curl -s 'http://127.0.0.1:8000/api/v1/analytics/summary?range=7d'
```

### Parameters

| Parameter | In | Type | Allowed | Default | Meaning |
| --- | --- | --- | --- | --- | --- |
| `range` | query | string | `24h`, `7d`, `30d`, `all` | `24h` | Time range |

An unrecognised range returns 422.

### Response

| Field | Contents |
| --- | --- |
| `series` | Alerts over time, each bucket split into `known` and `unclassified` |
| `total_alerts`, `unclassified_alerts`, `unclassified_rate` | Headline counts |
| `families` | The family breakdown for the range, most frequent first |
| `top_destination_hosts`, `top_destination_ports`, `top_source_hosts` | Ranked tables, top 10 |
| `throughput` | Opened vs resolved, verdict counts, TP rate, mean seconds to verdict |

Bucket width is an hour for `24h` and a day for the rest.

### Unlike `/metrics/*`, this is computed from the database

It describes what this deployment has actually seen, which is the opposite of `/metrics/model`, where every number comes from the offline evaluation and must never move when a replay runs. Worth keeping straight when reading the two screens side by side.

### Three decisions worth knowing

**The series splits known from unclassified rather than reporting a total.** The unclassified line is the novel-detection headline — the one a reviewer asks about if it spikes — and folding it into a total would hide exactly the claim this project is making.

**Empty buckets are emitted as zeros, not omitted.** A chart that skips empty hours draws a continuous line through the gap and makes an outage look like steady traffic.

**`UNSURE` is excluded from `true_positive_rate`'s denominator** rather than counted as a miss. It is not a judgement that the alert was wrong, and folding it in would drag the rate down every time an analyst was honest about not knowing — which punishes the behaviour the feedback loop most wants. With nothing decided the rate is `null`, not `0.0`: zero would claim every alert was a false positive.

Time buckets are floored in Python rather than in SQL, because the date functions differ between SQLite and Postgres and this project's persistence story is that pointing `IDS_DATABASE_URL` at Postgres is a configuration change and nothing more.

No accuracy tile. If this screen needs one big number it is alerts per analyst hour or the unclassified-anomaly rate.

### Status codes

200 (including on an empty database — no alerts yet is a real answer), 422 for an unrecognised range.

---

## GET /api/v1/analytics/mitre-coverage

Technique counts for the coverage heatmap.

```bash
curl -s http://127.0.0.1:8000/api/v1/analytics/mitre-coverage
```

### Response

`techniques`: one row per technique **the lookup table knows**, each with `family`, `technique_id`, `name`, `url`, `means` and `count`. Plus `unclassified_anomalies`, a single count.

### The axis comes from the table, not from the data

`app/mitre.py::coverage_vocabulary()` builds the rows, so a technique with no hits is a **visible zero** rather than a missing row. "We have never seen this" and "we cannot see this" look identical when the axis is built from whatever happened to fire, and telling them apart is the entire point of a coverage heatmap.

`UNCLASSIFIED_ANOMALY` maps to no technique by design, so it has no row — but its count sits beside the table rather than being dropped. Omitting it would understate exactly the detections this project is proudest of; giving it a technique id would be a fabrication. `app/mitre.py::technique_for` returns `None` for it and the honest panel is `app/remediation.py::NO_PLAYBOOK`.

### Status codes

200.

---

## POST /api/v1/replay/start

Start streaming held-out test rows at accelerated time.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/replay/start \
  -H 'Content-Type: application/json' -d '{"speed": 10, "dataset": "test"}'
```

```json
{"running": true, "speed": 10, "dataset": "test",
 "started_at": "2026-10-06T05:25:17.442052Z", "rows_scored": 0, "alerts_emitted": 0}
```

### Request

| Field | Type | Allowed | Meaning |
| --- | --- | --- | --- |
| `speed` | integer | `1`, `10`, `100` | Time acceleration |
| `dataset` | string | `test`, `val` | Which held-out split to replay |

`speed` is a `Literal`, so an unlisted value is a 422 from the validation layer rather than something the engine has to police.

**`train` and `benign_train` are not replayable.** Streaming the rows a model was fitted on would show it recognising traffic it memorised, which is the one demo that proves nothing — so the engine refuses the name and says why.

### What "accelerated time" means here

The published MachineLearningCSV release ships **no `Timestamp` column**, so there are no inter-arrival gaps to play back faster: there is nothing to accelerate *from*. Replay time is **synthesised** rather than reconstructed, and `app/replay.py` says so rather than implying it recovered a rate the data does not carry.

One nominal rate stands in and `speed` multiplies it:

```
sleep_between_batches = BATCH_ROWS / (BASE_FLOWS_PER_SECOND * speed)
```

**Speed moves only the gap between batches, never the batch size.** A speed control that grew the batch would make 100x a different *computation* rather than the same one delivered faster, and alert volume per wall-clock second would stop being a straight multiple of the 1x rate — which is exactly the comparison the demo invites. A test pins it.

Measured at 10x: 15,500 rows in 40 seconds, about 390 rows/second against 500 targeted. The gap is the scoring and explanation work inside each batch, which the pacing does not subtract. See `reports/phase5_api.md`.

### Behaviour

Runs as an asyncio background task, scoring in batches and pushing over SSE. Rows carry real ground-truth labels, which is what lets the dashboard show predictions against truth and badge them demo-only. Each batch gets its own transaction, so a 5,000-flow burst is one commit.

A rejected start leaves nothing half-armed: the dataset is resolved **before** the state is touched, so a typo answers 422 and the service is still idle rather than wedged until someone calls stop.

### Status codes

202 on start, 409 if a replay is already running, 422 for an unknown dataset or an unlisted speed, 503 if no model stage is loaded.

---

## POST /api/v1/replay/stop

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/replay/stop
```

No body. Returns the run's **final** counters, which is the useful thing to return from a stop — "it scored 15,500 rows and raised 93 alerts before you stopped it" rather than an empty acknowledgement.

The stop **awaits** the cancelled task rather than returning once cancellation is requested, so an immediately following `/replay/start` cannot race a run that is still writing.

### Status codes

202 on stop, 409 if nothing is running.

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

One entry per row of `model_versions`: `version`, `stage` (`champion` / `challenger` / `archived`), `supervised_algorithm`, `anomaly_algorithm`, `trained_at`, `trained_on`, `schema_hash`, `tau_sup`, `tau_anom`, `metrics`, `is_active`. The table exists today and is empty. Columns are documented in [Database Schema](Database-Schema.md).

---

## Streaming

`GET /api/v1/stream` is the live alert feed.

### Why SSE and not WebSockets

The feed is one-directional: the server pushes alerts, the browser never pushes back. `EventSource` is built into the browser, handles reconnection itself, and needs no client library. FastAPI serves SSE in roughly ten lines by returning a `StreamingResponse` over an async generator with content type `text/event-stream`. A WebSocket would add a bidirectional protocol, a handshake, a keepalive scheme and reconnect logic in exchange for a channel back to the server that nothing in the design uses.

Verdicts, replay control and filtering are all ordinary HTTP calls on other endpoints, so the second direction is already covered.

### Event shape

```
event: alert
data: {"id":4,"kind":"UNCLASSIFIED_ANOMALY","family":null,"severity":"high",
       "risk_score":0.695,"anomaly_score":0.1534,
       "detected_at":"2026-10-06T05:25:02.190827Z","src_ip":"172.16.0.1",
       "dst_ip":"192.168.10.14","occurrence_count":27,
       "model_version":"stage1-lgbm-202609281410"}

event: heartbeat
data: {"ts":"2026-10-06T05:25:17.442052+00:00"}
```

Alerts reach the stream *after* dedupe, not before. One compromised host emitting 5,000 flows is one incident and therefore one event with a rising `occurrence_count` — without that, the queue is unusable within thirty seconds of starting a replay. The dedupe key is already implemented: `dedupe_key(src_host, alert_class, timestamp)` in `backend/app/dedupe.py` floors the timestamp into a bucket of `IDS_DEDUPE_WINDOW_SECONDS` seconds (default 300) and returns `"<src_host>|<alert_class>|<bucket>"`.

### Verifying the stream

```bash
curl -N http://127.0.0.1:8000/api/v1/stream
```

`-N` disables curl's output buffering; without it the events sit in a buffer and the stream looks dead. Running this alongside a 10x replay, and watching burst traffic collapse into single events with incrementing counts, is the Phase 5 checkpoint. It is recorded in `reports/phase5_api.md` and re-runnable with `python scripts/phase5_checkpoint.py --speed 10`.

**One bug this checkpoint found**, worth repeating because no unit test caught it: the first capture emitted `detected_at` with no timezone suffix. Every event after the first in a burst is built from a row that has round-tripped through SQLite, which strips `tzinfo`, so the value serialised without its `Z` and a browser would have parsed UTC as local time. The first event of each burst looked correct and only the repeats were wrong, which would have read as an intermittent display glitch rather than a systematic offset. The pipeline now re-attaches UTC. The pipeline's own tests assert ids and counts off published events, and the surviving `detected_at` is only observable on the wire — which is what a live checkpoint is for.

---

## Scoring

Scoring is **batch always**. `POST /api/v1/score` takes a list of flow records and `ModelBundle.score_batch()` scores them as a single matrix.

This is a performance contract, not a style preference. Calling `predict()` once per row inside the replay loop is roughly 50x slower than one batched call: every call pays Python-level dispatch, per-call validation, and array allocation overhead that vectorised scoring pays once for the whole matrix. At 100x replay speed that difference is the gap between a feed that scrolls and a feed that stutters, which is visible to everyone watching a live demo.

The same rule applies everywhere flows are scored. Replay batches (`app/replay.py` ships 500 rows at a time). Live capture batches. The single-flow case is a batch of one, not a separate code path.

Explanation is batched for the same reason and it is the less obvious half: `app/pipeline.py` masks a batch's alerting rows into a Stage 1 group and a Stage 2 group and calls each explainer **at most once**, skipping an empty group before the matrix exists so a batch with no Stage 1 alerts never pays shap's `TreeExplainer` setup cost.

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

Nothing in Phases 0 through 9 schedules such an endpoint. See [Anti-Patterns](Anti-Patterns.md) for why the auto-block button is listed there as a failure, not a feature.
