# Architecture

How Recluse is put together: the two-stage detection pipeline, the fusion rule
that joins the two stages, what an HTTP request actually does inside the
running Phase 0 process, which module imports which, how the system is started
in development and in containers, and why each significant structural choice was
made instead of the obvious alternative. Written for anyone about to change
backend code, and for anyone reviewing whether the design supports the claim the
project makes. Start with [Project Overview](Project-Overview.md) for the claim
itself, and [Repository Layout](Repository-Layout.md) for where the files live.

**Status of this page.** The pipeline shape, the fusion rule and the alert
pipeline described below are the target design from `BUILD_PROMPT.md` Parts 2, 7
and 8. Phase 0 of 9 is complete, so sections marked **Today** describe code you
can run now; sections marked **Planned** describe code that raises
`NotImplementedError` or answers HTTP 501 today. No detection number on this page
is measured, because no model has been trained yet.

---

## The two-stage pipeline

```
                 ┌─────────────────────────────────────┐
  pcap / CSV ──▶ │  Feature extraction (features.py)   │
                 └──────────────┬──────────────────────┘
                                │
                 ┌──────────────▼──────────────────────┐
                 │  STAGE 1 — Supervised classifier    │
                 │  RandomForest → LightGBM, multiclass│
                 │  benign + known attack families     │
                 └──────────────┬──────────────────────┘
                                │
              confident attack ─┤─ confident benign ──▶ drop
                                │
                        low confidence
                                │
                 ┌──────────────▼──────────────────────┐
                 │  STAGE 2 — Anomaly detector         │
                 │  Autoencoder, benign-only training  │
                 │  reconstruction error > threshold   │
                 └──────────────┬──────────────────────┘
                                │
                 ┌──────────────▼──────────────────────┐
                 │  Alert pipeline                     │
                 │  explain → map + recommend → dedupe │
                 │  → enrich → persist → SSE push      │
                 └──────────────┬──────────────────────┘
                                │
                 ┌──────────────▼──────────────────────┐
                 │  React triage dashboard             │
                 │  analyst verdict ──▶ active learning│
                 └─────────────────────────────────────┘
```

### Feature extraction

`backend/training/features.py` turns a raw flow record — a row of a CICIDS2017
CSV today, a CICFlowMeter row derived from a pcap in Phase 9 — into the exact
numeric matrix the models were trained on. It is a separate box because it is the
only box that both training and serving enter. `app/inference.py` imports
`compute_schema_hash` from it directly; there is no second implementation of any
transform inside the API.

The module already defines the contract: `normalise_column_name(name)`,
`normalise_columns(columns)`, `compute_schema_hash(feature_order)`, the
`PreprocessingBundle` TypedDict, `build_preprocessing_bundle(scaler,
feature_order, dropped_columns, port_encoding)`, `save_preprocessing_bundle`,
`load_preprocessing_bundle` and `build_feature_matrix`, alongside the
`SCHEMA_HASH_PREFIX`, `LEAKAGE_COLUMNS`, `SPLIT_ONLY_COLUMNS` and `PORT_COLUMN`
constants. The hash is `SCHEMA_HASH_PREFIX` — the literal string `sha256` —
then a colon, then the SHA-256 of the feature names joined by newlines, so
reordering two columns changes it and any bundle still carrying the old value is
refused at boot. See [Code: Backend Pipeline](Code-Backend-Pipeline.md) and
[Data Pipeline](Data-Pipeline.md).

| | |
| --- | --- |
| Today | Column normalisation, the leakage-column list, the schema hash and the bundle read/write path exist and are tested (`backend/tests/test_features.py`). |
| Today | `build_feature_matrix(frame, bundle=None)` is implemented. It drops `LEAKAGE_COLUMNS`, applies the `destination_port` encoding, reindexes to `feature_order` and applies the fitted `RobustScaler`. Without a bundle it returns the unscaled feature frame, which is what fitting needs before a scaler exists; with one it reproduces the training-time matrix exactly. Cleaning itself belongs to `training/clean.py`, not here. |

### Stage 1 — supervised classifier

A multi-class model over benign plus the known attack families — `dos`, `ddos`,
`brute_force`, `port_scan`, `web_attack`, `botnet`, `infiltration`, a vocabulary
already fixed in `app/schemas.py` and `app/models.py`. It answers "which named
attack is this?" and it can only ever answer with a name it was trained on. The
seven attack names are the vocabulary fixed in `app/schemas.py` (the
`AlertFamily` literal) and `app/models.py` (the `ALERT_FAMILIES` tuple).
`benign` is a class the model predicts but is deliberately absent from both:
those enums are the *alert* vocabulary, and a benign prediction produces no
alert row at all.

It is a separate box because a supervised classifier is the cheap,
high-precision path: when it is confident, no further work is needed, and the
bulk of traffic resolves here. Artifact: `supervised_model.pkl`. See
[ML Models](ML-Models.md).

| | |
| --- | --- |
| Today | Not trained. `backend/training/train_supervised.py` is a Phase 2 module. |
| Planned | RandomForest baseline first — `n_estimators=300`, `max_depth` tuned against the validation day, committed as a running baseline before anything else is touched. LightGBM only once that baseline runs end to end and has been evaluated, with the RandomForest artifact kept as a fallback. `class_weight='balanced'`, never SMOTE; early stopping on the validation day; `tau_sup` persisted into the bundle. |

### Stage 2 — anomaly detector

A PyTorch autoencoder trained on benign traffic only, with no attack labels in
its training set at all. It answers "how unlike normal traffic is this?" by mean
squared reconstruction error per row.

It is a separate box precisely because it is trained on a different dataset with
a different objective. Merging it into Stage 1 would make it a supervised model
with extra steps, and the project's central claim — that it detects attack
traffic it was never trained on — would stop being testable. Artifact:
`autoencoder.pt`.

| | |
| --- | --- |
| Today | Not trained. `backend/training/train_autoencoder.py` is a Phase 3 module. |
| Planned | `input(d) → 64 → 32 → 16 → 32 → 64 → output(d)`, ReLU, MSE, Adam, dropout 0.1 in the encoder, batch norm, early stopping on benign validation loss. `tau_anom` at the 99.5th percentile of held-out benign reconstruction error, with the benign error distribution persisted as histogram bins rather than raw rows — `ModelBundle.benign_error_histogram` already reserves the slot, and both the dashboard threshold slider and drift detection read it. IsolationForest, LOF and ECOD run on the same split as baselines; if one of them wins, that is reported rather than hidden. |

### Alert pipeline

Between "a model produced a score" and "an analyst sees a row" sits a fixed
sequence: explain, narrate, map to MITRE ATT&CK and attach a recommended
response, deduplicate, enrich, persist, push over SSE.

It is a separate box because every one of those steps is a property of the
alert, not of the model, and each one has to happen for every alert regardless of
which stage produced it. Dedupe in particular is load-bearing: keyed on
`(src_host, alert_class, floor(ts, dedupe_window_seconds))`, it collapses one
compromised host's 5,000 flows into one incident row with `occurrence_count`
incremented. Without it the queue is unusable within thirty seconds of starting a
replay. The default window is `IDS_DEDUPE_WINDOW_SECONDS=300`.

The key is a pipe-delimited string, not a tuple: `dedupe_key()` returns
`"{src_host}|{alert_class}|{bucket}"` where
`bucket = epoch - (epoch % IDS_DEDUPE_WINDOW_SECONDS)`. It is stored in
`alerts.dedupe_key` (`String(255)`, not nullable) and looked up through the
composite index `ix_alerts_dedupe_key_last_seen`, with `occurrence_count >= 1`
enforced by the `occurrence_count_positive` check constraint.

| | |
| --- | --- |
| Today | `app/explain.py` (`explain_supervised`, `explain_anomaly`, `narrate`), `app/mitre.py` (`technique_for`) and `app/remediation.py` (`playbook_for`) are docstring-only — every function raises `NotImplementedError` naming Phase 5. `app/dedupe.py` is different: `dedupe_key(src_host, alert_class, timestamp)` is implemented now, and it is the only module in the alert pipeline that contains no `NotImplementedError`. The `alerts` table already carries `explanation`, `narrative`, `recommended_actions`, `raw_flow`, `dedupe_key`, `occurrence_count`, `first_seen` and `last_seen`. See [Database Schema](Database-Schema.md). |
| Planned | Phase 5 implements the sequence, and the insert-or-increment logic around `dedupe_key`. |

### React triage dashboard

The consumer. It reads the queue, renders the explanation, and writes the
analyst's verdict back, which is the input to Phase 7 active learning. It is a
separate box, and a separate process, because it is a static bundle: nginx can
serve it with the API behind a proxy path, and nothing in it needs to run on the
same host as the models. See [Frontend Screens](Frontend-Screens.md).

| | |
| --- | --- |
| Today | One screen, System Health, rendering live `/api/v1/health` data through TanStack Query. |
| Planned | Phase 6 delivers seven screens, starting with the triage queue as the landing page. |

---

## The fusion rule

This is the join between the two stages, specified in `BUILD_PROMPT.md` Part 7:

```python
def classify(x):
    p = supervised.predict_proba(x)
    attack_conf = p[attack_classes].max()

    if attack_conf >= tau_sup:
        return Alert(kind="KNOWN", family=argmax_class(p), conf=attack_conf)

    anom = autoencoder.score(x)
    if anom >= tau_anom:
        return Alert(kind="UNCLASSIFIED_ANOMALY", family=None, score=anom)

    return None
```

Three outcomes, and each one means something different:

| Outcome | Condition | What the analyst gets |
| --- | --- | --- |
| `KNOWN` | Stage 1's highest attack-class probability is at or above `tau_sup` | An alert with a family label, a confidence, and a MITRE technique with a written playbook |
| `UNCLASSIFIED_ANOMALY` | Stage 1 was not confident, but reconstruction error is at or above `tau_anom` | An alert with no family, an anomaly score, and the top-5 features the autoencoder failed to reconstruct |
| dropped | Neither threshold is crossed | Nothing. No row, no notification |

**`UNCLASSIFIED_ANOMALY` is the project thesis rendered as an enum value.** The
claim is that this system surfaces attacks it was never trained on; the mechanism
is a row that reaches an analyst carrying a score and an explanation but no name.
That is why it is a value in `ALERT_KINDS` in `app/models.py` and in the
`AlertKind` literal in `app/schemas.py` rather than a flag or a null family
alone; why the database carries a check constraint named `family_matches_kind`
asserting that an `UNCLASSIFIED_ANOMALY` has `family IS NULL` while a `KNOWN`
alert does not; why there is a dedicated index `ix_alerts_kind_detected_at`
behind the one-click filter chip; and why the UI gives it a visually distinct
badge. It has a reserved shape end to end before any model exists, so nothing
later can quietly collapse it into "low-confidence known attack".

Note what the rule does not do. It does not average the two scores, and it does
not let Stage 2 override a confident Stage 1 verdict. The stages are sequenced,
not blended, so every alert traces to exactly one producing stage — which is what
the `detection_stage` column (`stage1_supervised` / `stage2_anomaly`) records,
and what the "Caught by Stage 1 / Caught by Stage 2" columns of the
leave-one-attack-out table count. See [Roadmap](Roadmap.md).

`tau_sup` is not 0.5. It is chosen from analyst capacity; see
[Configuration](Configuration.md) for the budget arithmetic and the three
environment variables that feed it.

| | |
| --- | --- |
| Today | `ModelBundle.score_batch()` in `app/inference.py` raises `NotImplementedError` naming Phase 4. Both thresholds (`tau_sup`, `tau_anom`) are already fields on `ModelBundle` and columns on `model_versions`. |
| Planned | Phase 4 implements fusion on top of Phase 2 and Phase 3, then measures it with leave-one-attack-out. No fusion recall number exists yet — not measured yet, Phase 4 produces `reports/loao.md`. |

---

## Request lifecycle

This section describes the process you can run today, end to end. Source:
`backend/app/main.py`, `backend/app/config.py`, `backend/app/inference.py`,
`backend/app/routes/__init__.py`.

```
uvicorn app.main:app
        │
        ├─ import app.main
        │     ├─ import app.config      →  settings = get_settings()   (lru_cache, reads .env)
        │     ├─ import app.inference   →  imports training.features
        │     ├─ import app.routes      →  builds api_router from 6 modules
        │     └─ app = create_app()     (module level)
        │
        ├─ create_app()
        │     ├─ FastAPI(title=f"{settings.app_name} API",
        │     │          description=API_DESCRIPTION, version=__version__,
        │     │          lifespan=lifespan, docs_url="/docs",
        │     │          openapi_url="/openapi.json")
        │     ├─ add_middleware(CORSMiddleware, ...)   if settings.cors_origin_list
        │     ├─ include_router(health_router, prefix=settings.api_v1_prefix)
        │     └─ include_router(api_router,    prefix=settings.api_v1_prefix)
        │
        └─ lifespan startup
              │  (no database engine is built — app.db is never imported)
              ├─ _configure_logging()            level from settings.log_level
              ├─ settings.ensure_directories()   data / artifacts / reports
              ├─ app.state.started_at = time.monotonic()
              ├─ bundle = load_bundle(settings.artifacts_path)
              │     └─ ModelBundle.load()
              │           ├─ preprocessing.pkl absent → log, return unloaded bundle
              │           └─ present → unpickle → _verify_schema_hash()
              │                                 → _load_model_card()
              │                                 → _load_models()
              ├─ app.state.bundle = bundle
              └─ log "ready" + the false-positive budget line

   ── serving ──────────────────────────────────────────────────────────

   GET /api/v1/health
        │
        ├─ CORSMiddleware       origin checked against settings.cors_origin_list
        ├─ router match         prefix settings.api_v1_prefix + "/health"
        ├─ health(request)      reads request.app.state.bundle / .started_at
        └─ HealthResponse(status=bundle.status,
                          model_version=bundle.version,
                          uptime_s=round(monotonic() - started_at, 3))
```

Step by step:

1. **`uvicorn app.main:app`** — run from `backend/`, either by `scripts/dev.py`
   with `--reload` or by the container `CMD`. Importing `app.main` is what builds
   the application: `app = create_app()` executes at module level.
2. **Settings load first.** `app/config.py` runs `settings = get_settings()` at
   import time, and `get_settings()` is `@lru_cache`d, so the environment is read
   once per process. `Settings` carries `env_prefix="IDS_"` and
   `env_file=REPO_ROOT / ".env"`, so `IDS_PORT` in the repo-root `.env` populates
   `settings.port` no matter which directory the process was started from.
   `_resolve()` anchors relative paths to the repo root for the same reason.
3. **`create_app()`** builds the `FastAPI` instance. Title is
   `f"{settings.app_name} API"` (`"Recluse API"`, asserted by
   `backend/tests/test_api_surface.py`), version is `app.__version__`
   (`"0.1.0"`), docs at `/docs`, schema at `/openapi.json` — the schema the
   frontend's TypeScript types are generated from. `description` is
   `API_DESCRIPTION`, a module-level constant in `app/main.py`: a short
   two-stage summary whose last line is "The system alerts, ranks and explains.
   It never blocks traffic." The constraint is therefore published in
   `/openapi.json` and rendered at `/docs`, not merely asserted in prose.
4. **CORS middleware** is added only when `settings.cors_origin_list` is
   non-empty. That computed property splits the comma-separated
   `IDS_CORS_ORIGINS` string; it is a string rather than a list because
   pydantic-settings would otherwise demand a JSON array in the env file. Allowed
   methods are `GET`, `POST`, `PATCH`, `DELETE`, `OPTIONS`; `allow_headers` is
   `["*"]` and `allow_credentials` is `True`, so the origin list is the only
   thing narrowing the surface — which is why it is an explicit list rather than
   `*`. In native development
   the Vite dev server proxies `/api` to the backend, so the browser is
   same-origin and never exercises this path.
5. **Routers mount under a settings-driven prefix.** Both `health_router` and
   `api_router` are included with `prefix=settings.api_v1_prefix`, default
   `/api/v1`. No route path hardcodes its prefix; changing `IDS_API_V1_PREFIX`
   moves the whole surface. `health_router = APIRouter(tags=["system"])` is
   declared in `app/main.py` rather than in `app/routes/`, because `/health` is
   the one implemented endpoint and the only one that reads `app.state` —
   keeping it beside the factory leaves `app/routes/` uniformly "the v1
   surface, all of it stubbed".
6. **Lifespan startup** runs before the first request is served.
   `_configure_logging()` calls `logging.basicConfig` at
   `settings.log_level` with the format
   `"%(asctime)s %(levelname)-8s %(name)s | %(message)s"`,
   `settings.ensure_directories()` creates `data/`, `backend/artifacts/` and
   `reports/` if absent, the monotonic start time is recorded on `app.state`,
   and `load_bundle(settings.artifacts_path)` runs. Nothing here opens a
   database connection: the engine in `app/db.py` is built at *that* module's
   import time, and `app/main.py` never imports it.
7. **Artifact loading is deliberately fatal on inconsistency.** If
   `preprocessing.pkl` does not exist, `ModelBundle.load()` logs that this is
   expected until Phase 2 and returns an unloaded bundle whose `version` is
   `"unloaded"` — that is the Phase 0 path. If the file does exist,
   `_verify_schema_hash()` recomputes `compute_schema_hash(feature_order)` using
   `training/features.py` and raises `SchemaHashMismatch` when it disagrees with
   the persisted hash, which takes the process down at boot. Mismatched column
   order produces garbage scores without raising anything, so the check is what
   makes it loud. `_load_model_card()` additionally cross-checks the hash recorded
   in `model_card.json` against the one in `preprocessing.pkl`, and is where
   `version`, `tau_sup` and `tau_anom` come from — an absent card is not an
   error, it just leaves the bundle at `"unloaded"`. `_load_models()` imports
   `torch` lazily, inside the branch that finds `autoencoder.pt`, so a Phase 0
   boot with no artifacts never pays for the heaviest dependency in the project;
   importing `app.main` today leaves `torch` out of `sys.modules` entirely. When
   it does load, `torch.load(..., map_location="cpu", weights_only=True)` keeps
   `autoencoder.pt` data rather than executable code.
8. **Two log lines confirm the boot.** One naming the environment, the database
   backend and `model_version`; one stating the false-positive budget —
   `max_alerts_per_day`, `expected_daily_flow_volume`, and the derived
   `target_fpr`.
9. **A request arrives.** For `GET /api/v1/health` the handler takes `Request`,
   reads `request.app.state.bundle` and `request.app.state.started_at`, and
   returns a `HealthResponse`. Nothing is recomputed per request: `status` and
   `version` are properties of the bundle loaded once at startup, and `uptime_s`
   is `time.monotonic()` minus the recorded start, rounded to three places. No
   database session is opened — the handler reads `app.state` and nothing else.
   `status` is typed `Literal["ok", "degraded"]` in `app/schemas.py`, but
   `ModelBundle.status` returns `"degraded"` only when its private `_degraded`
   flag is set, and nothing in Phase 0 sets it anywhere in the repository — the
   endpoint always answers `"ok"` today. `"degraded"` is reserved for a bundle
   that is present but unusable; an *inconsistent* bundle is not degraded, it is
   fatal at boot. Phase 2 is the first code that can raise the flag.
10. **Pydantic serialises the response** against `response_model=HealthResponse`,
    which is also what puts the three-field contract into the OpenAPI schema and
    therefore into `frontend/src/types/api.d.ts`.
11. **Sixteen operations are registered; fifteen answer 501.** `GET /health` is
    the only implemented one. Each stub handler calls
    `not_implemented(endpoint, phase)` from `app/routes/__init__.py`, which builds
    a `NotImplementedResponse` (`detail`, `phase`, `endpoint`) and wraps it in a
    `JSONResponse(status_code=501, ...)`. Each route also declares
    `responses={501: {"model": NotImplementedResponse}}`, so the 501 body is part
    of the published schema rather than an undocumented surprise. The phase
    string is per route, not per module: Phase 5 for the alert, score, stream,
    analytics, `/replay/start` and `/replay/stop` endpoints; Phase 7 for
    `GET /metrics/drift` and `GET /models`; Phase 9 for `POST /ingest/start`.
    `metrics.py` and `replay.py` are the two modules that straddle phases, which
    is the whole point of putting the phase in the body rather than in a page
    footnote. The frontend's query client knows not to retry a 501. See
    [API Reference](API-Reference.md).

One structural detail worth knowing: `app/routes/__init__.py` defines
`not_implemented` before it imports the six route modules, with the import placed
after the function and marked `# noqa: E402`. All six route modules do
`from app.routes import not_implemented`, so defining it first is what keeps that
from being a circular import. The package then assembles `api_router` by
including `alerts`, `score`, `metrics`, `analytics`, `replay` and `stream` in
that order, and exports exactly `["api_router", "not_implemented"]`.

A second detail: four of the six route modules carry their own `prefix` on
`APIRouter(...)` — `/alerts`, `/analytics`, `/score`, `/stream`. `metrics.py`
and `replay.py` do not, because their paths do not share one stem:
`metrics.py` owns `/metrics/model`, `/metrics/threshold`, `/metrics/drift` and
`/models`, and `replay.py` owns `/replay/start`, `/replay/stop` and
`/ingest/start`. Those paths are spelled in full on the decorator instead.

---

## Module dependency map

First-party imports only; standard library and third-party packages are omitted.
Python modules under `backend/` only — the dashboard is a separate process and
imports nothing from here, its one dependency on the backend being the OpenAPI
schema that `frontend/scripts/generate-types.mjs` turns into
`frontend/src/types/api.d.ts`. Arrows point from importer to imported.

```
                       training.features
                              ▲
                              │     the shared feature contract — the only
                              │     edge crossing from app/ into training/
                        app.inference
                              ▲
                              │
      app.__init__            │        app.config ◀── app.db ◀── app.models
        (__version__)         │             ▲                         ▲
              │               │             │                         │
              └───────────┬───┴─────────────┘                         │
                          │                                           │
                       app.main ───▶ app.routes ───▶ app.schemas      │
                          │              │  ▲            ▲            │
                          └──────────────┘  │            │            │
                                            │            │            │
                    app.routes.{alerts, analytics, metrics,           │
                                replay, score, stream}                │
                                                                      │
      app.dedupe ───▶ app.config                                      │
                                                                      │
      alembic/env.py ───▶ app.config, app.db, app.models ─────────────┘

      no first-party imports yet (Phase 5 / 7 / 9 modules):
        app.drift   app.explain   app.live_capture
        app.mitre   app.remediation   app.replay
        training.clean   training.split   training.evaluate
        training.loao   training.train_supervised   training.train_autoencoder
```

| Module | Imports (first-party) | Imported by (first-party) |
| --- | --- | --- |
| `app/__init__.py` | — | `app.main` (for `__version__`) |
| `app/config.py` | — | `app.db`, `app.main`, `app.dedupe`, `alembic/env.py`, `tests/conftest.py`, `tests/test_config.py` |
| `app/db.py` | `app.config` | `app.models`, `alembic/env.py`, `tests/conftest.py`, `tests/test_schema_portability.py` |
| `app/models.py` | `app.db` | `alembic/env.py`, `tests/test_schema_portability.py` |
| `app/schemas.py` | — | `app.main`, `app.routes`, all six route modules |
| `app/inference.py` | `training.features` | `app.main`, `tests/test_api_surface.py` |
| `app/main.py` | `app`, `app.config`, `app.inference`, `app.routes`, `app.schemas` | `tests/conftest.py`, `tests/test_api_surface.py` |
| `app/routes/__init__.py` | `app.schemas`, plus the six route modules | `app.main`, and all six route modules (for `not_implemented`) |
| `app/routes/alerts.py`, `analytics.py`, `metrics.py`, `replay.py`, `score.py`, `stream.py` | `app.routes` (`not_implemented`), `app.schemas` | `app.routes` |
| `app/dedupe.py` | `app.config` | — (Phase 5 wires it in) |
| `app/drift.py`, `explain.py`, `mitre.py`, `remediation.py`, `replay.py`, `live_capture.py` | — | — (Phase 5 / 7 / 9) |
| `training/features.py` | — | `app.inference`, `tests/test_features.py` |
| `training/clean.py`, `split.py`, `train_supervised.py`, `train_autoencoder.py`, `evaluate.py`, `loao.py` | — | — (Phase 1–4) |
| `alembic/env.py` | `app.config`, `app.db`, `app.models` | Alembic |

Four properties this map exists to keep true:

- **`training/` never imports `app/`.** The dependency crosses in one direction
  only, `app.inference → training.features`. Training is offline batch and has to
  stay runnable without a web framework, a database or a settings object.
- **`app.config` is a sink, not a source.** It imports nothing first-party, so
  any module can read settings without creating a cycle.
- **Route modules depend on the package, not on each other.** There is no edge
  between `alerts.py` and `score.py`.
- **The serving process does not reach `app.db` or `app.models` in Phase 0.**
  Follow the arrows out of `app.main` and neither is reachable. The only
  first-party importers of the database layer today are `alembic/env.py` and two
  test modules. That changes in Phase 5, when a route handler first takes a
  session dependency.

---

## Process and deployment topology

### Native development — `make dev` / `./make.ps1 dev`

`scripts/dev.py` is a supervisor rather than a shell one-liner, because
backgrounding two processes and cleaning both up on Ctrl-C is not portable
between GNU make on Linux, Git Bash on Windows and PowerShell.

```
  make dev  /  ./make.ps1 dev
        │
        └─ python scripts/dev.py
              ├─ require("uv"), require("npm")     fail early with a hint
              ├─ ensure_env_file()                 copy .env.example → .env
              ├─ ensure_installed()                uv sync / npm install if missing
              ├─ migrate()                         uv run alembic upgrade head
              ├─ read_env()                        ports come from .env
              │
              ├── child ── uv run uvicorn app.main:app --reload
              │              cwd=backend/  host=IDS_HOST  port=IDS_PORT
              │              stdout piped → [backend] prefix
              │
              └── child ── npm run dev            (no arguments)
                             cwd=frontend/
                             vite reads VITE_DEV_SERVER_HOST / _PORT itself
                             from the repo-root .env via loadEnv
                             stdout piped → [frontend] prefix

   browser  ──▶  http://localhost:5173        Vite dev server (HMR)
                        │
                        └─ /api/*  proxied to VITE_DEV_PROXY_TARGET
                                   (http://127.0.0.1:8000)  ──▶  uvicorn
```

Only the uvicorn child is given `--host` and `--port` on the command line. Vite
gets them from the same repo-root `.env` the backend reads, because
`frontend/vite.config.ts` calls `loadEnv(mode, repoRoot, '')` and sets `envDir`
to the repo root — one env file for the whole stack rather than two that drift.
The supervisor reads `VITE_DEV_SERVER_PORT` only to print the dashboard URL. The
config defaults the dev host to `'::'`, which binds both IP stacks: on Windows
Node otherwise resolves the default to `::1` alone and `127.0.0.1` refuses
connections. `strictPort: true` means a busy port fails the start instead of
silently moving to another one, which would leave the printed URL pointing at
nothing. The container `dev` target is the exception — its `CMD` does pass
`--host` and `--port` to `npm run dev`, because a container must bind
`0.0.0.0` or the published port reaches nothing.

Both children are launched in their own process group
(`CREATE_NEW_PROCESS_GROUP` on Windows), because uvicorn's reloader restarting
its worker raises a console control event that would otherwise take the launcher
and the frontend down with it — editing one backend file would kill the
dashboard. The supervisor polls both children and exits as soon as either dies,
so a crashed backend is never hidden behind a still-running frontend, and the
`finally` block terminates and then kills anything still alive.

Because Vite proxies `/api` to the backend, the browser is same-origin in
development and the CORS configuration is not exercised.

### Containers — `make up` / `docker compose up --build`

```
   ┌───────────────────────────────────────────────────────────────┐
   │ docker compose   (project name: recluse)                      │
   │                                                               │
   │  ┌──────────────────────────┐   ┌──────────────────────────┐  │
   │  │ frontend                 │   │ backend                  │  │
   │  │ frontend/Dockerfile      │   │ backend/Dockerfile       │  │
   │  │ target: dev              │   │ uv python3.12-bookworm-  │  │
   │  │                          │   │   slim                   │  │
   │  │ vite dev --host 0.0.0.0  │   │ alembic upgrade head     │  │
   │  │ proxy → backend:8000     │   │   && uvicorn app.main:app│  │
   │  │ :5173                    │   │ :8000                    │  │
   │  └────────────┬─────────────┘   └────────────┬─────────────┘  │
   │               │ depends_on: service_healthy  │                │
   │               └──────────────────────────────┘                │
   │                                              │ healthcheck    │
   │                                  GET /api/v1/health           │
   │                                                               │
   │  ┌─────────────────────────────────────────────────────────┐  │
   │  │ postgres   profile "postgres" — not started by default   │ │
   │  └─────────────────────────────────────────────────────────┘  │
   └───────────────────────────────────────────────────────────────┘
      host ports   ${FRONTEND_PORT:-5173} / ${BACKEND_PORT:-8000}
      host mounts  ./data              → /srv/data
                   ./backend/artifacts → /srv/backend/artifacts
                   ./reports           → /srv/reports
```

The backend image mirrors the repo layout (`/srv/backend`, `/srv/data`) so the
repo-root-relative path resolution in `app/config.py` behaves identically inside
and outside a container. Datasets, the SQLite file, model artifacts and reports
are bind-mounted from the host: they are reproducible outputs, not image
contents. The frontend compose service mounts only `src/`, `index.html` and
`vite.config.ts` — mounting the whole directory would shadow the image's
`node_modules` with the host's and break platform-specific binaries.

The production frontend image is a different target in the same Dockerfile:

```
   frontend/Dockerfile
     deps  ── node:24-alpine, npm ci
       ├─ dev    ── vite dev server               (what docker compose runs)
       └─ build  ── npm run build → dist/

     serve ── nginx:1.29-alpine                   (a separate base, not from deps)
                COPY --from=build dist → /usr/share/nginx/html
                COPY frontend/nginx.conf → conf.d/default.conf

   browser ──▶ nginx :80
                 ├─ location /       try_files $uri $uri/ /index.html   (SPA)
                 └─ location /api/   proxy_pass http://backend:8000
                                     proxy_buffering off
                                     proxy_cache off
                                     proxy_read_timeout 24h      ← SSE feed
```

The `serve` target is wired for Phase 8 packaging. It carries no node and no
`node_modules`: it starts from `nginx:1.29-alpine` rather than from `deps`, and
copies only the built `dist/` out of the `build` stage, so the shipped image is
static files plus a proxy config. The `proxy_buffering off` and
24-hour read timeout in `nginx.conf` exist specifically so the `/api/v1/stream`
server-sent-event feed is neither buffered nor cut off by the proxy. See
[Code: Infrastructure](Code-Infrastructure.md).

---

## Data stores

Two stores, with different lifetimes and different rules.

### Relational: SQLite today, Postgres-shaped throughout

`app/db.py` builds one engine from `settings.sqlalchemy_url` with
`pool_pre_ping=True`. For SQLite it passes `check_same_thread: False`, because a
single file is touched by request handlers and by the replay background task on
different threads, and it registers a `connect` listener that issues
`PRAGMA foreign_keys=ON` (SQLite ignores foreign keys unless asked, Postgres
always enforces them, so behaviour is matched) and `PRAGMA journal_mode=WAL` (so
the replay writer does not block dashboard readers).

Schema changes go through Alembic. `alembic/env.py` takes the URL from
pydantic-settings rather than from `alembic.ini`, so a migration can never run
against a different database than the application. `app/db.py` pins a
`NAMING_CONVENTION` on the shared `MetaData`, because without deterministic
constraint names autogenerate emits unnamed constraints that SQLite cannot later
ALTER and Postgres names differently — migrations then diverge per backend.

Portability is a rule, not an aspiration:

| Concern | Rule in `app/models.py` |
| --- | --- |
| Autoincrement keys | `PK = BigInteger().with_variant(Integer, "sqlite")`, because SQLite only auto-assigns rowids for `INTEGER PRIMARY KEY` |
| Enumerations | `String` plus a `CheckConstraint`, never a native database enum, so widening the allowed set stays an ordinary migration |
| Timestamps | `DateTime(timezone=True)`; SQLite stores them naively, so the application layer always hands over tz-aware values |
| Structured payloads | the generic `JSON` type, which maps to `json` on Postgres and `TEXT` on SQLite |

`backend/tests/test_schema_portability.py` compiles every column type against
both the SQLite and Postgres dialects, so a SQLite-only type fails in the test
suite rather than on the day of the swap. See [Testing](Testing.md).

**What swapping to Postgres takes.** Point `IDS_DATABASE_URL` at
`postgresql+psycopg://ids:ids@localhost:5432/ids`, start the database, run
`alembic upgrade head`. `docker-compose.yml` carries a `postgres` profile
(`postgres:17-alpine`, not started by default) for exactly this rehearsal. No
model, query or migration changes. The SQLite-only pragmas in `_build_engine()`
are already guarded by a backend check, and `settings.sqlalchemy_url` rewrites
the database path only for SQLite URLs. See
[Database Schema](Database-Schema.md).

### Artifacts on disk: `backend/artifacts/`

Everything under `backend/artifacts/` is produced by `backend/training/` on the
machine that runs it, and is gitignored — reproducible output, not source.

| File | Written by | Phase | Read by |
| --- | --- | --- | --- |
| `preprocessing.pkl` | `split.py` / `features.py` | 1 | `ModelBundle.load()` |
| `supervised_model.pkl` | `train_supervised.py` | 2 | `ModelBundle._load_models()` |
| `autoencoder.pt` | `train_autoencoder.py` | 3 | `ModelBundle._load_models()` |
| `model_card.json` | `evaluate.py` | 2/3 | `ModelBundle._load_model_card()` |

The scaler, the feature order, the dropped columns, the port encoding and the
`sha256:` schema hash travel together in one `preprocessing.pkl` bundle because
none of them alone reproduces the training-time feature matrix.

Trust boundary: nothing under this directory is ever loaded from an untrusted
source. The API has no artifact-upload path, and Torch weights are read with
`weights_only=True`, so `autoencoder.pt` is data rather than code. The
scikit-learn and LightGBM estimators have no non-pickle round-trip, so
`supervised_model.pkl` is unpickled as a first-party file.

Two more on-disk locations: `data/` holds `raw/`, `interim/` and `processed/`
plus the SQLite file, and `reports/` holds `loao.md` and its siblings from Phase
4. All three roots are configurable (`IDS_DATA_DIR`, `IDS_ARTIFACTS_DIR`,
`IDS_REPORTS_DIR`) and are created at startup by
`settings.ensure_directories()`.

---

## Design decisions and their reasons

| Decision | Alternative rejected | Why |
| --- | --- | --- |
| SSE for the live alert feed | WebSockets | The feed is one-directional. `EventSource` is built into the browser, FastAPI does SSE in about ten lines, and there is no reconnect logic to write. WebSockets buy nothing here and add a protocol upgrade for the nginx proxy to get right. |
| Batch scoring — `score_batch(flows: list[dict])` | per-row `predict()` in the replay loop | Roughly 50x slower per row, and the cost lands on the visible path: the accelerated replay is what makes the demo look alive, and per-row scoring makes it stutter. The signature takes a list so there is no single-row entry point to reach for. |
| Offline training in `backend/training/` | training or fitting inside a request handler | A handler that trains blocks a worker for minutes, has no reproducible inputs, and produces a model nobody can point at. Training is batch; the API loads artifacts once in the lifespan, and `app/inference.py` never calls `.fit()`. |
| One shared feature module | a serving-side reimplementation of the transforms | Train/serve skew is silent: feeding columns in a different order than training produces garbage scores without raising anything. One module plus a persisted `schema_hash` that the service recomputes at startup turns a silent wrong answer into a refusal to boot. |
| Static remediation lookup in `app/remediation.py` | generated per-alert remediation text | A fixed, reviewed playbook is something a SOC can trust. Advice improvised per alert has to be re-verified every time, which defeats the purpose of having it. For `UNCLASSIFIED_ANOMALY` the honest entry is that no playbook exists yet and the alert routes to manual investigation — stated rather than guessed. |
| 501 stubs with a machine-readable body | mock or sample data behind the unbuilt endpoints | Mock data makes "not built yet" indistinguishable from "built and broken", and it survives into demos. The `NotImplementedResponse` body names the endpoint and the phase that implements it, the 501 is declared in the OpenAPI schema, and `backend/tests/test_api_surface.py` asserts none of the stubs fabricates data. |
| Full v1 route surface registered in Phase 0 | adding routes as each phase implements them | The OpenAPI schema — and therefore the generated `frontend/src/types/api.d.ts` — is complete from the start, so the frontend can be typed against endpoints before they return anything. |
| `RobustScaler` | `StandardScaler` | Network flow features are extremely heavy-tailed. A handful of enormous flows dominate the mean and standard deviation and flatten every other value; the median and IQR that `RobustScaler` uses do not move. |
| Parquet for `data/interim` and `data/processed` | CSV | Roughly ten times faster to reload, and it preserves dtypes, so a cleaned numeric column does not come back as `object` and silently change a transform. |
| Temporal splits by capture day | `train_test_split(shuffle=True)` | CICIDS2017 flow records are heavily duplicated and correlated. Random splitting leaks near-identical rows across train and test and manufactures fake 99.9% scores. |
| Two models, sequenced | one multi-class model with an "unknown" class | An "unknown" class has to be trained on examples of unknown traffic, which is a contradiction. The benign-only autoencoder never sees an attack label, which is what makes novel-attack detection a measurable claim. |
| `class_weight='balanced'` | SMOTE oversampling | Synthetic interpolation between flow records produces packets that could not exist on a real network, and applying it before the split inflates test scores outright. |
| TreeSHAP for Stage 1, per-feature reconstruction error for Stage 2 | KernelSHAP for both | KernelSHAP on a neural net is slow and approximated. The autoencoder already computes `(x - x_hat) ** 2` per feature, and the features it failed hardest to reconstruct are literally why the row looks anomalous — faster and more faithful at once. |
| Threshold from a false-positive budget | `tau_sup = 0.5`, or argmax | At 1,000,000 flows per day, a 0.1% false-positive rate is 1,000 false alerts a day. The threshold is derived from analyst capacity instead, and the service logs the resulting budget at startup. |
| Alerts deduplicated on `(src_host, alert_class, floor(ts, window))` | one row per scored flow | One compromised host producing 5,000 flows is one incident. Without dedupe the queue is unusable within thirty seconds of a replay. |
| `String` + `CheckConstraint` for enumerations | native database enum types | Native enums differ between SQLite and Postgres and make adding a value a type migration. Check constraints are portable and stay an ordinary migration. |
| TypeScript API types generated from OpenAPI | hand-written client types | A hand-written type drifts from the server the first time a field is renamed. `npm run gen:types` regenerates `frontend/src/types/api.d.ts` from the live schema, so drift becomes a compile error. |
| `IDS_ALLOW_AUTO_BLOCK` exists and is rejected when true | omitting the flag entirely | An absent feature is invisible. A flag that is greppable, documented, and raises at startup when set to true both states the constraint and enforces it. `backend/tests/test_config.py` asserts the rejection, and `backend/tests/test_api_surface.py` asserts no route path contains `block`, `drop` or `quarantine`. |

See [Anti-Patterns](Anti-Patterns.md) for the failure modes these decisions
defend against, and [Testing](Testing.md) for which of them are asserted by a
test rather than by convention.
