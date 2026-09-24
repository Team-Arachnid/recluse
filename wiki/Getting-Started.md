# Getting Started

This page takes a clone of the repository to a browser showing live health data from the running
API. It covers the native path (`make dev`), the container path (`docker compose up`), how to verify
the install, and the failures new contributors actually hit on a first run. It is for anyone setting
up Recluse for the first time, on Windows, Linux or macOS. For what each setting means once the stack
is up, see [Configuration](Configuration.md); for what runs where, see [Architecture](Architecture.md).

**Status:** Phase 0 of 9 is complete. Everything on this page is shipped and runs today. There is no
trained model, so `/api/v1/health` reports `model_version: "unloaded"` and every other v1 endpoint
answers `501` with the phase that implements it. That is the expected result of a correct install,
not a broken one. See [Roadmap](Roadmap.md).

---

## Prerequisites

| Tool | Version | Why | Check command |
| ---- | ------- | --- | ------------- |
| Python | 3.12 (`backend/.python-version`); `pyproject.toml` allows `>=3.11,<3.13` | Runs the API and the training scripts. 3.13+ is excluded because the ML stack (torch, lightgbm, shap→numba) does not have settled wheels there yet. | `python --version` |
| uv | any recent release | Resolves and installs the backend from `uv.lock`, and runs every backend command (`uv run pytest`, `uv run alembic`). It also fetches the pinned Python for you. | `uv --version` |
| Node.js | 20.19+ or 22.12+ (the floor Vite 7 requires; `scripts/dev.py` prints exactly this hint). The container image uses `node:24-alpine`. | Runs the Vite dev server, the dashboard build and the vitest suite. | `node --version` |
| npm | ships with Node | Installs the frontend from `package-lock.json`. | `npm --version` |
| Docker | any version with the Compose v2 plugin | Optional. Only needed for the containerised stack (`make up`) and the Postgres profile. | `docker compose version` |
| git | any | Cloning, and the wiki publish script. See [Wiki Publishing](Wiki-Publishing.md). | `git --version` |

GNU make is **not** a prerequisite. It is not installed on Windows by default, so `make.ps1` at the
repo root mirrors every `Makefile` target. Both forms are given side by side below.

---

## Quick start (native)

`make dev` runs `scripts/dev.py`, which is deliberately more than a shell one-liner: it creates
`.env` if missing, installs anything missing, applies migrations, then starts uvicorn and Vite
together and shuts both down cleanly on Ctrl-C.

**1. Clone and enter the repository.**

| | Command |
| --- | --- |
| any shell | `git clone <repository-url> Recluse` then `cd Recluse` |

**2. Create the environment file.** Optional — every command below that needs it creates it for you
from `.env.example`.

| Shell | Command |
| ----- | ------- |
| make | `make env` |
| PowerShell | `./make.ps1 env` |

**3. Install both sides.** Backend via `uv sync` in `backend/`, frontend via `npm install --no-fund`
in `frontend/`.

| Shell | Command |
| ----- | ------- |
| make | `make install` |
| PowerShell | `./make.ps1 install` |

**4. Apply the database migrations.** `make dev` does this for you; run it separately if you only
want the schema.

| Shell | Command |
| ----- | ------- |
| make | `make migrate` |
| PowerShell | `./make.ps1 migrate` |

**5. Start both processes with hot reload.**

| Shell | Command |
| ----- | ------- |
| make | `make dev` |
| PowerShell | `./make.ps1 dev` |

`scripts/dev.py` prints the URLs it actually served, reading the ports from `.env` rather than
hardcoding them:

```
[setup] API      http://localhost:8000/api/v1/health
[setup] API docs http://localhost:8000/docs
[setup] Dashboard http://localhost:5173
[setup] Ctrl-C to stop both
```

**6. Open `http://localhost:5173`.** The System Health panel fetches `/api/v1/health` through
TanStack Query and re-polls on the interval set by `VITE_HEALTH_POLL_MS` (default 5000 ms). The
browser never makes a cross-origin request in dev: Vite proxies `/api` to the backend, so the page
and the API share an origin. See [Frontend Screens](Frontend-Screens.md).

If you would rather run one side at a time, `make backend` / `./make.ps1 backend` starts only
uvicorn, and `make frontend` / `./make.ps1 frontend` starts only Vite.

```
  make dev  ->  scripts/dev.py
                     |
      +--------------+--------------+
      |                             |
  uv run uvicorn app.main:app   npm run dev  (vite)
  127.0.0.1:8000                 [::]:5173
      ^                             |
      |      /api  proxied          |
      +-----------------------------+
```

---

## Quick start (containers)

| Shell | Command |
| ----- | ------- |
| make | `make up` |
| PowerShell | `./make.ps1 up` |
| direct | `docker compose up --build` |

The compose project is named `recluse`. Ports on the host come from `.env`
(`BACKEND_PORT`, `FRONTEND_PORT`), with `8000` and `5173` as the fallbacks.

| Service | Image / build | Host port | Container port | What it runs |
| ------- | ------------- | --------- | -------------- | ------------ |
| `backend` | built from `backend/Dockerfile` | `${BACKEND_PORT:-8000}` | 8000 | `alembic upgrade head && uvicorn app.main:app`, bound to `0.0.0.0`. Healthcheck polls `/api/v1/health` every 10s. |
| `frontend` | built from `frontend/Dockerfile`, target `dev` | `${FRONTEND_PORT:-5173}` | 5173 | The Vite dev server with HMR, proxying `/api` to `http://backend:8000`. Starts only once the backend healthcheck passes. |
| `postgres` | `postgres:17-alpine` | `${POSTGRES_PORT:-5432}` | 5432 | Not started by default. Behind the `postgres` profile; see below. |

The backend container mounts `./data`, `./backend/artifacts` and `./reports` from the host, because
datasets, the SQLite file, model artifacts and reports are reproducible outputs rather than image
contents. The frontend mounts only `src/`, `index.html` and `vite.config.ts` read-only — mounting the
whole directory would shadow the image's `node_modules` with the host's and break platform-specific
binaries.

Postgres is opt-in and exists to demonstrate that swapping the database backend is configuration
only:

```
docker compose --profile postgres up -d postgres
IDS_DATABASE_URL=postgresql+psycopg://ids:ids@postgres:5432/ids \
  docker compose up --build backend
```

Other container commands: `make down` (stop), `make logs` (tail), `make ps` (status), each mirrored
by `./make.ps1`.

---

## Verifying the install

**The health endpoint.** This is the Phase 0 checkpoint.

```
curl http://localhost:8000/api/v1/health
```

Expected response — three fields, no more:

```json
{
  "status": "ok",
  "model_version": "unloaded",
  "uptime_s": 12.472
}
```

`model_version` is `"unloaded"` because no artifact bundle exists yet; `app/main.py` reports what is
actually resident on `app.state.bundle` rather than a hardcoded string. `status` is `"ok"` — a
scaffold with no model is the expected state, not a failure. It becomes `"degraded"` only when a
bundle is present but unusable. See [ML Models](ML-Models.md) and [Code: Backend Core](Code-Backend-Core.md).

The same request through the dev server proves the proxy works:

```
curl http://localhost:5173/api/v1/health
```

**Any other v1 endpoint** should answer `501` with a machine-readable body naming its phase:

```
curl -i http://localhost:8000/api/v1/alerts
```

```json
{
  "detail": "Not implemented yet. Arrives in Phase 5 (backend API).",
  "phase": "Phase 5 (backend API)",
  "endpoint": "GET /api/v1/alerts"
}
```

That is a correct install. See [API Reference](API-Reference.md).

**The interactive docs.** Open `http://localhost:8000/docs` for Swagger UI, or fetch the raw schema
at `http://localhost:8000/openapi.json`. The full v1 surface is registered in Phase 0, so the schema
is complete from the start and the generated frontend types cover routes that are not implemented yet.

**The test suites.**

| Suite | make | PowerShell | Underlying command |
| ----- | ---- | ---------- | ------------------ |
| Both | `make test` | `./make.ps1 test` | the two below, in order |
| Backend | `make test-backend` | `./make.ps1 test-backend` | `cd backend && uv run pytest` |
| Frontend | `make test-frontend` | `./make.ps1 test-frontend` | `cd frontend && npm run test` (`vitest run`) |

74 backend tests currently collect across five files. The frontend suite has one live-integration
block that is skipped automatically unless a backend is answering at `VITE_DEV_PROXY_TARGET`, so both
suites pass with nothing else running. Details in [Testing](Testing.md).

---

## Common tasks

| I want to... | Command (`make` / `./make.ps1`) |
| ------------ | ------------------------------- |
| See every available target | `make help` / `./make.ps1 help` |
| Create `.env` from the template | `make env` / `./make.ps1 env` |
| Install everything | `make install` / `./make.ps1 install` |
| Run the whole stack natively | `make dev` / `./make.ps1 dev` |
| Run only the API | `make backend` / `./make.ps1 backend` |
| Run only the dashboard | `make frontend` / `./make.ps1 frontend` |
| Apply migrations | `make migrate` / `./make.ps1 migrate` |
| Autogenerate a migration | `make revision m="add drift table"` / `./make.ps1 revision -m "add drift table"` |
| Run every test | `make test` / `./make.ps1 test` |
| Lint the backend and typecheck the frontend | `make lint` / `./make.ps1 lint` |
| Format and autofix the backend | `make format` / `./make.ps1 format` |
| Typecheck the frontend only | `make typecheck` / `./make.ps1 typecheck` |
| Regenerate the frontend API types | `make gen-types` / `./make.ps1 gen-types` (backend must be running) |
| Build the production dashboard bundle | `make build` / `./make.ps1 build` |
| Start / stop the containers | `make up` / `make down` (same on `./make.ps1`) |
| Tail container logs | `make logs` / `./make.ps1 logs` |
| Remove build output, caches and the dev database | `make clean` / `./make.ps1 clean` |

`make clean` deletes `data/ids.db` along with its `-wal` and `-shm` files. Run `make migrate`
afterwards to recreate the schema.

---

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `'make' is not recognized as an internal or external command` | GNU make is not installed on Windows by default. | Use `./make.ps1 <target>` — it mirrors every `Makefile` target. If you prefer real make, `winget install ezwinports.make`. |
| `./make.ps1 : File ... cannot be loaded because running scripts is disabled on this system` | PowerShell execution policy. | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` for the current session, then re-run. |
| `'uv' not found on PATH. Install from https://docs.astral.sh/uv/` | `scripts/dev.py` checks for `uv` before starting anything and exits with code 1. | Install uv, reopen the shell so `PATH` refreshes, re-run `make dev`. The same check exists for `npm` with the hint `Install Node.js 20.19+ or 22.12+`. |
| Vite exits with `Port 5173 is already in use` instead of moving to another port | `vite.config.ts` sets `strictPort: true` on purpose, so the documented URL never points at nothing. | Free the port, or set `VITE_DEV_SERVER_PORT` in `.env` to something else. `make dev` reads the new value and prints the URL it used. |
| `[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)` | Another process — often a previous uvicorn that did not exit — holds the backend port. | Stop it, or change `IDS_PORT` in `.env`. Also update `VITE_DEV_PROXY_TARGET` to match, or the dashboard proxies to the old port. |
| `sqlite3.OperationalError: no such table: alerts` | The database file exists but migrations were never applied, usually after `make clean` or a manual delete. | `make migrate` / `./make.ps1 migrate`. `make dev` and the backend container both run `alembic upgrade head` automatically. |
| The API answers, but the browser shows "backend unreachable" | In dev the dashboard calls the relative path `/api/v1/health` and relies on the Vite proxy. If `VITE_DEV_PROXY_TARGET` points at the wrong host or port, nothing answers. | Set `VITE_DEV_PROXY_TARGET` to the real backend origin (`http://127.0.0.1:8000` natively, `http://backend:8000` in compose) and restart Vite — it is read at config load, not per request. |
| Browser console shows a CORS error | Something is calling the API cross-origin — an absolute `VITE_API_BASE_URL`, or a dashboard served from a port not on the allow list. | Either keep `VITE_API_BASE_URL=/api/v1` (relative, so the proxy handles it), or add the dashboard origin to `IDS_CORS_ORIGINS` and restart the backend. See [Configuration](Configuration.md). |
| TypeScript errors about response fields that exist in the API | `frontend/src/types/api.d.ts` is generated and stale. | Start the backend, then `make gen-types`. Never hand-edit the file; its banner says so. |
| `failed to read http://127.0.0.1:8000/openapi.json` when generating types | The generator reads the live schema; the backend was not running. | Start it (`make backend`), then re-run `make gen-types`. |
| `npm run dev` fails inside the container after a host `npm install` | The host `node_modules` contains platform-specific binaries. | The compose file mounts only `src/`, `index.html` and `vite.config.ts` to avoid this. Do not add a whole-directory mount. |
| Docker healthcheck keeps the frontend from starting | `frontend` waits on `service_healthy` for `backend`, which polls `/api/v1/health` with a 15s start period and 12 retries. | `docker compose logs backend` — a failing migration or a bad `IDS_DATABASE_URL` is the usual cause. |

---

## Next

- [Configuration](Configuration.md) — every setting, its default and what it controls.
- [Repository Layout](Repository-Layout.md) — what lives where.
- [API Reference](API-Reference.md) — the full v1 surface and which phase fills each route.
- [Testing](Testing.md) — the suites, what they protect and what is still missing.
- [Data Pipeline](Data-Pipeline.md) — the next phase of work: the dataset, its defects and the split discipline.
