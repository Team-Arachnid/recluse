# API Reference

Every HTTP endpoint the Recluse backend exposes and what it returns. Facts here are taken from `backend/app/main.py`, `backend/app/routes/`, `backend/app/schemas.py`, `backend/app/config.py` and the modules behind them (`app/pipeline.py`, `app/replay.py`, `app/live_capture.py`, `app/events.py`, `app/metrics_store.py`, `app/registry.py`) as they stand at the end of **Phase 9**.

**All twenty-six operations answer with real data; none is a stub.** Phase 0 registered the original sixteen and Phase 5 implemented thirteen of them; Phase 6 added the reads the dashboard needed, Phase 7 drift, the registry and retraining, Phase 8 the replay dataset list, and Phase 9 live capture. `reports/phase5_api.md` is the measured checkpoint for the replay path and `reports/phase9_live.md` for live capture, and `backend/tests/snapshots/openapi.json` is the whole contract, compared against the running app on every test run.

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

Through Phase 8, every endpoint a later phase would implement was **registered early** and answered `501 Not Implemented` with a machine-readable body naming that phase. A caller could tell "not built yet" from "built and broken", the OpenAPI schema was complete from day one, and no route ever returned invented data. Phase 9's `POST /ingest/start` was the last of them: **no route answers 501 today.**

`backend/tests/test_api_surface.py` keeps the record. `DEFERRED_ROUTES` -- routes that still answer 501, each with its exact phase string -- is empty, and kept so a future stub has somewhere to be declared; `IMPLEMENTED_ROUTES` lists all twenty-six operations, and `test_implemented_routes_do_not_answer_501` calls every one. A partition test asserts the two lists together equal the operations in the served schema, so a route cannot be added without being accounted for.

The mechanism stays for that future stub. `not_implemented(endpoint, phase)` in `backend/app/routes/__init__.py` returns `JSONResponse(status_code=501)` with a `NotImplementedResponse` body from `backend/app/schemas.py`:

```json
{
  "detail": "Not implemented yet. Arrives in Phase 9 (real traffic).",
  "phase": "Phase 9 (real traffic)",
  "endpoint": "POST /ingest/start"
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `detail` | string | Human-readable, always `Not implemented yet. Arrives in <phase>.` |
| `phase` | string | The build phase that implements this endpoint |
| `endpoint` | string | The endpoint as the route reports itself, path parameters resolved |

No route references `NotImplementedResponse` any more, so it is absent from the served OpenAPI schema and from `frontend/src/types/api.d.ts`. The client still handles a 501 should one appear: `ApiError.isNotImplemented` in `frontend/src/api/client.ts` is true for status 501, and the `retry` predicate in `createQueryClient()` (`frontend/src/api/queryClient.ts`) declines to retry it -- retrying a route that does not exist yet only delays the message the UI wants to show.

### Wire vocabularies (`backend/app/schemas.py`)

The wire contracts live in one file, and its module docstring states the rule that makes it worth reading: these models are the source of truth for the frontend's TypeScript types, which are generated from this app's OpenAPI schema (`npm run gen:types`) rather than hand-written. A vocabulary change therefore reaches the frontend through a regeneration, never by editing `frontend/src/types/api.d.ts` by hand.

Twelve `Literal` type aliases, kept in step with the `CheckConstraint`s in `backend/app/models.py`:

| Alias | Members |
| --- | --- |
| `AlertKind` | `"KNOWN"`, `"UNCLASSIFIED_ANOMALY"` |
| `AlertFamily` | `"dos"`, `"ddos"`, `"brute_force"`, `"port_scan"`, `"web_attack"`, `"botnet"`, `"infiltration"` |
| `Severity` | `"low"`, `"medium"`, `"high"`, `"critical"` |
| `AlertStatus` | `"open"`, `"in_review"`, `"closed"`, `"dismissed"` |
| `Verdict` | `"TP"`, `"FP"`, `"UNSURE"` |
| `VerdictFilter` | `"TP"`, `"FP"`, `"UNSURE"`, `"none"` -- the queue's filter, where `none` means "not yet judged" |
| `DetectionStage` | `"stage1_supervised"`, `"stage2_anomaly"` |
| `AlertSource` | `"replay"`, `"live"`, `"api"` |
| `HealthStatus` | `"ok"`, `"degraded"` |
| `DriftBand` | `"stable"`, `"moderate"`, `"significant"` |
| `RetrainStatus` | `"requested"`, `"running"`, `"completed"`, `"failed"`, `"cancelled"` |
| `ModelStage` | `"champion"`, `"challenger"`, `"archived"` |

Around them sit forty-nine Pydantic models: a response model for every route (each named in that route's section below), the request bodies (`FlowRecord`, `VerdictRequest`, `AlertStatusUpdate`, `ReplayStartRequest`, `RetrainRequest`, `IngestStartRequest`), and the nested shapes they are built from. Models with a `model_`-prefixed field -- `HealthResponse`'s `model_version` is the first -- set `model_config = ConfigDict(protected_namespaces=())`: Pydantic v2 reserves the prefix, and `model_version` is part of the agreed contract, so the namespace guard is lifted rather than the field renamed.

### Route modules (`backend/app/routes/`)

`backend/app/routes/__init__.py` holds the package docstring, the `not_implemented()` helper kept for any future stub, and the assembly of `api_router`. Two details are easy to trip over:

- It exports `__all__ = ["api_router", "not_implemented"]`, and it imports the seven route modules **below** the helper definition with a `# noqa: E402`, commented "Imported after the helper so the route modules can use it without a cycle". Moving those imports to the top of the file breaks the package.
- `api_router` is a bare, prefixless `APIRouter()`. Every path segment above `/api/v1` comes either from a sub-router's own `prefix` or from the path string on the decorator, and the sub-routers are included in a fixed order (alerts, score, metrics, drift, analytics, replay, stream).

Each module is a set of thin handlers over the module that does the work: `alerts.py` and `analytics.py` query the database, `metrics.py` serves the evaluation artifacts loaded at startup, `drift.py` serves what the drift and retrain jobs wrote and queues retrain requests, `replay.py` drives the replay engine and the live capture, `score.py` calls `ModelBundle.score_batch`, and `stream.py` reads the SSE broker. No handler fits, loads or trains a model.

Every route also carries a `summary`, which is the one-line title `/docs` shows, and FastAPI derives an operation ID from the handler name and path — `list_alerts_api_v1_alerts_get`, `get_alert_api_v1_alerts__alert_id__get`, and so on. Those operation IDs are what name the entries under `operations` in `frontend/src/types/api.d.ts`, so they are the names a frontend reader actually imports.

### Application setup (`backend/app/main.py`)

The application factory and `GET /health` live in the same small module.

| Symbol | Signature | What it does |
| --- | --- | --- |
| `API_DESCRIPTION` | `str` | The Markdown description rendered at the top of `/docs`: the two-stage summary, and the sentence "The system alerts, ranks and explains. It never blocks traffic." This is where the never-blocks rule is stated to every API reader. |
| `_configure_logging()` | `-> None` | `logging.basicConfig` at `settings.log_level` with a fixed format. Private; called from the lifespan. |
| `lifespan(app)` | `-> AsyncIterator[None]` | The async context manager passed to `FastAPI(lifespan=...)`. |
| `health_router` | `APIRouter(tags=["system"])` | Carries `GET /health` alone, mounted separately from `api_router`. |
| `health(request)` | `-> HealthResponse` | The handler. Reads `request.app.state.bundle` and `request.app.state.started_at`. |
| `create_app()` | `-> FastAPI` | Builds the app: title, `API_DESCRIPTION`, `version=__version__` (`0.1.0`, from `backend/app/__init__.py`), the lifespan, `docs_url`, `openapi_url`, optional CORS, then both routers at `settings.api_v1_prefix`. |
| `app` | `FastAPI` | Module-level `app = create_app()`. This is the target uvicorn is pointed at (`uvicorn app.main:app`). |

The lifespan runs, in order: `_configure_logging()`, `settings.ensure_directories()`, `app.state.started_at = time.monotonic()`; the SSE broker (`app.state.broker`), the replay state (`app.state.replay`) and the live-capture state (`app.state.ingest`), created up front so a status read before anything has started answers "not running" rather than raising; `load_bundle(settings.artifacts_path)` parked on `app.state.bundle`; the evaluation artifacts parked on `app.state.metrics`; `register_champion()`, which records what was actually loaded in the model registry (non-fatal: a failed registry write is logged, not an outage); then two log lines -- one naming the app, env, database backend and model version, one printing the false-positive budget. A `SchemaHashMismatch` raised by `load_bundle()` is intentionally fatal and takes the process down at boot; the comment in the source says so.

Because `create_app()` is a function rather than a module-level expression only, the tests can build a fresh app per test (`test_api_surface.py` does exactly that) while uvicorn uses the single module-level instance.

---

## Endpoint index

Twenty-six operations, all implemented. **Nothing documented is unrouted and nothing routed is undocumented**, and both halves are machine-checked:

- `backend/tests/test_api_surface.py` holds `IMPLEMENTED_ROUTES`, the same twenty-six `(method, path)` pairs as the table below. `test_route_is_documented` asserts each appears in `create_app().openapi()` -- read from the schema rather than from `app.routes`, because the schema is what the frontend generates types from, and a route missing there is a route the client cannot see. `test_implemented_routes_do_not_answer_501` calls each one. `test_deferred_and_implemented_routes_partition_the_documented_surface` asserts the listed pairs equal the schema's operations exactly, so a new route that neither list knows about fails the suite. `test_no_route_mentions_blocking` asserts no path contains `block`, `drop` or `quarantine` (see [Explicitly absent](#explicitly-absent)), and `test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` asserts `load_bundle()` raises `SchemaHashMismatch` on a bundle whose hash disagrees with its feature order.
- `backend/tests/test_api_contract.py` (Phase 8) compares the whole served schema -- paths, parameters, request and response models, descriptions -- against the committed snapshot `backend/tests/snapshots/openapi.json`. A contract change therefore shows up as a reviewed diff of that file, made with `make openapi`, which also regenerates `frontend/src/types/api.d.ts` from it; `frontend/src/types/contract.test.ts` checks the generated types match the snapshot.

The `client` fixture in `backend/tests/conftest.py` is session-scoped and enters the `TestClient` context manager, which is what makes the lifespan -- artifact loading and the schema-hash check -- actually run during the suite. The `api_prefix` fixture returns `settings.api_v1_prefix`, so the tests never hardcode `/api/v1` either.

| Method | Path | Purpose | Phase |
| --- | --- | --- | --- |
| GET | `/api/v1/health` | Liveness, loaded model version, uptime | 0 |
| POST | `/api/v1/score` | Score a batch of flow records | 5 |
| GET | `/api/v1/alerts` | Filter, sort, cursor-paginate the queue | 5 |
| GET | `/api/v1/alerts/stats` | Counts for the strip above the queue | 6 |
| PATCH | `/api/v1/alerts/status` | Move a batch of alerts to a triage status | 6 |
| GET | `/api/v1/alerts/{alert_id}` | Detail: explanation, narrative, remediation, raw flow | 5 |
| POST | `/api/v1/alerts/{alert_id}/verdict` | Record TP / FP / UNSURE with a note | 5 |
| GET | `/api/v1/alerts/{alert_id}/related` | Same source host, 24h window | 5 |
| GET | `/api/v1/stream` | Live alert feed over SSE | 5 |
| GET | `/api/v1/metrics/model` | Per-class metrics, PR/ROC curves, LOAO table | 5 |
| GET | `/api/v1/metrics/threshold` | Projected alert volume at a candidate threshold | 5 |
| GET | `/api/v1/metrics/anomaly-histogram` | The Stage 2 error bins the threshold line is drawn across | 6 |
| GET | `/api/v1/metrics/drift` | PSI per feature over time, with the baseline overlay | 7 |
| GET | `/api/v1/models` | Registry: versions, thresholds, alerts each one scored | 7 |
| POST | `/api/v1/retrain` | Queue a challenger run from the analyst labels | 7 |
| GET | `/api/v1/retrain` | Retraining history, promoted or not | 7 |
| GET | `/api/v1/analytics/summary` | Alerts over time, family mix, top hosts, throughput | 5 |
| GET | `/api/v1/analytics/feedback` | Labels since the last retrain, TP/FP split, disagreement | 6 |
| GET | `/api/v1/analytics/mitre-coverage` | Technique counts for the coverage heatmap | 5 |
| GET | `/api/v1/replay/status` | Whether a replay is running, and what it has done | 6 |
| GET | `/api/v1/replay/datasets` | What a replay can stream here | 8 |
| POST | `/api/v1/replay/start` | Start a dataset replay at 1x / 10x / 100x | 5 |
| POST | `/api/v1/replay/stop` | Stop the active replay | 5 |
| GET | `/api/v1/ingest/status` | Live capture state, and both Stage 2 thresholds | 9 |
| POST | `/api/v1/ingest/start` | Begin a shadow burn-in or an alerting capture | 9 |
| POST | `/api/v1/ingest/stop` | Stop the capture, scoring what it already metered | 9 |

Router registration, with the tags that group the endpoints in `/docs` and travel in the served schema:

```
create_app()
  ├─ health_router   prefix=/api/v1   tags=["system"]     →  GET /health
  └─ api_router      prefix=/api/v1   (bare APIRouter, no prefix of its own)
       ├─ alerts.router      prefix=/alerts     tags=["alerts"]     (6 routes)
       ├─ score.router       prefix=/score      tags=["scoring"]    (1 route)
       ├─ metrics.router     no prefix          tags=["metrics"]    (/metrics/model, /threshold, /anomaly-histogram)
       ├─ drift.router       no prefix          tags=["drift"]      (/metrics/drift, /models, /retrain x2)
       ├─ analytics.router   prefix=/analytics  tags=["analytics"]  (3 routes)
       ├─ replay.router      no prefix          tags=["traffic"]    (/replay/* x4, /ingest/* x3)
       └─ stream.router      prefix=/stream     tags=["stream"]     (1 route)
```

The eight tags in the schema are `alerts`, `analytics`, `drift`, `metrics`, `scoring`, `stream`, `system` and `traffic`. `/replay/*` and `/ingest/*` share the `traffic` tag because they are one concept -- where flows come from -- and they never run at once: one writer is what keeps the dedupe upsert's single-writer invariant, so each start refuses with 409 while the other source is running. `/metrics/drift` sits under `drift` rather than `metrics` because it is served from what the drift job wrote, not from the evaluation artifacts.

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

With the committed release installed this reports both stages, `stage1-lgbm-202610070057+stage2-autoencoder-202610070106`. `make dev`, `make models` and the container install it whenever nothing is serving, so the `"unloaded"` case below is what an artifacts directory with no model in it reports — the release removed, or never installed.

On a clean clone there is no `backend/artifacts/preprocessing.pkl` — artifacts are gitignored reproducible output — so `ModelBundle.load()` logs that fact and returns an empty bundle whose `version` is the module constant `UNLOADED_VERSION = "unloaded"`. That is not an error state: `status` stays `"ok"`, because the API is expected to serve health and the dashboard shell before any model exists.

After a training run (`make data`, `make train`, `make train-lgbm`, `make train-anomaly`) the field carries what that run promoted, both stages joined by `+`. The endpoint reads it from the bundle rather than holding a constant, and `backend/tests/test_health.py` asserts exactly that, so the test passes in every state.

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
  "model_version": "stage1-lgbm-202610070057+stage2-autoencoder-202610070106",
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

The documented response carries no `id`, and an `alerts` row needs a source address — `alerts.src_ip` is `NOT NULL` — which an arbitrary API caller does not supply. The dedupe/enrich/persist/push pipeline runs where a flow's origin is known: the replay path (`app/replay.py`) and live capture (`app/live_capture.py`), both through `app/pipeline.py`. `ALERT_SOURCES` carries an `"api"` value that nothing writes for that reason.

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
| `raw_flow` | The flow as it arrived, carrying the `_provenance` key. On a live alert that key also records the local `tau_anom` the alert was decided at, which the drawer shows in place of the dataset's |
| `ground_truth_label` | Replay only; always `null` for live capture, badged demo-only on the frontend |
| `ground_truth_counts` | Replay only: every dataset label the alert's dedupe bucket absorbed, counted -- `ground_truth_label` is the first flow's, this is all of them. `null` for live capture |
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

## GET /api/v1/alerts/stats

Counts for the strip above the queue. Response model `QueueStats`.

```bash
curl -s http://127.0.0.1:8000/api/v1/alerts/stats
```

| Field | Meaning |
| --- | --- |
| `open_alerts` | Alerts with status `open` |
| `alerts_last_hour` | Alerts whose `detected_at` is within the last hour |
| `observed_alerts_per_hour`, `observed_window_hours` | All alerts over the span from the first to the last `detected_at`; `null` with no alerts |
| `hosts_affected`, `sources_seen` | Distinct destination and source addresses among open alerts |
| `unclassified_open` | Open `UNCLASSIFIED_ANOMALY` alerts |
| `unjudged_open` | Open alerts with no verdict yet |
| `generated_at` | When the counts were taken |

Everything is counted from the database. The projected volume and operating threshold that sit beside these on the strip come from `GET /metrics/threshold`, because a projection at a candidate threshold is a property of the model rather than of the queue. There is no accuracy figure: a strip is exactly where a number gets read without its caveat.

200 always, including on an empty database.

---

## PATCH /api/v1/alerts/status

Move a batch of alerts to one triage status in one transaction -- the queue's bulk dismiss.

```bash
curl -s -X PATCH http://127.0.0.1:8000/api/v1/alerts/status \
  -H 'Content-Type: application/json' -d '{"alert_ids": [12, 14, 15], "status": "dismissed"}'
```

Request `AlertStatusUpdate`: `alert_ids` (1 to 500 integers) and `status` (an `AlertStatus`). Response `AlertStatusResult`: the `status` applied, `updated` (the ids that were moved) and `missing` (ids that no longer exist). A stale id is reported rather than failing the batch, because a bulk action against a queue a replay is still writing to can legitimately name a row that has gone.

**It writes no verdict.** A triage state and an analyst's judgement are different facts: dismissing a noisy row is not the claim "the model was wrong", and conflating the two would poison the labels retraining reads.

200 on success; 422 for an empty list, more than 500 ids, or an unknown status.

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

**The 503 is deliberate rather than a convenience.** An empty stream and a dead stream are indistinguishable from the client's side, and the first is a bug while the second is Tuesday — so a stream with nothing feeding it refuses rather than holding open a connection that can never produce an event. `app.state.broker.active_source` is what the route consults, and `POST /replay/start` or `POST /ingest/start` is what sets it.

### Implementation notes

`app/events.py` holds one bounded `asyncio.Queue` per connection and **drops rather than blocks** when a queue is full: at 100x a backgrounded browser tab must not be able to stall the scoring loop, so the newest event is lost for that subscriber and counted in `dropped_events`. A silent drop would be a lie about what the ticker showed; a counted one is telemetry.

`publish()` is safe to call from another thread, and that is the normal case rather than the exotic one — `app/replay.py` and `app/live_capture.py` both score each batch through `asyncio.to_thread` because scoring and explaining are CPU-bound and would otherwise stall this stream. `asyncio.Queue` is explicitly not thread-safe, and a bare cross-thread `put_nowait` can enqueue an event without ever waking the coroutine awaiting `get()`, which looks exactly like a feed that silently stopped. The broker therefore hands the enqueue to the loop that owns the queues via `call_soon_threadsafe`.

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
| `fpr` | benign rows of the held-out test day at or above `t`, over all of them |
| `recall` | attack rows at or above `t`; `null` without that split loaded |
| `benign_rows` / `benign_above`, `attack_rows` / `attack_above` | the raw counts both figures come from |
| `false_alerts_per_day` | `fpr` × `IDS_EXPECTED_DAILY_FLOW_VOLUME` |
| `alerts_per_analyst_hour` | per **analyst** hour: divided by the shift length, not by 24 |
| `budget_per_day`, `target_fpr`, `within_budget` | the budget this is judged against |
| `covers_distribution` | `false` when `t` sits above the histogram's range |

Both figures are read off histograms that **share their bin edges**, so the false-positive rate and the recall describe the same point on the same axis rather than coming from two independently binned distributions whose boundaries do not line up.

The result is approximate by construction — a bin straddling `t` contributes all of itself — which is exactly the arithmetic a slider does when it projects a count from bins instead of rescoring a day of traffic on every drag.

`alerts_per_analyst_hour` divides by `IDS_ANALYST_SHIFT_HOURS` and not by 24, matching `training/metrics.py`: at the shipped `tau_anom` (0.1032) the test day projects 54,296 false alerts a day, which reads as 6,787 an hour over an 8-hour shift -- the Phase 3 domain-shift result, seen from the slider. A slider and a model card quoting different volumes for one operating point is the kind of disagreement nobody notices until a SOC lead sets a threshold from the wrong one.

### The declared range, and what sits outside it

`[0.0, 1.0]` stays as declared, but Stage 2's errors reach about **1.60** on the shipped card (the top of the shared bins), so the top of the attack distribution is outside what this slider can express. The response says so in `covers_distribution` rather than quietly reporting a recall for a threshold the axis cannot reach. `[0, 1]` covers both operating points (`tau_anom` 0.1032, `budget_tau` 0.4016) and all but a sliver of benign error (the validation day's 99.9th percentile is 0.28), which is the part of the axis a SOC lead actually drags.

The budget it is judged against is configuration, not a constant: `max_alerts_per_day = IDS_ANALYST_CAPACITY_PER_HOUR * IDS_ANALYST_SHIFT_HOURS`, and `target_fpr = max_alerts_per_day / IDS_EXPECTED_DAILY_FLOW_VOLUME`. With the shipped defaults that is 320 alerts/day and a target FPR of 3.2e-4.

### Status codes

200, 422 for a missing or out-of-range `t`, 503 when no Stage 2 benign error distribution is loaded.

---

## GET /api/v1/metrics/anomaly-histogram

The persisted Stage 2 error bins the Live screen draws its threshold line across. Response model `AnomalyHistogram`.

```bash
curl -s http://127.0.0.1:8000/api/v1/metrics/anomaly-histogram
```

`edges` (the bin boundaries, shared by every distribution), `spacing` (`log` for the shipped card -- log bins drawn on a linear axis would pile sixty bars into the left-hand tenth of the chart), `tau_anom` and `budget_tau`, and `distributions`: one entry each for `validation_benign`, `test_benign` and `test_attack`, with `rows`, per-bin `counts` and reference `percentiles`.

`GET /metrics/threshold` answers "what happens at t"; this answers "what does the distribution look like", which the projection endpoint cannot without being sampled sixty times. Nothing is recomputed from the database: these are the distributions the model was calibrated against, written by Phase 3 training.

200, or 503 when no Stage 2 error distribution is loaded -- an empty histogram would read as traffic with no reconstruction error at all.

---

## GET /api/v1/metrics/drift

Population Stability Index per feature over time, with the training baseline to overlay. Response model `DriftResponse`.

```bash
curl -s 'http://127.0.0.1:8000/api/v1/metrics/drift?snapshots=30'
```

| Parameter | In | Type | Meaning |
| --- | --- | --- | --- |
| `snapshots` | query | integer, 1–365, default 30 | How many runs the per-feature series reaches back over |

| Field | Meaning |
| --- | --- |
| `latest` | The most recent snapshot: its observed window, rows observed against the reference, `max_psi`, how many features are moderate or significant, `retrain_recommended`, the observed score histogram, notes, and every feature's PSI, worst first. `null` until the first run |
| `series` | Per feature, oldest first: PSI and band at each snapshot, plus `worst_psi` and the latest value; features sorted worst first |
| `snapshots` | Runs stored in total |
| `baseline` | The validation day's benign error histogram, which `tau_anom` was cut from, for the overlay |
| `moderate_threshold`, `significant_threshold` | The PSI bands, 0.1 and 0.25 |
| `sampled_rows`, `sample_stride` | How many scored flows the sample table holds, and the `IDS_DRIFT_SAMPLE_STRIDE` it was taken at |

**Served, never computed here.** `python -m training.drift_job` (`make drift`, `--source live|replay` to restrict it to one traffic source) computes a snapshot over the sampled window and stores it. PSI over ninety-two features is a scan of the sample table, and drift is a property of a window of time rather than of a request -- computed per request, two readers minutes apart would get two answers. An empty response is a real state and says so: a monitor that drew a flat line at zero before its first run would be telling its reader everything is fine.

Live capture feeds the same sample table (`source = live`), in shadow mode as well as alert mode: a burn-in is exactly when the distance from the training distribution is worth seeing.

200 always, including before the first snapshot; 422 for `snapshots` out of range.

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

## GET /api/v1/analytics/feedback

Analyst judgement on its way back to the model: the Feedback screen. Response model `FeedbackLoop`.

```bash
curl -s http://127.0.0.1:8000/api/v1/analytics/feedback
```

| Field | Meaning |
| --- | --- |
| `serving_model_version` | The bundle currently loaded |
| `total_alerts`, `judged_alerts`, `judged_share` | How much of the queue carries at least one verdict |
| `labels_total`, `labels_pending_retrain`, `labels_consumed` | Verdicts, split by whether a retrain has consumed them yet (`analyst_verdicts.consumed_at`) |
| `true_positives`, `false_positives`, `unsure` | The verdict counts |
| `disagreement_rate` | FP / (TP + FP): how often the analyst overruled the model; `null` with nothing decided. `UNSURE` is outside the denominator -- it is not a judgement that the model was wrong |
| `mean_seconds_to_verdict` | From detection to verdict |
| `by_model_version` | The same counts per model version, busiest first. Verdicts carry the version that produced the alert, so a label against a since-replaced champion stays attributed to it |
| `retrain_available`, `retrain_phase` | Whether `POST /retrain` takes requests -- true since Phase 7 -- and the phase that made it so |

200 always.

---

## GET /api/v1/replay/status

Whether a replay is running, at what speed, and what it has done: `running`, `speed`, `dataset`, `started_at`, `rows_scored`, `alerts_emitted` (response model `ReplayStatus`, the same body `/replay/start` and `/replay/stop` return).

```bash
curl -s http://127.0.0.1:8000/api/v1/replay/status
```

Start and stop describe the state at the moment they were called, which is no help to a dashboard opened after the fact -- a speed control showing 1x while the engine runs at 100x is worse than one showing nothing. Always 200: `running: false` is the answer, not an error.

---

## GET /api/v1/replay/datasets

Every name `/replay/start` accepts, and whether it can run here. A list of `ReplayDataset`: `name`, `label`, `description`, `available`, `rows` (from the Parquet footer, so listing costs a metadata read) and `reason` when unavailable.

```bash
curl -s http://127.0.0.1:8000/api/v1/replay/datasets
```

| Name | What it is |
| --- | --- |
| `test` | The held-out Friday in full, which the README's numbers describe. Needs `make data` |
| `val` | The validation Thursday in full, which both thresholds were cut on. Needs `make data` |
| `demo` | Real flows sampled from both held-out days and committed with the release (`backend/release/demo_flows.parquet`), so a clean clone or a container replays without the dataset |

A container ships the demo sample but not the 500 MB dataset, so the held-out days are listed as unavailable there rather than left for a start request to discover with a 422. Always 200.

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
| `dataset` | string | `test`, `val`, `demo` | Which held-out split to replay; see [`/replay/datasets`](#get-apiv1replaydatasets) |

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

202 on start, 409 if a replay or a live capture is already running, 422 for an unknown or absent dataset or an unlisted speed, 503 if no model stage is loaded.

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

## GET /api/v1/ingest/status

The live capture's state, where it may run, and both Stage 2 thresholds. Response model `IngestStatus`, the same body start and stop return.

```bash
curl -s http://127.0.0.1:8000/api/v1/ingest/status
```

| Field | Meaning |
| --- | --- |
| `running`, `mode`, `source`, `session_id`, `started_at`, `finished_at` | The current or last capture: `shadow` or `alert`, and `interface:<name>` or `pcap:<file>` |
| `packets`, `undecoded` | Frames seen, and those the meter could not decode as IPv4/IPv6 TCP or UDP |
| `flows`, `unscoreable`, `scored` | Flows the meter finished, the zero-duration ones it could not score (their rates are infinite and no training row had one), and the flows scored |
| `shadow_rows`, `alerts` | Shadow scores written, or alerting flows raised |
| `tau_anom` | The Stage 2 threshold this capture decides at: the dataset's in shadow mode, the local one in alert mode |
| `error` | Why the last capture stopped, if it stopped on an error |
| `allowed_interfaces`, `pcaps` | What `IDS_LIVE_INTERFACES` lists and what `IDS_LIVE_PCAP_DIR` holds -- the only things a start can name |
| `tau_anom_dataset` | The CICIDS2017 threshold |
| `calibration` | The local threshold a burn-in produced (`LocalCalibration`: `tau_anom_local`, the percentile, flows, `stage2_version`, the share the dataset threshold would have flagged, the Stage 1 rate, the capture sources and window), or `null` until one exists **for the Stage 2 model that is serving** -- a percentile of one model's errors means nothing against another's |

Always 200.

---

## POST /api/v1/ingest/start

Begin a shadow burn-in or an alerting capture. Live flows go through the **same** `features.py`, the same `ModelBundle.score_batch` and the same alert pipeline as a replay: if live traffic needed its own scoring code, the train/serve-skew defence would already be broken.

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ingest/start \
  -H 'Content-Type: application/json' \
  -d '{"source": "interface", "interface": "eth0", "mode": "shadow"}'
```

### Request

| Field | Type | Meaning |
| --- | --- | --- |
| `source` | `"interface"` \| `"pcap"` | Capture from a network interface, or read a recorded classic pcap |
| `interface` | string | With `source: interface`: a name listed in `IDS_LIVE_INTERFACES` |
| `pcap` | string | With `source: pcap`: a file name in `IDS_LIVE_PCAP_DIR` |
| `mode` | `"shadow"` \| `"alert"`, default `shadow` | Shadow scores every flow and alerts no one; alert raises alerts at the local threshold |

### Two constraints in the request path, not just the documentation

**Where capture runs is configuration, not a parameter.** Capture is lawful only on a network the operator owns or is explicitly authorised to monitor, so the interface must be one the operator listed in `IDS_LIVE_INTERFACES` (empty by default: no capture at all) and the pcap a file inside `IDS_LIVE_PCAP_DIR` -- a path that resolves anywhere else is a 404. No request can widen either. Raw capture needs root or `CAP_NET_RAW`.

**Shadow before alert.** Alert mode is refused with 409 until `python -m training.calibrate_live` (`make calibrate`) has cut a local `tau_anom` from a shadow burn-in for the serving Stage 2 model. The CICIDS2017 threshold on a real network flags its ordinary traffic -- 11.8% of it on the one burn-in recorded in `reports/phase9_live.md` -- so no live alert reaches the queue before a burn-in has priced the network's normal.

### What each mode writes

- **Shadow** writes one `shadow_scores` row per flow -- observed endpoints, Stage 1 confidence, Stage 2 error, and what the shipped model would have decided at the dataset thresholds -- plus drift samples, and no alerts. These rows are what the calibration reads.
- **Alert** scores with the local threshold in place of the dataset's and hands the batch to `app/pipeline.py::ingest_batch` with the observed addresses, ports and protocol. Risk is ranked against the same local threshold and the burn-in's error histogram that the calibration keeps beside it, since `app/risk.py` measures an anomaly as headroom past the threshold that produced it. Each alert's `raw_flow._provenance` says every endpoint was observed and records the threshold and calibration it was decided at. Live alerts never carry a ground-truth label.

The meter (`app/flowmeter.py`) reproduces CICFlowMeter-V3's flow features, quirks included, finishes a flow at its first FIN or after a 120-second flow timeout, and flushes flows idle for `IDS_LIVE_IDLE_FLUSH_S` (60) seconds; finished flows are drained every `IDS_LIVE_BATCH_INTERVAL_S` (2) seconds and scored in batches of up to 500. On loopback, the outgoing copy of each frame is dropped so no packet is counted twice.

### Status codes

| Code | When |
| --- | --- |
| 202 | Started; the body is the status at that moment |
| 403 | The interface is not in `IDS_LIVE_INTERFACES` |
| 404 | No such pcap in `IDS_LIVE_PCAP_DIR` |
| 409 | A capture or a replay is already running, or alert mode before a calibration exists |
| 422 | An invalid source or mode, or a missing interface or pcap name |
| 503 | Stage 2 is not loaded, so there is nothing to score or calibrate with |

A refused start leaves nothing armed.

---

## POST /api/v1/ingest/stop

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/ingest/stop
```

No body. Signals the capture to stop, then waits (up to 30 seconds) for the flows still open to be finished and scored, so the counters returned are the run's final totals. 202 on stop, 409 if nothing is running.

---

## GET /api/v1/models

The model registry, and with it the scoring audit trail made readable. Response model `ModelRegistry`.

```bash
curl -s http://127.0.0.1:8000/api/v1/models
```

`serving` is the version loaded now; `versions` has one entry per row of `model_versions`: `version`, `stage` (`champion` / `challenger` / `archived`), `is_active`, `supervised_algorithm`, `anomaly_algorithm`, `trained_at`, `trained_on`, `schema_hash`, `tau_sup`, `tau_anom`, `metrics`, `notes`, and -- the part that makes it an audit trail -- `alerts_scored`, `verdicts_recorded`, `first_alert_at` and `last_alert_at`, counted from the alerts and verdicts that version produced.

"Which model scored which alert" has been answerable since Phase 5, because `alerts.model_version` is written on every row; this is the place to ask it. `alerts_scored` is the figure needed after an incident, when the question is how many decisions a model that turned out to be wrong was behind. The serving bundle registers itself at startup (see [Application setup](#application-setup-backendappmainpy)), and a new version archives the previous champion rather than leaving two rows claiming to serve. Challengers are recorded on their retrain run (`GET /retrain`), and reach this list once one is promoted and served. Columns are documented in [Database Schema](Database-Schema.md).

200 always.

---

## POST /api/v1/retrain

Queue a challenger run from the analyst labels recorded so far. **202, and nothing is fitted here.**

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/retrain \
  -H 'Content-Type: application/json' -d '{"requested_by": "dashboard"}'
```

Request `RetrainRequest`: an optional `requested_by`. Response `RetrainRunResponse`, the queued run.

The handler writes a `retrain_runs` row with status `requested` and returns; `python -m training.retrain` (`make retrain`) claims the oldest pending row, fits the challenger, evaluates champion and challenger on the same held-out split, and promotes only past the gate. Fitting inside a request handler is the anti-pattern the brief names outright -- it would put a multi-minute, multi-gigabyte job on the event loop that also serves the alert stream.

| Code | When |
| --- | --- |
| 202 | Queued |
| 409 | A run is already requested or running: two would consume the same labels and race to publish a champion |
| 422 | No unconsumed analyst labels: a challenger fitted on the champion's own data differs only by random seed, so there would be nothing to promote on |

---

## GET /api/v1/retrain

Retraining history, newest first, including the runs that were not promoted -- a gate that has never declined anything is a gate nobody has evidence for. Response `RetrainStatusResponse`: `runs`, `pending` (runs still waiting for the worker) and `worker_hint` (how a requested run gets executed).

```bash
curl -s 'http://127.0.0.1:8000/api/v1/retrain?limit=20'
```

Each run carries its status and timestamps, the labels it consumed (split into false and true positives), both versions and their PR-AUC on `held_out_split`, `promoted`, the `decision` in words, any `error`, and `stage2` -- the guarded benign re-fit's half of the run, when one was attempted: the refit pool admitted and refused, and champion against challenger on PR-AUC, pool hold-out FPR and attack recall.

`limit` is 1–200, default 20. 200 always; 422 for `limit` out of range.

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

The same rule applies everywhere flows are scored. Replay batches (`app/replay.py` ships 500 rows at a time). Live capture batches (`app/live_capture.py` drains finished flows every `IDS_LIVE_BATCH_INTERVAL_S` seconds and scores them up to 500 at a time). The single-flow case is a batch of one, not a separate code path.

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
