# Configuration

Every tunable in Recluse — path, port, threshold budget, poll interval — arrives from the
environment. This page documents how that happens, lists every setting the backend defines, works
through the derived values (including the false-positive budget arithmetic that replaces an
arbitrary `0.5` threshold), and covers the frontend's separate `VITE_` surface. It is for anyone
running the stack in a new environment or wondering where a number came from.

**Status:** Everything on this page is shipped. Some settings exist for phases not yet built
(`IDS_DEDUPE_WINDOW_SECONDS`, the false-positive budget inputs); where that is true it is marked.
The values are real and read today even though the code that consumes them arrives later.

Source files: `backend/app/config.py`, `.env.example`, `frontend/src/lib/env.ts`,
`frontend/vite.config.ts`, `docker-compose.yml`.

---

## How configuration is loaded

The backend settings object is a `pydantic-settings` `BaseSettings` subclass in
`backend/app/config.py`:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IDS_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),  # we legitimately expose `model_*` names
    )
```

Four consequences worth knowing:

- **Every backend variable is prefixed `IDS_`.** The field `port` is set by `IDS_PORT`, `database_url`
  by `IDS_DATABASE_URL`, and so on. The prefix is applied by pydantic-settings, not written into the
  field names.
- **The env file is found by repo root, not by working directory.** `REPO_ROOT` is derived from the
  module's own location (`backend/app/config.py` → `backend/` → repo root), so the same `.env` is read
  whether uvicorn was launched from the repo root, from `backend/`, or by pytest.
- **`extra="ignore"`** means the frontend's `VITE_*` entries and the compose-only `BACKEND_PORT` /
  `FRONTEND_PORT` entries can share one `.env` without the backend rejecting them.
- **`protected_namespaces=()`** is required because the project legitimately uses `model_*` names,
  which pydantic otherwise reserves.

**Precedence**, highest first:

1. Values passed directly to the constructor — `Settings(data_dir=tmp_path)`. Used by tests only.
2. Real process environment variables. This is what `docker-compose.yml` sets on each service, and it
   is why a container binds `0.0.0.0` regardless of what `.env` says for local runs.
3. The repo-root `.env` file.
4. The field default declared in `config.py`.

The settings object is a process-wide singleton:

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

The environment is therefore read once per process. Changing `.env` requires a restart; uvicorn's
`--reload` restarts on Python file changes, not on `.env` changes.

**The rule this enforces:** nothing downstream hardcodes a path or a port. The same image runs
locally, under compose, and against Postgres without a code change. `scripts/dev.py` parses `.env`
itself for exactly this reason — the URLs it prints are the ports actually served, not a guess.

One setting is not merely read but validated against:

```python
@field_validator("allow_auto_block")
@classmethod
def _reject_auto_block(cls, value: bool) -> bool:
    if value:
        raise ValueError(
            "IDS_ALLOW_AUTO_BLOCK cannot be enabled. The system alerts, "
            "ranks and explains; it never drops traffic. ..."
        )
    return value
```

Setting `IDS_ALLOW_AUTO_BLOCK=true` fails at startup rather than being silently ignored. The flag
exists so the constraint is explicit and greppable rather than merely absent. See
[Anti-Patterns](Anti-Patterns).

---

## Settings reference

Every field defined on `Settings`, in declaration order. Prefix every env var with `IDS_`.

| Setting | Env var | Type | Default | What it controls |
| ------- | ------- | ---- | ------- | ---------------- |
| `app_name` | `IDS_APP_NAME` | `str` | `"Recluse"` | The OpenAPI title (`"Recluse API"`) and the name in startup and shutdown log lines. Not present in `.env.example`; override only if you fork the project. |
| `env` | `IDS_ENV` | `Literal["development", "staging", "production"]` | `"development"` | Declared deployment environment. Logged at startup. Any other value fails validation. |
| `log_level` | `IDS_LOG_LEVEL` | `Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]` | `"INFO"` | Root logging level applied by `_configure_logging()` in the lifespan. `.env.example` documents the four common values; `CRITICAL` is also accepted. |
| `host` | `IDS_HOST` | `str` | `"127.0.0.1"` | Interface uvicorn binds. `scripts/dev.py` passes it as `--host`; the container overrides it to `0.0.0.0`, since a container bound to loopback publishes a port that reaches nothing. |
| `port` | `IDS_PORT` | `int` | `8000` | Port uvicorn binds, and the port `scripts/dev.py` prints in the URLs it reports. |
| `api_v1_prefix` | `IDS_API_V1_PREFIX` | `str` | `"/api/v1"` | Mount prefix for both routers. Health lives at `{prefix}/health`; an unprefixed `/health` correctly 404s, and a test asserts that. |
| `database_url` | `IDS_DATABASE_URL` | `str` | `"sqlite+pysqlite:///data/ids.db"` | SQLAlchemy URL. Swapping to Postgres is this line and nothing else — the ORM uses portable column types exclusively. See [Database Schema](Database-Schema). |
| `db_echo` | `IDS_DB_ECHO` | `bool` | `false` | Passes through to SQLAlchemy's `echo`, logging every emitted statement. Debugging aid; noisy. |
| `data_dir` | `IDS_DATA_DIR` | `Path` | `data` | Root for datasets and the SQLite file. Relative values resolve against the repo root. |
| `artifacts_dir` | `IDS_ARTIFACTS_DIR` | `Path` | `backend/artifacts` | Where `backend/training/` writes model artifacts and where `load_bundle()` looks at startup. |
| `reports_dir` | `IDS_REPORTS_DIR` | `Path` | `reports` | Where evaluation output lands, including the Phase 4 `loao.md` table. |
| `cors_origins` | `IDS_CORS_ORIGINS` | `str` (comma-separated) | `"http://localhost:5173,http://127.0.0.1:5173"` | Browser origins allowed to call the API. Deliberately a string, not a list: a list-typed field would make pydantic-settings demand a JSON array in the env file. |
| `expected_daily_flow_volume` | `IDS_EXPECTED_DAILY_FLOW_VOLUME` | `int`, `gt=0` | `1000000` | Flows the monitored network is expected to produce per day. The denominator of the false-positive budget. |
| `analyst_capacity_per_hour` | `IDS_ANALYST_CAPACITY_PER_HOUR` | `int`, `gt=0` | `40` | Alerts one analyst can triage in an hour. |
| `analyst_shift_hours` | `IDS_ANALYST_SHIFT_HOURS` | `int`, `gt=0` | `8` | Length of an analyst shift, in hours. |
| `dedupe_window_seconds` | `IDS_DEDUPE_WINDOW_SECONDS` | `int`, `gt=0` | `300` | Time bucket for the alert dedupe key `(src_host, alert_class, floor(ts, window))`. Consumed by `app/dedupe.py`; the full pipeline arrives in Phase 5. |
| `allow_auto_block` | `IDS_ALLOW_AUTO_BLOCK` | `bool` | `false` | Present so the no-auto-block constraint is explicit and greppable. Setting it `true` raises at startup. There is no value that enables automatic blocking. |

`gt=0` fields are enforced by pydantic — `IDS_EXPECTED_DAILY_FLOW_VOLUME=0` raises a
`ValidationError` rather than producing a division by zero later.

---

## Derived values

Seven `@computed_field` properties turn raw settings into the values the rest of the code uses.

### `cors_origin_list`

```python
return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
```

Splits the comma-separated string, trims whitespace and drops empties, so
`"http://a.test, http://b.test ,"` yields `["http://a.test", "http://b.test"]`. `create_app()` only
adds `CORSMiddleware` when this list is non-empty.

### `data_path`, `artifacts_path`, `reports_path`

Each applies the module-level `_resolve()`:

```python
def _resolve(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()
```

A relative value is anchored to the repo root; an absolute value is honoured untouched, which is what
the container passes in (`IDS_DATA_DIR=/srv/data`). Without this, `IDS_DATA_DIR=data` would mean a
different directory depending on where the process was started.

### `max_alerts_per_day`

```python
return self.analyst_capacity_per_hour * self.analyst_shift_hours
```

With defaults: `40 alerts/hour * 8 hours = 320 alerts/day`. This is the numerator of the budget —
one analyst's realistic daily triage capacity.

### `target_fpr`

```python
return self.max_alerts_per_day / self.expected_daily_flow_volume
```

With defaults: `320 / 1,000,000 = 0.00032`, i.e. `3.2e-4`.

This is the false-positive rate the supervised threshold has to respect. Phase 2 will pick `tau_sup`
as the smallest threshold whose measured FPR stays at or under this number, rather than defaulting to
`0.5`:

```
Given expected daily flow volume V and analyst capacity C alerts/hour:
  max_alerts_per_day = C * shift_hours      ->  40 * 8      = 320
  target_FPR         = max_alerts_per_day / V ->  320 / 1e6 = 3.2e-4
  tau_sup            = smallest threshold where FPR(tau) <= 3.2e-4
```

Read the arithmetic backwards to see why it matters: at a 1% false-positive rate on a million flows,
the queue receives 10,000 alerts a day against a capacity of 320. The queue is not noisy, it is
unusable. The budget makes the threshold an engineering decision with a stated cost model instead of
a library default.

The whole calculation is logged once at startup:

```
false-positive budget: 320 alerts/day over 1000000 flows/day -> target FPR 3.20e-04
```

`tau_sup` itself does not exist yet — **not measured yet; Phase 2 produces it** by evaluating the
supervised model's FPR curve against this target. `tau_anom`, the Stage 2 threshold, is the 99.5th
percentile of benign reconstruction error and is **not measured yet; Phase 3 produces it**. Both are
persisted in the artifact bundle rather than recomputed at serve time. See [ML Models](ML-Models).

### `sqlalchemy_url`

```python
url = make_url(self.database_url)
if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
    db_path = Path(url.database)
    if not db_path.is_absolute():
        db_path = _resolve(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = url.set(database=str(db_path))
return url.render_as_string(hide_password=False)
```

SQLite URLs with a relative path are rewritten to an absolute path anchored at the repo root, and the
parent directory is created. A bare `sqlite:///data/ids.db` is otherwise resolved against the working
directory, which silently produces a *different* database depending on where uvicorn was launched —
the API writing one file while Alembic migrated another. Non-SQLite URLs pass through byte for byte,
including a Postgres URL with credentials; a test asserts that.

`:memory:` is left alone so in-memory test databases still work.

---

## Directories

`ensure_directories()` is called once, from the FastAPI lifespan, before anything else runs:

```python
def ensure_directories(self) -> None:
    for path in (self.data_path, self.artifacts_path, self.reports_path):
        path.mkdir(parents=True, exist_ok=True)
```

| Directory | Default | Why it must exist before the app serves |
| --------- | ------- | --------------------------------------- |
| `data_path` | `<repo>/data` | Holds `ids.db` plus `raw/`, `interim/` and `processed/` for the dataset pipeline. The SQLite connection fails outright if the parent is missing. |
| `artifacts_path` | `<repo>/backend/artifacts` | Where `load_bundle()` looks for `preprocessing.pkl`, `supervised_model.pkl`, `autoencoder.pt` and `model_card.json`. An absent directory would raise where "no model yet" should be an ordinary, expected state. |
| `reports_path` | `<repo>/reports` | Evaluation output written by `backend/training/evaluate.py` and `loao.py`. |

The call is idempotent, so restarts and the container's repeated boots are safe. `sqlalchemy_url`
additionally creates the SQLite file's parent directory as a side effect of resolving the path.

The directories are tracked in git via `.gitkeep` files; their contents are gitignored, because
datasets and model artifacts are reproducible outputs rather than source. See
[Repository Layout](Repository-Layout).

---

## Frontend configuration

The browser bundle only ever sees variables prefixed `VITE_`. Vite's `envDir` is pointed at the repo
root, so there is one env file for the whole stack rather than two that drift:

```ts
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, repoRoot, '')
  ...
  return { envDir: repoRoot, ... }
})
```

| Variable | Default | Read by | What it controls |
| -------- | ------- | ------- | ---------------- |
| `VITE_API_BASE_URL` | `/api/v1` | `src/lib/env.ts` | Prefix prepended to every request path by `src/api/client.ts`. Relative by default. |
| `VITE_HEALTH_POLL_MS` | `5000` | `src/lib/env.ts` | Health panel refetch interval, in milliseconds. |
| `VITE_DEV_SERVER_HOST` | `::` | `vite.config.ts` | Interface the dev server binds. Dual-stack by default; on Windows, Node resolves the default host to `::1` only, so `127.0.0.1` refuses connections. Containers override it to `0.0.0.0`. |
| `VITE_DEV_SERVER_PORT` | `5173` | `vite.config.ts` | Dev server and preview port, with `strictPort: true` so a collision fails loudly rather than silently moving. |
| `VITE_DEV_PROXY_TARGET` | `http://127.0.0.1:8000` | `vite.config.ts`, `scripts/generate-types.mjs`, `SystemHealth.test.tsx` | Where the dev server proxies `/api`, where the type generator fetches `/openapi.json`, and which backend the live integration test probes. |

`src/lib/env.ts` is the single place a default lives on the frontend, so no component contains a
literal port or path:

```ts
export const env = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? DEFAULTS.apiBaseUrl,
  healthPollMs: positiveInt(import.meta.env.VITE_HEALTH_POLL_MS, DEFAULTS.healthPollMs),
} as const
```

`positiveInt` falls back whenever the value is not a finite number greater than zero, so a typo in
`.env` degrades to the default rather than producing a request storm or a panel that never refreshes.

### How the API base URL resolves

```
DEV (make dev)
  browser  -> http://localhost:5173/api/v1/health
  vite     -> proxy '/api' -> VITE_DEV_PROXY_TARGET (http://127.0.0.1:8000)
  origin   -> same-origin; the browser never performs a CORS preflight

CONTAINERS (docker compose up)
  browser  -> http://localhost:5173/api/v1/health
  vite     -> proxy '/api' -> http://backend:8000   (service name, not localhost)

PRODUCTION IMAGE (frontend Dockerfile target `serve`, Phase 8)
  browser  -> http://<host>/api/v1/health
  nginx    -> location /api/ { proxy_pass http://backend:8000; }
              proxy_buffering off, proxy_read_timeout 24h for the SSE feed
```

Because `VITE_API_BASE_URL` is a *relative* URL in all three cases, the same built bundle works
behind the dev proxy and behind nginx with no rebuild — and the browser never needs CORS. The
backend's `IDS_CORS_ORIGINS` is the fallback for the case where something does call the API
cross-origin.

---

## Changing environments

| Scenario | What to change | Notes |
| -------- | -------------- | ----- |
| Local development | Copy `.env.example` to `.env` (`make env` does this) and edit. | Defaults work unmodified. `scripts/dev.py` creates the file if it is missing. |
| Moving ports | `IDS_PORT` and `VITE_DEV_SERVER_PORT` in `.env`. | Also update `VITE_DEV_PROXY_TARGET` to the new backend port, or the dashboard proxies to the old one. Restart Vite; the proxy target is read at config load. |
| Containers | Nothing — `docker-compose.yml` sets process env vars that override `.env`. | It forces `IDS_HOST=0.0.0.0`, `VITE_DEV_SERVER_HOST=0.0.0.0`, `VITE_DEV_PROXY_TARGET=http://backend:8000`, and the `/srv/...` absolute paths that mirror the repo layout inside the image. Host port publishing uses `BACKEND_PORT` and `FRONTEND_PORT`, which compose reads from `.env` directly (no `IDS_` prefix). |
| SQLite to Postgres | `IDS_DATABASE_URL` only. | `postgresql+psycopg://ids:ids@localhost:5432/ids` natively, or `@postgres:5432` under compose with the `postgres` profile started. The URL passes through `sqlalchemy_url` untouched. `backend/tests/test_schema_portability.py` asserts every column type compiles for both dialects, so this stays a one-line change rather than a discovery on the day of the swap. |
| Staging or production | `IDS_ENV`, `IDS_LOG_LEVEL`, `IDS_CORS_ORIGINS`, `IDS_DATABASE_URL`. | `IDS_ENV` is declarative — it is logged, and no code branches on it today. Serve the built dashboard from the `serve` image stage rather than the Vite dev server. |
| Tuning the alert budget | `IDS_EXPECTED_DAILY_FLOW_VOLUME`, `IDS_ANALYST_CAPACITY_PER_HOUR`, `IDS_ANALYST_SHIFT_HOURS`. | These feed threshold selection in Phase 2. Changing them after a model is trained means the persisted `tau_sup` no longer matches the budget; re-run threshold selection rather than editing the artifact. |

`.env` is gitignored; `.env.example` is not. Any new setting must be added to both `config.py` and
`.env.example` in the same change, or the next contributor's environment silently differs from yours.
