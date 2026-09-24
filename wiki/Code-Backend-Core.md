# Code Reference — Backend Core

This page documents the seven modules that make up the core of the Recluse serving layer: the package marker, settings, the FastAPI application factory, the database layer, the ORM models, the wire contracts, and the model bundle loader. It is written for anyone modifying the backend, reviewing a change against it, or trying to work out why the API reports `model_version: "unloaded"`. It reflects the repository at the end of Phase 0 — the scaffold is complete and runnable, no model has been trained, and the scoring path is an explicit stub.

| File | Lines | Role |
| --- | --- | --- |
| `backend/app/__init__.py` | 5 | Package marker and single source of the API version string. |
| `backend/app/config.py` | 158 | Environment-driven settings, derived paths, and the false-positive budget. |
| `backend/app/main.py` | 128 | Application factory, lifespan startup, CORS, router mounting, `/health`. |
| `backend/app/db.py` | 99 | Engine, session factory, declarative base, naming convention, SQLite pragmas. |
| `backend/app/models.py` | 250 | SQLAlchemy models for alerts, analyst verdicts and the model registry. |
| `backend/app/schemas.py` | 72 | Pydantic wire contracts and the shared `Literal` vocabularies. |
| `backend/app/inference.py` | 214 | Model bundle dataclass, artifact loading, schema-hash verification. |

---

## backend/app/\_\_init\_\_.py

**Path:** `backend/app/__init__.py` — declares the serving layer as a Python package and holds the version string that the OpenAPI document advertises.

### What it does

The file exists for two reasons. First, `backend/app` has to be an importable package so that every other module can use absolute imports (`from app.config import settings`) rather than relative path games; the backend is run with `backend/` on the import path. Second, it owns `__version__`, which [main.py](#backendappmainpy) passes straight into the `FastAPI(version=...)` constructor. Keeping the number in one place means every part of the Python code that speaks about the API version — the OpenAPI document, `/docs` — reads the same literal.

`__version__` is the *API* version, not the model version. The two are deliberately separate: the API contract can change without retraining, and a retrain produces a new `model_version` without touching the API. `/api/v1/health` reports the model version from the loaded bundle, never from here.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `__all__` | Constant | `__all__ = ["__version__"]` | Restricts `from app import *` to the version string. |
| `__version__` | Constant | `__version__ = "0.1.0"` | API version, consumed by `create_app()` for the OpenAPI document. |

### Notes

- The module docstring reads "Recluse serving layer — FastAPI application package." It marks this tree as the serving half of the repository, distinct from `backend/training/`.
- Invariant: `__version__` is the only version literal in the backend's Python code, and `app/main.py` is its only consumer — `FastAPI(version=__version__)` at line 108. It is not the only version string in the backend: `backend/pyproject.toml:3` carries the same `0.1.0` as the distribution version and has to be bumped alongside it, or a release ships a package whose metadata disagrees with its own `/openapi.json`.
- Status: **implemented.**

---

## backend/app/config.py

**Path:** `backend/app/config.py` — a single `pydantic-settings` class that reads every tunable from the environment and derives the paths, CORS list and false-positive budget the rest of the system depends on.

### What it does

Every path, database URL and threshold budget in Recluse arrives through this module, as do the bind address and port — though those last two are read by the container's uvicorn command line rather than by any Python module, which the notes below spell out. Nothing downstream hardcodes a location, so the same code runs from a shell in the repo root, from a `docker compose` container with absolute paths, and against Postgres instead of SQLite, with no source change. The class sets `env_prefix="IDS_"`, so the field `database_url` is read from `IDS_DATABASE_URL`, `log_level` from `IDS_LOG_LEVEL`, and so on. Values also load from a `.env` file at the repository root; `.env.example` is the checked-in template.

Two structural decisions are worth calling out. `protected_namespaces=()` is set because Pydantic v2 reserves the `model_` prefix and this project legitimately exposes `model_*` names as part of its contract. And `cors_origins` is typed `str`, not `list[str]`, because `pydantic-settings` would otherwise demand a JSON array in the env file for a list-typed field; the comma-separated string is split by the derived `cors_origin_list` property instead, which is what a `.env` file can express comfortably.

The module also encodes a product constraint as a validator. `allow_auto_block` is present so the constraint is explicit and greppable rather than merely absent, and a `field_validator` raises if it is ever set true. There is no serving code path that drops traffic, and setting the flag to true is rejected at startup rather than silently ignored.

The false-positive budget is arithmetic, not a guess. The code computes it exactly as:

```python
max_alerts_per_day = analyst_capacity_per_hour * analyst_shift_hours
target_fpr         = max_alerts_per_day / expected_daily_flow_volume
```

With the shipped defaults — 40 alerts triaged per analyst-hour, an 8-hour shift, and 1,000,000 flows per day — that is `40 * 8 = 320` alerts per day, and `320 / 1_000_000 = 0.00032`, a target FPR of `3.2e-4`. Phase 2 picks `tau_sup` as the smallest threshold whose measured false-positive rate stays under that number, instead of defaulting to 0.5. The startup log line in [main.py](#backendappmainpy) prints all three numbers so the budget is visible at boot.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `BACKEND_DIR` | Constant | `BACKEND_DIR: Path = Path(__file__).resolve().parents[1]` | Absolute path to `backend/`, derived from this file's location. |
| `REPO_ROOT` | Constant | `REPO_ROOT: Path = BACKEND_DIR.parent` | Absolute repository root; the anchor for every relative path. |
| `_resolve` | Function | `_resolve(raw: str \| Path) -> Path` | Expands `~`, returns absolute paths unchanged, resolves relative ones against `REPO_ROOT`. |
| `Settings` | Class | `class Settings(BaseSettings)` | The settings model; all fields below are its attributes. |
| `Settings.model_config` | Class attribute | `SettingsConfigDict(env_prefix="IDS_", env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore", protected_namespaces=())` | Env prefix, `.env` location, unknown keys ignored, `model_*` names permitted. |
| `Settings._reject_auto_block` | Validator | `@field_validator("allow_auto_block") def _reject_auto_block(cls, value: bool) -> bool` | Raises `ValueError` if `IDS_ALLOW_AUTO_BLOCK` is enabled. |
| `Settings.cors_origin_list` | Computed property | `cors_origin_list -> list[str]` | Splits `cors_origins` on commas, strips whitespace, drops empties. |
| `Settings.data_path` | Computed property | `data_path -> Path` | `data_dir` resolved to an absolute path. |
| `Settings.artifacts_path` | Computed property | `artifacts_path -> Path` | `artifacts_dir` resolved; passed to `load_bundle()` at startup. |
| `Settings.reports_path` | Computed property | `reports_path -> Path` | `reports_dir` resolved. |
| `Settings.max_alerts_per_day` | Computed property | `max_alerts_per_day -> int` | `analyst_capacity_per_hour * analyst_shift_hours`; the numerator of the FP budget. |
| `Settings.target_fpr` | Computed property | `target_fpr -> float` | `max_alerts_per_day / expected_daily_flow_volume`; the FPR the supervised threshold must respect. |
| `Settings.sqlalchemy_url` | Computed property | `sqlalchemy_url -> str` | `database_url` with a relative SQLite path anchored to the repo root and its parent directory created. |
| `Settings.ensure_directories` | Method | `ensure_directories(self) -> None` | Creates `data_path`, `artifacts_path`, `reports_path`. Idempotent. |
| `get_settings` | Function | `@lru_cache def get_settings() -> Settings` | Process-wide cached settings factory, so the environment is read once. |
| `settings` | Constant | `settings = get_settings()` | The module-level singleton every other module imports. |

### Settings fields

| Field | Type | Default | Environment variable | Controls |
| --- | --- | --- | --- | --- |
| `app_name` | `str` | `"Recluse"` | `IDS_APP_NAME` | Name used in the OpenAPI title (`f"{app_name} API"`) and in startup/shutdown log lines. |
| `env` | `Literal["development", "staging", "production"]` | `"development"` | `IDS_ENV` | Deployment environment; logged at startup. Any other value fails validation. |
| `log_level` | `Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]` | `"INFO"` | `IDS_LOG_LEVEL` | Root logging level applied by `_configure_logging()`. |
| `host` | `str` | `"127.0.0.1"` | `IDS_HOST` | Bind address the container CMD passes to uvicorn; not read by any Python module. |
| `port` | `int` | `8000` | `IDS_PORT` | Bind port the container CMD passes to uvicorn; not read by any Python module. |
| `api_v1_prefix` | `str` | `"/api/v1"` | `IDS_API_V1_PREFIX` | Prefix under which both the health router and the main API router are mounted. |
| `database_url` | `str` | `"sqlite+pysqlite:///data/ids.db"` | `IDS_DATABASE_URL` | Raw SQLAlchemy URL. Swapping to `postgresql+psycopg://...` is this value only. |
| `db_echo` | `bool` | `False` | `IDS_DB_ECHO` | Passed to `create_engine(echo=...)`; logs emitted SQL when true. |
| `data_dir` | `Path` | `Path("data")` | `IDS_DATA_DIR` | Datasets and the SQLite file. Relative values resolve against the repo root. |
| `artifacts_dir` | `Path` | `Path("backend/artifacts")` | `IDS_ARTIFACTS_DIR` | Where training writes the model bundle and where `load_bundle()` reads it. |
| `reports_dir` | `Path` | `Path("reports")` | `IDS_REPORTS_DIR` | Evaluation reports and plots produced by the training phases. |
| `cors_origins` | `str` | `"http://localhost:5173,http://127.0.0.1:5173"` | `IDS_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API. Parsed by `cors_origin_list`. |
| `expected_daily_flow_volume` | `int` (`gt=0`) | `1_000_000` | `IDS_EXPECTED_DAILY_FLOW_VOLUME` | Denominator of `target_fpr`: flows the sensor is expected to see per day. |
| `analyst_capacity_per_hour` | `int` (`gt=0`) | `40` | `IDS_ANALYST_CAPACITY_PER_HOUR` | Alerts one analyst can triage per hour. |
| `analyst_shift_hours` | `int` (`gt=0`) | `8` | `IDS_ANALYST_SHIFT_HOURS` | Length of an analyst shift in hours. |
| `dedupe_window_seconds` | `int` (`gt=0`) | `300` | `IDS_DEDUPE_WINDOW_SECONDS` | Bucket width for the dedupe key `(src_host, alert_class, floor(ts, window))`. Read today by `app/dedupe.py:25` inside `dedupe_key()`, the one alert-pipeline function with a working body — see [Code Reference — Alert Pipeline Modules](Code-Backend-Pipeline). |
| `allow_auto_block` | `bool` | `False` | `IDS_ALLOW_AUTO_BLOCK` | Must stay false. Setting it true raises at settings construction, which means at startup. |

### Notes

- `_resolve()` is why `IDS_DATA_DIR=data` keeps its meaning no matter which directory the process was started from, while absolute paths — what the container passes in — are honoured verbatim.
- `sqlalchemy_url` does more than rewrite a string: for a non-memory SQLite database it resolves the file path and calls `db_path.parent.mkdir(parents=True, exist_ok=True)`, so a first run creates `data/` before SQLAlchemy tries to open the file. A bare `sqlite:///data/ids.db` would otherwise resolve against the working directory and silently produce a different database depending on where uvicorn was launched.
- `get_settings()` is `lru_cache`-decorated, so the environment is read once per process. The cache wraps the factory, not the class, so a test that needs different values just constructs a fresh `Settings(...)` — which is what seven of the eight cases in `backend/tests/test_config.py` do (the eighth asserts on the module singleton) — rather than clearing the cache or mutating the singleton. `get_settings.cache_clear()` appears nowhere in the repository.
- `settings = get_settings()` executes at import time, so an invalid value — `IDS_ENV=prod`, or `IDS_ALLOW_AUTO_BLOCK=true` — raises a `ValidationError` while `app.config` is being imported, before FastAPI is constructed. The singleton is imported by `app/db.py`, `app/main.py`, `app/dedupe.py`, `backend/alembic/env.py` and `backend/tests/conftest.py`. `alembic/env.py:22` calls `config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)` so a migration can never run against a different database than the application — see [Code Reference — Backend Migrations](Code-Backend-Migrations).
- `host` and `port` are declared here so the values are validated and documented in one place, but nothing in `backend/app/` reads them. `backend/Dockerfile`'s CMD expands `${IDS_HOST:-0.0.0.0}` and `${IDS_PORT:-8000}` into the uvicorn command line, and `docker-compose.yml` sets both for the container. `make dev` runs `uvicorn app.main:app --reload` with uvicorn's own defaults, so changing `IDS_PORT` does not move the local dev server — pass `--port` yourself.
- `extra="ignore"` means unrelated `IDS_`-prefixed variables in the environment do not break startup.
- The `allow_auto_block` validator is the enforcement point for the project's core constraint: the system alerts, ranks and explains; it never drops traffic, and containment actions are manual and confirmed by an analyst. `backend/tests/test_config.py::test_auto_block_cannot_be_enabled` asserts that `Settings(allow_auto_block=True)` raises and that the message contains "never drops traffic". See [Anti-Patterns](Anti-Patterns).
- Status: **implemented.** The FP-budget properties are live now; the Phase 2 threshold selection that consumes `target_fpr` is not yet written. The arithmetic is pinned by `backend/tests/test_config.py::test_false_positive_budget_arithmetic`, which asserts `max_alerts_per_day == 320` and `target_fpr == 3.2e-4` on the shipped defaults — see [Code Reference — Backend Tests](Code-Backend-Tests).

---

## backend/app/main.py

**Path:** `backend/app/main.py` — the FastAPI application factory, the lifespan startup sequence, and the only endpoint that is fully implemented in Phase 0.

### What it does

This module builds the ASGI application. `create_app()` constructs `FastAPI` with the title, description and version, attaches CORS middleware when origins are configured, mounts the health router and the main API router under `settings.api_v1_prefix`, and returns the app. A module-level `app = create_app()` is what `uvicorn app.main:app` imports.

The important part is the lifespan. Model artifacts are loaded exactly once, at startup, and parked on `app.state.bundle`. No request handler ever trains, fits, or reloads a model. Loading in the lifespan is not merely an optimisation: it is what makes a bad bundle a boot failure. `load_bundle()` verifies that the persisted schema hash matches a hash recomputed from the bundle's own feature order, and raises `SchemaHashMismatch` if it does not. Raising inside the lifespan aborts startup. The alternative — catching it and serving anyway — would mean every request afterwards produced silently wrong scores, because feeding columns to a model in a different order than it was trained on does not raise anything; it just returns garbage. The source says so directly: `# A SchemaHashMismatch raised here is intentionally fatal.`

The startup sequence, in order:

1. `_configure_logging()` — calls `logging.basicConfig` with `settings.log_level` and the format `"%(asctime)s %(levelname)-8s %(name)s | %(message)s"`.
2. `settings.ensure_directories()` — creates `data_path`, `artifacts_path` and `reports_path` so nothing downstream has to guard against a missing directory.
3. `app.state.started_at = time.monotonic()` — the baseline for the `uptime_s` field of `/health`. Monotonic, so a wall-clock adjustment cannot make uptime go backwards.
4. `bundle = load_bundle(settings.artifacts_path)` — reads the artifact bundle and verifies its schema hash. In Phase 0 there is no `preprocessing.pkl`, so this returns an unloaded bundle, which is expected rather than an error.
5. `app.state.bundle = bundle` — the single process-wide handle to the models.
6. Two log lines: one reporting app name, environment, database backend (the scheme only, extracted with `settings.sqlalchemy_url.split("://", 1)[0]`, so no credentials are logged) and `bundle.version`; one reporting the budget as `"false-positive budget: %d alerts/day over %d flows/day -> target FPR %.2e"`.
7. `yield` — the application serves. On shutdown the `finally` block logs `"%s shutting down"`.

CORS is added only when `settings.cors_origin_list` is non-empty. It allows credentials, the methods `GET`, `POST`, `PATCH`, `DELETE`, `OPTIONS`, and all headers. `PUT` is absent — the API's mutations are partial updates rather than whole-resource replacements. Two routers are mounted, both under the v1 prefix: `health_router`, defined in this file and tagged `system`, and `api_router` from `app.routes`, which carries the alerts, score, metrics, analytics, replay and stream routers.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `logger` | Constant | `logger = logging.getLogger("recluse")` | The application logger used for startup and shutdown lines. |
| `API_DESCRIPTION` | Constant | `API_DESCRIPTION: str` | Markdown description rendered at `/docs`; states the two-stage design and that the system never blocks traffic. |
| `_configure_logging` | Function | `_configure_logging() -> None` | Applies `settings.log_level` and the shared log format via `logging.basicConfig`. |
| `lifespan` | Async context manager | `@asynccontextmanager async def lifespan(app: FastAPI) -> AsyncIterator[None]` | Startup and shutdown: logging, directories, uptime baseline, bundle load, budget log. |
| `health_router` | Constant | `health_router = APIRouter(tags=["system"])` | Router carrying the single implemented endpoint. |
| `health` | Route handler | `@health_router.get("/health", response_model=HealthResponse) def health(request: Request) -> HealthResponse` | `GET /api/v1/health` — returns the loaded bundle's status and version plus process uptime. |
| `create_app` | Function | `create_app() -> FastAPI` | Builds the app, adds CORS, mounts both routers under `settings.api_v1_prefix`. |
| `app` | Constant | `app = create_app()` | Module-level ASGI application object. |

### The health endpoint

`GET /api/v1/health` returns HTTP 200 with a [`HealthResponse`](#backendappschemaspy) built from live state, not constants:

```python
bundle: ModelBundle = request.app.state.bundle
started_at: float = request.app.state.started_at
return HealthResponse(
    status=bundle.status,
    model_version=bundle.version,
    uptime_s=round(time.monotonic() - started_at, 3),
)
```

In Phase 0 this answers `{"status": "ok", "model_version": "unloaded", "uptime_s": ...}`. `model_version` stays `"unloaded"` until Phase 2 produces an artifact bundle. That is the honest answer for a scaffold, and it is what the dashboard renders — see [Frontend Screens](Frontend-Screens).

### Notes

- `app.state` is the only place the bundle lives. Loading it anywhere else — lazily in a handler, or per request — would reintroduce the train/serve skew the startup check exists to catch.
- The database scheme is logged, never the full URL, so a Postgres password in `IDS_DATABASE_URL` does not land in the log.
- `uptime_s` uses `time.monotonic()` at both ends and is rounded to three decimal places.
- Interactive docs are served at `/docs` and the schema at `/openapi.json`; the frontend's TypeScript types are generated from that schema.
- `create_app()` loads nothing. `app.state.bundle` and `app.state.started_at` are set by the lifespan, so a `TestClient(create_app())` used *without* its context manager raises `AttributeError` on the first `/health` request — `backend/tests/conftest.py` enters the context manager for exactly this reason, while `test_api_surface.py` calls bare `create_app()` only to read `.openapi()`, which needs no startup. Each `create_app()` call builds a fresh application object but shares the module-level `settings` singleton.
- The wiring is short: this module imports `__version__` from `app`, `settings` from `app.config`, `ModelBundle` and `load_bundle` from `app.inference`, `api_router` from `app.routes` and `HealthResponse` from `app.schemas` — and nothing else from the project. Notably it does **not** import `app.db` or `app.models`, so `uvicorn app.main:app` never builds a database engine. Nothing imports `main.py` in turn except the ASGI server and `backend/tests/conftest.py`.
- The `/health` contract is regression-tested: `backend/tests/test_health.py` asserts the response body is exactly `{status, model_version, uptime_s}`, that a process with no artifacts reports `status: "ok"` and `model_version: "unloaded"`, that `uptime_s` is non-negative and non-decreasing, and that an unprefixed `GET /health` 404s because the prefix is configuration — see [Code Reference — Backend Tests](Code-Backend-Tests).
- Status: **implemented.** Every non-health endpoint reachable through `api_router` currently answers HTTP 501 with a machine-readable body — see [Code Reference — Backend Routes](Code-Backend-Routes) and [API Reference](API-Reference).

---

## backend/app/db.py

**Path:** `backend/app/db.py` — creates the SQLAlchemy engine, the session factory and the declarative base, and applies the SQLite pragmas needed to make SQLite behave like Postgres.

### What it does

SQLite is the development default, and the models deliberately avoid every SQLite-only construct so that pointing `IDS_DATABASE_URL` at Postgres is a configuration change and nothing more. This module is where that portability is set up. `Base` carries a `MetaData` built with an explicit `NAMING_CONVENTION`, which gives every index, unique constraint, check constraint, foreign key and primary key a deterministic name. Without these, Alembic autogenerate emits unnamed constraints that SQLite cannot later `ALTER` and that Postgres names differently, which makes migrations diverge per backend.

`_build_engine()` reads `settings.sqlalchemy_url` — already anchored by [config.py](#backendappconfigpy) — sets `pool_pre_ping=True` so a stale pooled connection is detected rather than surfacing as a request error, passes `echo=settings.db_echo`, and sets `future=True`. For SQLite it adds `connect_args={"check_same_thread": False}`, because a single SQLite file will be touched both by request handlers and by the replay background task, which live on different threads — the setting is there ahead of the Phase 5 consumers that need it, since neither exists yet.

Two pragmas are applied on every SQLite connection through a `connect` event listener. `PRAGMA foreign_keys=ON` is required because SQLite ignores foreign keys unless asked while Postgres always enforces them — turning it on makes behaviour match, which matters for the `ON DELETE CASCADE` between alerts and verdicts. `PRAGMA journal_mode=WAL` keeps the replay writer from blocking dashboard readers.

Sessions come from `SessionLocal`, configured with `autoflush=False`, `autocommit=False` and `expire_on_commit=False`. The last one means ORM objects remain readable after a commit, which is what lets a handler commit and then serialise the same object into a response. There are two ways to get a session: `get_session()`, the FastAPI dependency intended for use with `Depends`, which yields a request-scoped session and always closes it; and `session_scope()`, a context manager for scripts and background tasks that commits on success, rolls back on any exception, re-raises, and closes in a `finally`.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `NAMING_CONVENTION` | Constant | `NAMING_CONVENTION: dict[str, str]` | Deterministic names for `ix`, `uq`, `ck`, `fk` and `pk` constraints, so migrations are identical on SQLite and Postgres. |
| `metadata` | Constant | `metadata = MetaData(naming_convention=NAMING_CONVENTION)` | The shared metadata object; Alembic targets it for autogenerate. |
| `Base` | Class | `class Base(DeclarativeBase)` | Declarative base carrying the shared naming convention; every model in `app/models.py` subclasses it. |
| `_build_engine` | Function | `_build_engine() -> Engine` | Builds the engine, sets `pool_pre_ping`, applies SQLite-specific connect args and pragmas. |
| `engine` | Constant | `engine: Engine = _build_engine()` | The process-wide engine. |
| `SessionLocal` | Constant | `SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)` | Session factory used by both accessors below. |
| `get_session` | Function | `get_session() -> Generator[Session, None, None]` | FastAPI dependency yielding a request-scoped session; closes it in `finally`. |
| `session_scope` | Context manager | `@contextmanager def session_scope() -> Iterator[Session]` | Transactional scope for scripts and background tasks: commit, rollback on exception, always close. |

The naming convention, verbatim:

```python
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
```

### Notes

- `get_session()` does not commit. A handler that writes is responsible for its own `session.commit()`; the dependency only guarantees the session is closed.
- `session_scope()` does commit, and rolls back before re-raising. Use it outside the request cycle only.
- The SQLite branch is decided by `url.startswith("sqlite")` on the already-resolved URL, so the pragmas and `check_same_thread` never apply to Postgres.
- `pool_pre_ping=True` costs one cheap round trip per checkout and removes the class of failures where a connection dropped by the database is handed to a request.
- Neither accessor has a caller yet. No handler declares `Depends(get_session)` and nothing calls `session_scope()`; both are wired up in Phase 5 (backend API), when the first route reads or writes an alert. `backend/pyproject.toml:58` already carries `ignore = ["B008"]` with the comment "FastAPI Depends() in defaults is the documented idiom", in anticipation.
- Nothing in the running API imports this module yet. `uvicorn app.main:app` pulls in `app.config`, `app.inference`, `app.routes` and `app.schemas` only, so `engine` is never constructed in the serving process in Phase 0. Its importers today are `app/models.py` (for `Base`), `backend/alembic/env.py`, and two test modules — `backend/tests/conftest.py` and `backend/tests/test_schema_portability.py`. The first import from a request path arrives in Phase 5.
- This module imports `settings` from `app.config` and nothing else from the project, which is what keeps it importable from Alembic and from tests without dragging in FastAPI.
- Status: **implemented.**

---

## backend/app/models.py

**Path:** `backend/app/models.py` — the SQLAlchemy ORM models: one deduplicated alert, one analyst verdict, one model registry entry.

### What it does

This module defines the persistent shape of the system. Three tables: `alerts` holds one row per deduplicated detection, `analyst_verdicts` holds the human judgements that feed active learning, and `model_versions` is the registry of what was trained, when, and what it scored.

Four portability rules are enforced throughout, stated in the module docstring as Phase 0's "Postgres-compatible types only" requirement. Autoincrement keys use `PK = BigInteger().with_variant(Integer, "sqlite")`, because SQLite only auto-assigns rowids for an `INTEGER PRIMARY KEY`. Enumerations are `String` plus a `CheckConstraint` rather than native database enums — portable, and changing the allowed set stays an ordinary migration. Timestamps are `DateTime(timezone=True)`; SQLite stores them naively, so the application layer is responsible for always handing over timezone-aware values. Structured payloads use the generic `JSON` type, which maps to `json` on Postgres and `TEXT` on SQLite.

The controlled vocabularies are module-level tuples — `ALERT_KINDS`, `ALERT_FAMILIES`, `SEVERITIES`, `ALERT_STATUSES`, `VERDICTS`, `MODEL_STAGES`, `ALERT_SOURCES`, `DETECTION_STAGES` — and `_one_of()` renders them into a portable `col IN (...)` expression for the check constraints. The same vocabularies are mirrored as `Literal` types in [schemas.py](#backendappschemaspy), which is the wire contract; the two are kept in step by hand.

The indexes on `alerts` are not generic. Each one exists for a named query the dashboard actually runs: the dedupe lookup, the risk-ordered triage queue, the "other alerts from this source in the last 24h" panel, and the one-click unclassified-anomaly filter chip. Note that the queue is ordered by `risk_score`, never by timestamp — analysts work by risk.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `PK` | Constant | `PK = BigInteger().with_variant(Integer, "sqlite")` | Portable autoincrementing primary key type used by all three tables. |
| `ALERT_KINDS` | Constant | `("KNOWN", "UNCLASSIFIED_ANOMALY")` | Allowed values of `alerts.kind`. |
| `ALERT_FAMILIES` | Constant | `("dos", "ddos", "brute_force", "port_scan", "web_attack", "botnet", "infiltration")` | Attack families Stage 1 can name. |
| `SEVERITIES` | Constant | `("low", "medium", "high", "critical")` | Allowed values of `alerts.severity`. |
| `ALERT_STATUSES` | Constant | `("open", "in_review", "closed", "dismissed")` | Allowed values of `alerts.status`. |
| `VERDICTS` | Constant | `("TP", "FP", "UNSURE")` | Allowed values of `analyst_verdicts.verdict`. |
| `MODEL_STAGES` | Constant | `("champion", "challenger", "archived")` | Allowed values of `model_versions.stage`. |
| `ALERT_SOURCES` | Constant | `("replay", "live", "api")` | Allowed values of `alerts.source`. |
| `DETECTION_STAGES` | Constant | `("stage1_supervised", "stage2_anomaly")` | Allowed values of `alerts.detection_stage`. |
| `_one_of` | Function | `_one_of(column: str, values: tuple[str, ...]) -> str` | Renders a portable `col IN ('a', 'b')` check expression. |
| `TimestampMixin` | Class | `class TimestampMixin` | Adds `created_at` and `updated_at`, both `DateTime(timezone=True)` with `server_default=func.now()`. |
| `Alert` | SQLAlchemy model | `class Alert(TimestampMixin, Base)` — `__tablename__ = "alerts"` | One deduplicated detection. |
| `Alert.__repr__` | Method | `__repr__(self) -> str` | Debugging aid: `<Alert id= kind= family= risk=>`. |
| `AnalystVerdict` | SQLAlchemy model | `class AnalystVerdict(Base)` — `__tablename__ = "analyst_verdicts"` | An analyst's judgement on an alert; the input to active learning. |
| `ModelVersion` | SQLAlchemy model | `class ModelVersion(TimestampMixin, Base)` — `__tablename__ = "model_versions"` | Registry row for a trained model: algorithms, thresholds, metrics, stage. |

### TimestampMixin columns

| Column | Type | Nullable | Default | Index | FK | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| `created_at` | `DateTime(timezone=True)` | no | `server_default=func.now()` | no | — | Row insertion time, set by the database. |
| `updated_at` | `DateTime(timezone=True)` | no | `server_default=func.now()`, `onupdate=func.now()` | no | — | Last modification time, refreshed on update. |

### Alert columns

A burst of 5,000 flows from one compromised host collapses into a single row here, with `occurrence_count` incremented instead of a new insert.

| Column | Type | Nullable | Default | Index | FK | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `PK` | no | autoincrement | primary key | — | Surrogate key. |
| `kind` | `String(32)` | no | — | composite | — | `KNOWN` or `UNCLASSIFIED_ANOMALY`. |
| `family` | `String(32)` | yes | — | no | — | Named attack family; null for anomaly alerts. |
| `detection_stage` | `String(32)` | no | — | no | — | `stage1_supervised` or `stage2_anomaly`. |
| `severity` | `String(16)` | no | — | no | — | `low`, `medium`, `high` or `critical`. |
| `risk_score` | `Float` | no | — | composite | — | Queue ordering key. Never the timestamp — analysts work by risk. |
| `confidence` | `Float` | yes | — | no | — | Stage 1 maximum attack-class probability; null for pure anomaly alerts. |
| `anomaly_score` | `Float` | yes | — | no | — | Stage 2 mean reconstruction error; null for confident known attacks. |
| `detected_at` | `DateTime(timezone=True)` | no | — | `index=True`, plus composites | — | When the flow was detected. |
| `src_ip` | `String(45)` | no | — | composite | — | Source address. 45 characters is the textual length of an IPv6 address. |
| `src_port` | `Integer` | yes | — | no | — | Source port. |
| `dst_ip` | `String(45)` | no | — | no | — | Destination address. |
| `dst_port` | `Integer` | yes | — | no | — | Destination port. |
| `protocol` | `String(16)` | yes | — | no | — | Transport protocol. |
| `asset_criticality` | `String(16)` | yes | — | no | — | Enrichment: how important the affected asset is. |
| `host_prior_alert_count` | `Integer` | no | `0` | no | — | Enrichment: how many alerts this host already produced. |
| `mitre_technique` | `String(64)` | yes | — | no | — | Enrichment: mapped MITRE ATT&CK technique. |
| `explanation` | `JSON` | yes | — | no | — | Top-5 TreeSHAP contributors (Stage 1) or top-5 per-feature reconstruction errors (Stage 2). |
| `narrative` | `Text` | yes | — | no | — | Human-readable summary of why the alert fired. |
| `recommended_actions` | `JSON` | yes | — | no | — | Suggested analyst next steps. Suggestions only; nothing is executed. |
| `raw_flow` | `JSON` | yes | — | no | — | The originating flow record, for the drill-down view. |
| `dedupe_key` | `String(255)` | no | — | composite | — | `(src_host, alert_class, floor(ts, dedupe_window_seconds))`. |
| `occurrence_count` | `Integer` | no | `1` | no | — | How many flows collapsed into this row. Constrained `>= 1`. |
| `first_seen` | `DateTime(timezone=True)` | no | — | no | — | Earliest flow in the deduplicated group. |
| `last_seen` | `DateTime(timezone=True)` | no | — | composite | — | Most recent flow in the group; also the dedupe window check. |
| `status` | `String(16)` | no | `"open"` | composite | — | Triage state. |
| `model_version` | `String(64)` | no | — | no | — | Which model version produced this alert. Non-negotiable for the Phase 7 audit trail. |
| `source` | `String(16)` | no | `"replay"` | no | — | `replay`, `live` or `api`. |
| `ground_truth_label` | `String(64)` | yes | — | no | — | Dataset label; exists only for replayed rows and is badged demo-only in the UI. Always null for live capture. |
| `created_at`, `updated_at` | from `TimestampMixin` | no | `func.now()` | no | — | See above. |

**Constraints on `alerts`**

| Name | Kind | Expression |
| --- | --- | --- |
| `ck_alerts_kind_valid` (declared as `name="kind_valid"`) | Check | `kind IN ('KNOWN', 'UNCLASSIFIED_ANOMALY')` |
| `ck_alerts_severity_valid` (declared as `name="severity_valid"`) | Check | `severity IN ('low', 'medium', 'high', 'critical')` |
| `ck_alerts_status_valid` (declared as `name="status_valid"`) | Check | `status IN ('open', 'in_review', 'closed', 'dismissed')` |
| `ck_alerts_source_valid` (declared as `name="source_valid"`) | Check | `source IN ('replay', 'live', 'api')` |
| `ck_alerts_detection_stage_valid` (declared as `name="detection_stage_valid"`) | Check | `detection_stage IN ('stage1_supervised', 'stage2_anomaly')` |
| `ck_alerts_occurrence_count_positive` (declared as `name="occurrence_count_positive"`) | Check | `occurrence_count >= 1` |
| `ck_alerts_family_matches_kind` (declared as `name="family_matches_kind"`) | Check | `(kind = 'UNCLASSIFIED_ANOMALY' AND family IS NULL) OR (kind = 'KNOWN' AND family IS NOT NULL)` |

The declared `name=` is a stem: the `ck` rule in `NAMING_CONVENTION` expands it to `ck_<table>_<name>`, which is what appears in the migration and in the database. Grep the schema for `kind_valid` and you will not find it; grep for `ck_alerts_kind_valid` and you will. The unique constraint on `model_versions.version` becomes `uq_model_versions_version` the same way.

**Indexes on `alerts`**

| Name | Columns | Query it serves |
| --- | --- | --- |
| `ix_alerts_detected_at` (implicit, from `index=True`) | `detected_at` | Time-window filters. |
| `ix_alerts_dedupe_key_last_seen` | `dedupe_key`, `last_seen` | Dedupe lookup: one row per key inside the active window. |
| `ix_alerts_status_risk_score` | `status`, `risk_score` | The triage queue: open alerts, highest risk first. |
| `ix_alerts_src_ip_detected_at` | `src_ip`, `detected_at` | "Other alerts from this source in the last 24h". |
| `ix_alerts_kind_detected_at` | `kind`, `detected_at` | The one-click `UNCLASSIFIED_ANOMALY` filter chip. |

**Relationships on `alerts`**

| Name | Target | Configuration |
| --- | --- | --- |
| `verdicts` | `list[AnalystVerdict]` | `back_populates="alert"`, `cascade="all, delete-orphan"`, `passive_deletes=True` — deletion is delegated to the database-level `ON DELETE CASCADE`. |

### AnalystVerdict columns

| Column | Type | Nullable | Default | Index | FK | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `PK` | no | autoincrement | primary key | — | Surrogate key. |
| `alert_id` | `PK` | no | — | `index=True` | `alerts.id` `ON DELETE CASCADE` | The alert being judged. |
| `verdict` | `String(16)` | no | — | composite | — | `TP`, `FP` or `UNSURE`. |
| `note` | `Text` | yes | — | no | — | Free-text analyst rationale. |
| `analyst` | `String(128)` | yes | — | no | — | Who recorded the verdict. |
| `model_version` | `String(64)` | yes | — | no | — | Audit: the model version being judged, captured at verdict time. |
| `consumed_at` | `DateTime(timezone=True)` | yes | — | no | — | Set once a retrain has consumed this label, so the feedback screen can count "new labels since last retrain". |
| `created_at` | `DateTime(timezone=True)` | no | `server_default=func.now()` | composite | — | When the verdict was recorded. |

**Constraints and indexes on `analyst_verdicts`**

| Name | Kind | Expression / columns |
| --- | --- | --- |
| `ck_analyst_verdicts_verdict_valid` (declared as `name="verdict_valid"`) | Check | `verdict IN ('TP', 'FP', 'UNSURE')` |
| `ix_analyst_verdicts_created_at_verdict` | Index | `created_at`, `verdict` |
| `ix_analyst_verdicts_alert_id` (implicit, from `index=True`) | Index | `alert_id` — cascade lookups and "all verdicts on this alert" |
| `fk_analyst_verdicts_alert_id_alerts` | Foreign key | `alert_id` → `alerts.id`, `ON DELETE CASCADE` |

**Relationships on `analyst_verdicts`**

| Name | Target | Configuration |
| --- | --- | --- |
| `alert` | `Alert` | `back_populates="verdicts"` — the inverse of `Alert.verdicts`. |

`AnalystVerdict` does not use `TimestampMixin`: a verdict records a judgement at a point in time, so it carries `created_at` only and has no `updated_at`.

### ModelVersion columns

| Column | Type | Nullable | Default | Index | FK | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| `id` | `PK` | no | autoincrement | primary key | — | Surrogate key. |
| `version` | `String(64)` | no | — | unique | — | Model version identifier. |
| `stage` | `String(16)` | no | `"challenger"` | no | — | `champion`, `challenger` or `archived`. |
| `supervised_algorithm` | `String(64)` | yes | — | no | — | Stage 1 estimator. |
| `anomaly_algorithm` | `String(64)` | yes | — | no | — | Stage 2 architecture. |
| `trained_at` | `DateTime(timezone=True)` | yes | — | no | — | When training finished. |
| `trained_on` | `String(255)` | yes | — | no | — | Dataset identifier or split description. |
| `schema_hash` | `String(80)` | yes | — | no | — | Feature-order hash. Guards train/serve skew: the API refuses to start when the artifact bundle's hash disagrees with the feature module's. |
| `tau_sup` | `Float` | yes | — | no | — | Stage 1 decision threshold selected against `settings.target_fpr`. |
| `tau_anom` | `Float` | yes | — | no | — | Stage 2 reconstruction-error threshold. |
| `metrics` | `JSON` | yes | — | no | — | Recorded evaluation results for this version. |
| `is_active` | `Boolean` | no | `False` | no | — | Whether this version is the one currently served. |
| `notes` | `Text` | yes | — | no | — | Free-text remarks. |
| `created_at`, `updated_at` | from `TimestampMixin` | no | `func.now()` | no | — | See above. |

**Constraints on `model_versions`**

| Name | Kind | Expression |
| --- | --- | --- |
| `ck_model_versions_stage_valid` (declared as `name="stage_valid"`) | Check | `stage IN ('champion', 'challenger', 'archived')` |
| `uq_model_versions_version` | Unique | `version` (declared inline as `unique=True`; named by the `uq` convention) |

`__table_args__` on `ModelVersion` is that single `CheckConstraint` and nothing else.

### Notes

- `ModelVersion` backs `GET /api/v1/models` and champion/challenger promotion in Phase 7. Thresholds are recorded here as well as in the artifact bundle, so an old alert can be re-read against the exact threshold that produced it.
- The `family_matches_kind` constraint is the database-level guarantee behind the two-stage design: an `UNCLASSIFIED_ANOMALY` has, by definition, no family label, and a `KNOWN` alert must have one.
- `ALERT_FAMILIES` is defined but is not attached to a check constraint on `alerts.family`; the constraint set covers `kind`, `severity`, `status`, `source` and `detection_stage`. The family vocabulary is enforced at the wire boundary by the `AlertFamily` literal in [schemas.py](#backendappschemaspy).
- `explanation` is nullable at the schema level, but the pipeline always populates it: an alert with a score and no reason is an alert an analyst ignores.
- Only `detected_at` and `alert_id` carry a single-column index; every other index in the tables above is composite — and the naming convention names those two as well, `ix_alerts_detected_at` and `ix_analyst_verdicts_alert_id`, so nothing in the schema is anonymous.
- The defaults in the tables above — `host_prior_alert_count=0`, `occurrence_count=1`, `status='open'`, `source='replay'`, `stage='challenger'`, `is_active=False` — are SQLAlchemy `default=` values applied by the ORM at flush time. They are not `DEFAULT` clauses: the Phase 0 migration declares every one of those columns `NOT NULL` with no server default, so a raw `INSERT` that omits them fails. Only `created_at` and `updated_at` have a real server default (`server_default=sa.text('(CURRENT_TIMESTAMP)')`). Write through the ORM, or supply every column.
- `ModelVersion.stage` and `ModelVersion.is_active` are independent columns with no cross-constraint. The schema permits two rows at `stage='champion'`, or several with `is_active=True`, or a `champion` that is not active. Nothing in the database enforces one-champion-at-a-time; Phase 7 promotion has to do it in application code, or add a partial unique index when Postgres is the target.
- This module imports `Base` from `app/db.py` and nothing else from the project. It is imported by `backend/alembic/env.py:17` as `from app import models  # noqa: F401` — the import exists purely for its side effect of registering every table on `Base.metadata`, and without it autogenerate emits an empty migration — and by `backend/tests/test_schema_portability.py:16`. No module under `backend/app/routes/` imports it yet, and neither does `app/main.py`, so `Base.metadata` is empty in the serving process.
- Status: **implemented as schema.** The tables are created by the Phase 0 migration — see [Code Reference — Backend Migrations](Code-Backend-Migrations) and [Database Schema](Database-Schema). The deduplication logic the `Alert` docstring points at (`app/dedupe.py`) and the pipeline that writes these rows arrive with the alert pipeline phases. Portability is asserted rather than assumed: `backend/tests/test_schema_portability.py` compiles every column type against both the Postgres and SQLite dialects and exercises the check constraints — see [Code Reference — Backend Tests](Code-Backend-Tests).

---

## backend/app/schemas.py

**Path:** `backend/app/schemas.py` — the Pydantic wire contracts and the shared `Literal` vocabularies that mirror the database check constraints.

### What it does

These models are the source of truth for the frontend's TypeScript types, which are generated from this app's OpenAPI schema with `npm run gen:types` rather than hand-written, so the two cannot silently drift. Generating rather than transcribing means a field renamed here becomes a compile error in the frontend rather than an `undefined` at runtime.

The file is short in Phase 0 because only one endpoint has a real response body. It defines the shared vocabularies as `Literal` aliases — the wire-level counterpart of the `CheckConstraint` tuples in [models.py](#backendappmodelspy) — plus two models: `HealthResponse`, the three-field health contract, and `NotImplementedResponse`, the body every route stub returns with HTTP 501.

`NotImplementedResponse` is explicit and machine-readable by design, so a caller can tell "not built yet" apart from "built and broken". No stub returns invented data. The helper that constructs it, `not_implemented(endpoint, phase)`, lives in `app/routes/__init__.py`.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `AlertKind` | Type alias | `Literal["KNOWN", "UNCLASSIFIED_ANOMALY"]` | Wire vocabulary for `Alert.kind`. |
| `AlertFamily` | Type alias | `Literal["dos", "ddos", "brute_force", "port_scan", "web_attack", "botnet", "infiltration"]` | Named attack families Stage 1 can report. |
| `Severity` | Type alias | `Literal["low", "medium", "high", "critical"]` | Wire vocabulary for `Alert.severity`. |
| `AlertStatus` | Type alias | `Literal["open", "in_review", "closed", "dismissed"]` | Wire vocabulary for `Alert.status`. |
| `Verdict` | Type alias | `Literal["TP", "FP", "UNSURE"]` | Wire vocabulary for `AnalystVerdict.verdict`. |
| `DetectionStage` | Type alias | `Literal["stage1_supervised", "stage2_anomaly"]` | Which stage produced an alert. |
| `AlertSource` | Type alias | `Literal["replay", "live", "api"]` | Where an alert came from. |
| `HealthStatus` | Type alias | `Literal["ok", "degraded"]` | The two values `/health` can report. |
| `HealthResponse` | Pydantic model | `class HealthResponse(BaseModel)` | Response body of `GET /api/v1/health`. |
| `NotImplementedResponse` | Pydantic model | `class NotImplementedResponse(BaseModel)` | Body returned by route stubs that a later phase fills in. |

### HealthResponse fields

| Field | Type | Required | Constraints | Description |
| --- | --- | --- | --- | --- |
| `status` | `HealthStatus` | yes | `Literal["ok", "degraded"]` | `'ok'` once the process is serving; `'degraded'` if a loaded artifact bundle is present but unusable. |
| `model_version` | `str` | yes | examples `["unloaded", "rf-20260921-1"]` | Version of the currently loaded model bundle, or `'unloaded'`. |
| `uptime_s` | `float` | yes | `ge=0`, example `12.482` | Seconds since the FastAPI lifespan started. |

`HealthResponse` sets `model_config = ConfigDict(protected_namespaces=())`. `model_` is a protected prefix in Pydantic v2 and `model_version` is part of the agreed contract, so the namespace guard is lifted for this model.

### NotImplementedResponse fields

| Field | Type | Required | Constraints | Description |
| --- | --- | --- | --- | --- |
| `detail` | `str` | yes | — | Human-readable message, built as `f"Not implemented yet. Arrives in {phase}."`. |
| `phase` | `str` | yes | — | Build phase that implements this endpoint. |
| `endpoint` | `str` | yes | — | The endpoint the caller asked for. |

### Notes

- Seven of the eight vocabularies in `models.py` are mirrored here, and `HealthStatus` exists only on the wire. `MODEL_STAGES` (`models.py:58` — `champion`, `challenger`, `archived`) has no `Literal` alias, because no Phase 0 endpoint returns a `ModelVersion`; it arrives with `GET /api/v1/models` in Phase 7. The two lists are kept in step by hand, and the alias list is the one the frontend sees.
- This module imports nothing from the project — only `typing.Literal` and `pydantic` — which is why every other module can import it without a cycle. `HealthResponse` is used by `app/main.py:21` as the `response_model` of `GET /health`; `NotImplementedResponse` is used by `app/routes/__init__.py:17` to build the 501 body and by all six route modules to declare it in `responses={501: {"model": NotImplementedResponse}}`.
- The contract for `/health` is exactly three fields, per Phase 0. Adding a fourth is an API change, not a detail, and `backend/tests/test_health.py` asserts the field set exactly rather than checking for presence.
- Status: **implemented.** Request and response models for alerts, scoring, metrics and analytics land alongside those endpoints — see [API Reference](API-Reference) and the [Roadmap](Roadmap).

---

## backend/app/inference.py

**Path:** `backend/app/inference.py` — loads the trained model bundle from disk once at startup, verifies it against the feature contract, and is the future home of the two-stage scoring entry point.

### What it does

This module owns everything the serving path needs to know about the models, and nothing about how they were produced. Loading happens exactly once, in the FastAPI lifespan, and the result lives on `app.state`. Nothing here ever calls `.fit()`: training is offline batch work in `backend/training/`, and this module only consumes its artifacts.

The bundle is a dataclass holding preprocessing state (scaler, feature order, dropped columns, port encoding, schema hash), the Stage 1 estimator, the Stage 2 weights, both thresholds, the benign error histogram and the model card. Its `load()` method reads `preprocessing.pkl` first. If that file is absent, loading returns immediately with `version == "unloaded"` and logs an informational line — absent artifacts are not an error in Phase 0 or Phase 1, because the API is expected to serve health and the dashboard shell before any model exists. A *present but inconsistent* bundle is a different matter and raises.

Schema-hash verification is the point of the module. `_verify_schema_hash()` refuses a bundle with no `schema_hash` at all, then recomputes the hash from the bundle's own `feature_order` using `compute_schema_hash()` from `training.features` and compares. Any disagreement raises `SchemaHashMismatch`. Because `load_bundle()` is called inside the lifespan, that exception aborts startup. This is the whole defence against train/serve skew: mismatched column order produces garbage scores without raising anything, so the check has to be loud. `_load_model_card()` performs a second, independent check — if `model_card.json` carries a `schema_hash` that disagrees with the one in `preprocessing.pkl`, that also raises, because the two files are written together and disagreeing means the bundle is half-updated.

Status and version semantics are deliberately narrow. `version` starts at the module constant `UNLOADED_VERSION = "unloaded"` and is only replaced by the `version` key of the model card. `status` returns `"degraded"` when the private `_degraded` flag is set and `"ok"` otherwise — so a Phase 0 process with no artifacts at all reports `ok` with `model_version: "unloaded"`, because having no model yet is expected rather than broken. `degraded` is reserved for a bundle that was found but could not be made usable. Three readiness properties describe what is actually available: `is_loaded`, `stage1_ready` (a supervised model *and* `tau_sup`) and `stage2_ready` (autoencoder weights *and* `tau_anom`). A model without its threshold is not ready to serve, because the threshold is what turns a score into a decision.

`_load_models()` documents its trust boundary explicitly. Everything under `artifacts_dir` is produced locally by `backend/training/` and is gitignored; nothing user-supplied or downloaded is ever unpickled, the API has no artifact-upload path, and Phase 9's pcap ingestion feeds `features.py` rather than this module. The supervised estimator is unpickled because scikit-learn and LightGBM estimators have no non-pickle round trip. Torch weights are loaded with `map_location="cpu"` and `weights_only=True`, so the Stage 2 file is data rather than code, and `torch` is imported lazily inside the function because it is a heavy import that Phase 0 startup should not pay for when there is nothing to load.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `UNLOADED_VERSION` | Constant | `UNLOADED_VERSION = "unloaded"` | The version string reported when no model card has been read. |
| `PREPROCESSING_FILE` | Constant | `PREPROCESSING_FILE = "preprocessing.pkl"` | Scaler, feature order, dropped columns, port encoding and schema hash. Its presence gates the whole load. |
| `SUPERVISED_FILE` | Constant | `SUPERVISED_FILE = "supervised_model.pkl"` | Stage 1 estimator, written by `training/train_supervised.py`. |
| `AUTOENCODER_FILE` | Constant | `AUTOENCODER_FILE = "autoencoder.pt"` | Stage 2 weights as a state dict. |
| `MODEL_CARD_FILE` | Constant | `MODEL_CARD_FILE = "model_card.json"` | Version, thresholds and schema hash for the bundle. |
| `SchemaHashMismatch` | Exception | `class SchemaHashMismatch(RuntimeError)` | Raised when an artifact bundle disagrees with the feature contract. Deliberately fatal at startup. |
| `ModelBundle` | Dataclass | `@dataclass class ModelBundle` | Everything the serving path needs, loaded from disk once at startup. |
| `ModelBundle.is_loaded` | Property | `is_loaded -> bool` | True once at least one trained model is resident: `supervised is not None or autoencoder_state is not None`. |
| `ModelBundle.stage1_ready` | Property | `stage1_ready -> bool` | `supervised is not None and tau_sup is not None`. |
| `ModelBundle.stage2_ready` | Property | `stage2_ready -> bool` | `autoencoder_state is not None and tau_anom is not None`. |
| `ModelBundle.status` | Property | `status -> str` | `"degraded"` when `_degraded` is set, otherwise `"ok"`. |
| `ModelBundle.load` | Method | `load(self) -> ModelBundle` | Populates the bundle from `artifacts_dir` and returns `self`. Returns early and unchanged when `preprocessing.pkl` is absent. |
| `ModelBundle._verify_schema_hash` | Method | `_verify_schema_hash(self) -> None` | Raises `SchemaHashMismatch` when the hash is missing or does not match a hash recomputed from `feature_order`. |
| `ModelBundle._load_model_card` | Method | `_load_model_card(self) -> None` | Reads `model_card.json` if present; sets `version`, `tau_sup`, `tau_anom`; raises on a conflicting card hash. |
| `ModelBundle._load_models` | Method | `_load_models(self) -> None` | Loads Stage 1 via `pickle` and Stage 2 via `torch.load(..., map_location="cpu", weights_only=True)` when their files exist. |
| `ModelBundle.score_batch` | Method | `score_batch(self, flows: list[dict[str, Any]]) -> list[dict[str, Any]]` | Two-stage fusion scoring. Stub — raises `NotImplementedError`. |
| `load_bundle` | Function | `load_bundle(artifacts_dir: Path) -> ModelBundle` | Builds and loads the process-wide bundle: `ModelBundle(artifacts_dir=artifacts_dir).load()`. |

### ModelBundle fields

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `artifacts_dir` | `Path` | required | Directory the bundle is read from; supplied as `settings.artifacts_path`. |
| `version` | `str` | `UNLOADED_VERSION` | Model version from the model card, or `"unloaded"`. |
| `schema_hash` | `str \| None` | `None` | Feature-order hash persisted in `preprocessing.pkl`. |
| `feature_order` | `list[str]` | `[]` | Exact column order the model was trained against. |
| `dropped_columns` | `list[str]` | `[]` | Columns removed during preprocessing, kept for reproducibility. |
| `port_encoding` | `dict[str, Any]` | `{}` | Port encoding scheme chosen during feature work. |
| `scaler` | `Any \| None` | `None` | Fitted preprocessing scaler. |
| `supervised` | `Any \| None` | `None` | Stage 1 estimator. |
| `autoencoder_state` | `dict[str, Any] \| None` | `None` | Stage 2 weights as a state dict; Phase 3 owns the `nn.Module` definition and reconstructs the model from it. |
| `autoencoder` | `Any \| None` | `None` | The reconstructed Stage 2 module, populated in Phase 3. |
| `tau_sup` | `float \| None` | `None` | Stage 1 threshold, read from the model card's `thresholds`. |
| `tau_anom` | `float \| None` | `None` | Stage 2 threshold, read from the model card's `thresholds`. |
| `benign_error_histogram` | `dict[str, Any] \| None` | `None` | Distribution of benign reconstruction error, for contextualising an anomaly score. Never assigned by the Phase 0 loader — populated in Phase 3. |
| `model_card` | `dict[str, Any]` | `{}` | Parsed `model_card.json`. |
| `_degraded` | `bool` | `False` | Private flag behind the `status` property. |

### Notes

- The verification is a recomputation, not a comparison of two stored values: `compute_schema_hash(self.feature_order)` must equal the persisted `schema_hash`. The hash is the SHA-256 of the feature names joined by newlines, rendered as `sha256:<hexdigest>` (`training/features.py:77-89`), and order-sensitivity is pinned by an executable doctest in that function: `compute_schema_hash(["a", "b"]) == compute_schema_hash(["b", "a"])` is `False`. The writer side is `build_preprocessing_bundle()` in the same module, which derives the hash from `feature_order` rather than accepting one — see [Code Reference — Backend Training](Code-Backend-Training).
- A bundle carrying no `schema_hash` is rejected outright, with a message directing the operator to re-run the Phase 1 pipeline so the bundle is written with one.
- `load()` returns `self`, which is what makes the one-liner in `load_bundle()` work and what lets the lifespan assign the result directly.
- `_degraded` is declared after the `status` property in the source. It is a dataclass field defaulting to `False`; nothing in Phase 0 sets it, so `status` is always `"ok"` today.
- `benign_error_histogram` and `autoencoder` are declared on the dataclass but assigned nowhere: `load()` reads neither out of the bundle. `load()` assigns `scaler`, `feature_order`, `dropped_columns`, `port_encoding` and `schema_hash`; `_load_model_card()` assigns `model_card`, `version`, `tau_sup` and `tau_anom`; `_load_models()` assigns `supervised` and `autoencoder_state`. Phase 3 both writes the benign reconstruction-error distribution into the artifact bundle and reconstructs the `nn.Module` from `autoencoder_state`. Until then both stay at their defaults, alongside `_degraded`.
- `from training.features import compute_schema_hash` (line 21) is the only import from `backend/training/` into `backend/app/`, and it is the invariant made mechanical: feature transforms live in exactly one module, so the API cannot reimplement them and drift. The dependency runs one way only — `backend/training/__init__.py` states "Nothing in this package is imported by a request handler." It resolves because both packages sit directly under `backend/`, which is uvicorn's working directory (`Makefile:36`) and pytest's `pythonpath` (`backend/pyproject.toml:46`).
- The guard is regression-tested: `backend/tests/test_api_surface.py::test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash` writes a bundle whose `schema_hash` does not match its `feature_order` and asserts `load_bundle()` raises. It was written in Phase 0, before there was anything to load, because the failure it prevents raises nothing on its own — see [Code Reference — Backend Tests](Code-Backend-Tests).
- Apart from `training.features`, this module imports nothing from the project. It is imported by `app/main.py:19` (`ModelBundle`, `load_bundle`) and by `backend/tests/test_api_surface.py`; no route module imports it, so nothing in the request path reaches the bundle except `/health`.
- Status of the loader, the schema check and the status/version properties: **implemented.**
- Status of `score_batch`: **stub** — raises `NotImplementedError` with the message "score_batch arrives in Phase 4 (fusion); Stage 1 lands in Phase 2 and Stage 2 in Phase 3." It is batch-only by design, because per-row `predict()` in the replay loop is roughly 50x slower and makes the live demo stutter.
- Status of the Stage 1 and Stage 2 branches of `_load_models()`: **written but never yet exercised.** The code will load whatever is present, but no artifact exists until Phase 2 (supervised) and Phase 3 (autoencoder) produce one, and none has ever been produced in this repository. Until then a bundle may legitimately contain only preprocessing.
