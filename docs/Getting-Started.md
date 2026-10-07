# Getting Started

This page takes a clone of the repository to a browser showing live health data from the running
API. It covers the native path (`make dev`), the container path (`docker compose up`), how to verify
the install, and the failures new contributors actually hit on a first run. It is for anyone setting
up Recluse for the first time, on Windows, Linux or macOS.

**Status:** Phases 0 to 8 of 9 are complete; Phase 9 (live traffic) is next. Everything on this page
is shipped and runs today. A fresh clone serves trained models without any download or training:
one trained pair is committed under `backend/release/`, and `make dev`, `make models` and the
container all install it into `backend/artifacts/` when nothing is serving there. A model you train
yourself always takes precedence over it. The one v1 endpoint still unbuilt, `POST /ingest/start`,
answers `501` naming Phase 9. See [Roadmap](Roadmap.md).

---

## Prerequisites

| Tool | Version | Why | Check command |
| ---- | ------- | --- | ------------- |
| Python | 3.12 (`backend/.python-version`); `pyproject.toml` allows `>=3.11,<3.13` | Runs the API and the training scripts. 3.13+ is excluded because the ML stack (torch, lightgbm, shap→numba) does not have settled wheels there yet. | `python --version` |
| uv | any recent release | Resolves and installs the backend from `uv.lock`, and runs every backend command (`uv run pytest`, `uv run alembic`). It also fetches the pinned Python for you. | `uv --version` |
| Node.js | 20.19+ or 22.12+ (the floor Vite 7 requires; `scripts/dev.py` prints exactly this hint). The container image uses `node:24-alpine`. | Runs the Vite dev server, the dashboard build and the vitest suite. | `node --version` |
| npm | ships with Node | Installs the frontend from `package-lock.json`. | `npm --version` |
| Docker | any version with the Compose v2 plugin | Optional. Only needed for the containerised stack (`make up`) and the Postgres profile. | `docker compose version` |
| git | any | Cloning, and publishing the documentation site. See [Docs Publishing](Docs-Publishing.md). | `git --version` |

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

Then open <http://localhost:5173>. That is the whole demo: the first start migrates an empty
database, installs the committed model release, and seeds it by replaying 28,869 real CICIDS2017
flows through the real pipeline (`backend/app/seed.py`), so the triage queue opens on real,
explained alerts. Set `IDS_SEED_ON_START=false` in `.env` to open on the empty states instead, and
`docker compose down -v` to start over.

The compose project is named `recluse`. Ports on the host come from `.env` (`BACKEND_PORT`,
`FRONTEND_PORT`), with `8000` and `5173` as the fallbacks.

| Service | Image / build | Host port | Container port | What it runs |
| ------- | ------------- | --------- | -------------- | ------------ |
| `backend` | built from `backend/Dockerfile` | `${BACKEND_PORT:-8000}` | 8000 | `backend/docker-entrypoint.sh`: migrate, install the release if nothing is serving, seed if the database is empty, then `uvicorn`, bound to `0.0.0.0`. Healthcheck polls `/api/v1/health`. |
| `frontend` | built from `frontend/Dockerfile`, target `serve` | `${FRONTEND_PORT:-5173}` | 80 | The built dashboard behind nginx, which proxies `/api` and the event stream (unbuffered) to `http://backend:8000`. Starts once the backend is healthy. |
| `postgres` | `postgres:17-alpine` | `${POSTGRES_PORT:-5432}` | 5432 | Not started by default. Behind the `postgres` profile; see below. |

State lives in two named volumes, `recluse-state` (the SQLite file) and `recluse-artifacts` (the
serving model), rather than in the checkout, so the container never leaves root-owned files behind
for a native `make dev`. The dataset, if you have downloaded it, is mounted read-only from `./data`:
with it, the Live screen offers the full held-out days for replay; without it, the committed demo
sample. For hot reload, run natively with `make dev`; the Dockerfile's `dev` stage is there for
anyone who wants the Vite server inside a container.

Postgres is opt-in and exists to demonstrate that swapping the database backend is configuration
only (a driver such as `psycopg` is not in the default dependency set):

```
docker compose --profile postgres up -d postgres
RECLUSE_CONTAINER_DATABASE_URL=postgresql+psycopg://ids:ids@postgres:5432/ids \
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
  "model_version": "stage1-lgbm-202610070057+stage2-autoencoder-202610070106",
  "uptime_s": 12.472
}
```

`model_version` names both stages of the bundle actually resident on `app.state.bundle` — the
committed release, unless you have trained your own — rather than a hardcoded string. With no
artifacts at all (the release removed, say) it reads `"unloaded"`, and `status` is still `"ok"`: no
model is an honest state, not a failure. `status` becomes `"degraded"` only when a bundle is present
but unusable. `python -m app.release status` (in `backend/`) says which model is serving and whether
it came from the release. See [ML Models](ML-Models.md) and
[Code: Backend Core](Code-Backend-Core.md).

The same request through the dev server proves the proxy works:

```
curl http://localhost:5173/api/v1/health
```

**`POST /ingest/start`** — the one endpoint a later phase still owns — answers `501` with a
machine-readable body naming that phase:

```
curl -i -X POST http://localhost:8000/api/v1/ingest/start
```

**`GET /api/v1/alerts`** returns an empty page on a fresh database. `make seed` fills it with a demo
built by the real pipeline from real flows — the container does this on first start:

```
make seed                          # or: cd backend && uv run python -m app.seed
curl -s http://localhost:8000/api/v1/alerts/stats
```

To watch alerts arrive live, start a replay and follow the stream. `demo` is the committed sample
and needs no download; `test` and `val` need the dataset (`GET /api/v1/replay/datasets` lists what
this install has):

```
curl -s -X POST http://localhost:8000/api/v1/replay/start   -H 'Content-Type: application/json' -d '{"speed": 10, "dataset": "demo"}'
curl -N http://localhost:8000/api/v1/stream
```

Both of those are a correct install. See [API Reference](API-Reference.md) and
`reports/phase5_api.md`.

**The interactive docs.** Open `http://localhost:8000/docs` for Swagger UI, or fetch the raw schema
at `http://localhost:8000/openapi.json`. The full v1 surface has been registered since Phase 0, so the
schema is complete, and the generated frontend types cover every route. The schema is also committed
as a contract snapshot (`backend/tests/snapshots/openapi.json`); `make openapi` rewrites it and
regenerates the types after a deliberate change.

**The test suites.**

| Suite | make | PowerShell | Underlying command |
| ----- | ---- | ---------- | ------------------ |
| Both | `make test` | `./make.ps1 test` | the two below, in order |
| Backend | `make test-backend` | `./make.ps1 test-backend` | `cd backend && uv run pytest` |
| Frontend | `make test-frontend` | `./make.ps1 test-frontend` | `cd frontend && npm run test` (`vitest run`) |

The backend suite builds its own throwaway database from the Alembic history and installs the
committed release into a throwaway artifacts directory, so it passes on a clean clone and never
touches your data. The frontend suite has one live-integration block that is skipped automatically
unless a backend is answering at `VITE_DEV_PROXY_TARGET`, so both suites pass with nothing else
running. Details in [Testing](Testing.md).

---

## Building the dataset

Nothing above needs the dataset — the API and the dashboard run without it. The
Phase 1 pipeline does, and it is roughly 900 MB of CSV plus a few minutes of
processing.

```
make data-fetch      # download CICIDS2017 into data/raw/  (~885 MB)
make data            # clean, split and fit the preprocessing bundle
```

`data-fetch` pulls the Kaggle mirror of the MachineLearningCSV release. The
dataset's own distribution point at
[unb.ca](https://www.unb.ca/cic/datasets/ids-2017.html) sits behind a licence
form that cannot be scripted; if you would rather take it from there, unpack
the CSVs into `data/raw/` yourself and skip straight to `make data`.

`make data` prints the phase checkpoint: what cleaning removed, and row counts
per split per class. Three stages run behind it, and each can be run alone when
you are changing one of them:

| Target | Does |
| --- | --- |
| `make data-clean` | `data/raw/*.csv` -> `data/interim/*.parquet`, one file per capture day |
| `make data-split` | `data/interim/` -> `data/processed/{train,val,test,benign_train}.parquet` |
| `make data-fit` | fits the scaler on the training split and writes `backend/artifacts/preprocessing.pkl` |

Everything those produce is gitignored. It is reproducible output, not source,
and `data/` plus `backend/artifacts/` run to well over a gigabyte.

Once the bundle exists the API loads it at startup and re-checks its schema
hash. A preprocessing bundle is not a model, and it has two authors: Phase 1
writes it under its own port encoding and training overwrites it with the
champion's. Running `make data` over a serving model — the installed release
included — can therefore leave a pair the API refuses to start on. Training
puts the champion's own bundle back; to return to the release instead, run
`uv run python -m app.release install --force` in `backend/`, which moves the
displaced files aside rather than deleting them.

The measured results of this repository's own run are in
[Roadmap](Roadmap.md#measured-on-the-real-release).

## Training Stage 1 (Phase 2)

| Command | What it does |
| --- | --- |
| `make train` | Trains the RandomForest baseline and evaluates it on the held-out test day |
| `make train-lgbm` | Trains the LightGBM upgrade; promoted only if it beats the baseline on the validation day |
| `make evaluate` | Re-scores the promoted champion and rewrites `reports/phase2_supervised.md` |
| `make ablation-port` | Raw vs. bucketed destination port, into `reports/port_ablation.md` |

The order matters. `make train` needs `make data` to have run, and `make
train-lgbm` compares itself against the baseline recorded in
`model_card.json` — running it first leaves nothing to compare with. Expect
roughly three minutes for the forest on a sixteen-core machine and under one
for LightGBM.

After that, `/api/v1/health` reports a real version such as
`stage1-lgbm-202609281410`.

One ordering hazard is worth knowing about, though it now repairs itself.
Re-running `make data` *after* training refits `preprocessing.pkl` under Phase
1's default port encoding, which no longer matches a champion trained under
the bucketed one. The API refuses to start on that mismatch, which is the
designed behaviour — a model paired with the wrong scaler scores confidently
and wrongly. `make data` warns when it does this, and the next `make train`
puts the champion's own bundle back, so the recovery is the command you were
going to run anyway.

The measured results are in
[Roadmap](Roadmap.md#phase-2--supervised-classifier).

## Training Stage 2 (Phase 3)

| Command | What it does |
| --- | --- |
| `make train-anomaly` | Fits the benign-only autoencoder, cuts `tau_anom`, runs the PyOD baselines and writes `reports/phase3_anomaly.md` |
| `make ablation-input` | Re-chooses the Stage 2 input clip bound on the validation day, into `reports/input_ablation.md` |

`make train-anomaly` needs a promoted Stage 1, not because it uses the model
but because it uses the model's *feature contract*: both stages read one matrix
at serving time, so Stage 2 is fitted against the canonical
`preprocessing.pkl` and refuses to guess when it is missing. Expect roughly
fifty minutes on a sixteen-core CPU machine for the fit over 1.2M benign rows,
plus a few minutes for the baselines. `--no-baselines` skips the slow part.

After that, `/api/v1/health` still reports the Stage 1 version — the card
records one served pair — but `ModelBundle.stage2_ready` is true and
`tau_anom` is loaded.

The measured results are in
[Roadmap](Roadmap.md#phase-3--anomaly-detector).

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
| Download CICIDS2017 into `data/raw/` | `make data-fetch` / `./make.ps1 data-fetch` |
| Clean, split and fit the whole data pipeline | `make data` / `./make.ps1 data` |

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
