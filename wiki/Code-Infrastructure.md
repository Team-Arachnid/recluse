# Code Reference — Build, Run and Infrastructure

This page documents every file that builds, runs, configures, publishes or ignores the repository: the two task runners, the native dev launcher, the container stack, the Python project definition, the git hygiene files, the documentation toolchain that mirrors `wiki/` to the GitHub wiki, and the placeholder directories that keep reproducible output out of version control. Read it if you are setting the project up, changing how it starts, adding a dependency, or trying to work out why something you produced locally is not in git.

| File | Lines | Role |
| --- | --- | --- |
| `Makefile` | 100 | GNU make task runner; the canonical target list |
| `make.ps1` | 191 | PowerShell mirror of every Makefile target, for Windows |
| `scripts/dev.py` | 179 | Supervises the API and the dashboard as one foreground process |
| `docker-compose.yml` | 105 | Containerised stack: backend, frontend, optional Postgres |
| `backend/Dockerfile` | 33 | Single-stage uv image for the API |
| `frontend/Dockerfile` | 30 | Four-stage image: deps, dev, build, static serve |
| `frontend/nginx.conf` | 28 | Static hosting plus `/api` proxy for the production image |
| `.dockerignore` | 17 | Keeps datasets, caches and secrets out of the build context |
| `backend/pyproject.toml` | 61 | Dependencies, build backend, ruff and pytest configuration |
| `backend/.python-version` | 1 | Pins the interpreter uv provisions |
| `.env.example` | 55 | Template for every port, path and threshold input |
| `.gitignore` | 63 | Excludes data, artifacts, databases and secrets |
| `.gitattributes` | 25 | Line endings, binary types, generated-file marking |
| `backend/uv.lock` | generated | Exact backend dependency resolution; installed with `uv sync --locked` |
| `frontend/package-lock.json` | generated | Exact frontend dependency resolution; installed with `npm ci` |
| `scripts/publish_wiki.py` | 361 | Mirrors `wiki/` into the separate GitHub wiki repository |
| `scripts/install_hooks.py` | 169 | Copies `scripts/hooks/` into the repository's git hooks directory |
| `scripts/hooks/post-commit` | 56 | Publishes the wiki after a commit that touched `wiki/`, on an allowed branch |
| `.github/workflows/publish-wiki.yml` | 62 | The only GitHub Actions workflow: mirrors `wiki/` on push to `main` |
| `data/raw/.gitkeep`, `data/interim/.gitkeep`, `data/processed/.gitkeep`, `reports/.gitkeep` | 0 each | Keep otherwise-ignored output directories present in a clone |
| `backend/artifacts/.gitkeep`, `backend/artifacts/README.md` | 0, 32 | Artifact directory placeholder and its contract |

---

## Task runners

The two runners are intentionally kept in lockstep: the `Makefile` is the canonical list, and `make.ps1` mirrors it target for target. Every command in [Getting Started](Getting-Started) is written so that both `make <target>` and `./make.ps1 <target>` work.

### Every target, both runners

| Target | make | make.ps1 | What it does |
| --- | --- | --- | --- |
| `help` | `make` or `make help` | `./make.ps1` or `./make.ps1 help` | Lists the targets. `make` greps its own `## ` comments; `make.ps1` prints a hardcoded ordered hashtable. |
| `env` | `make env` | `./make.ps1 env` | Copies `.env.example` to `.env` if `.env` is absent. No-op otherwise. |
| `install` | `make install` | `./make.ps1 install` | `uv sync` in `backend/`, then `npm install --no-fund` in `frontend/`. Depends on `env`. |
| `dev` | `make dev` | `./make.ps1 dev` | Runs `python scripts/dev.py` from the repo root — both processes, hot reload. Depends on `env`. |
| `backend` | `make backend` | `./make.ps1 backend` | `uv run uvicorn app.main:app --reload` in `backend/`. Depends on `env`. |
| `frontend` | `make frontend` | `./make.ps1 frontend` | `npm run dev` in `frontend/`. |
| `migrate` | `make migrate` | `./make.ps1 migrate` | `uv run alembic upgrade head` in `backend/`. Depends on `env`. |
| `revision` | `make revision m="add drift table"` | `./make.ps1 revision -m "add drift table"` | `uv run alembic revision --autogenerate -m <message>`. See [Code Reference — Migrations](Code-Backend-Migrations). |
| `test` | `make test` | `./make.ps1 test` | Backend pytest then frontend vitest. |
| `test-backend` | `make test-backend` | `./make.ps1 test-backend` | `uv run pytest` in `backend/`. |
| `test-frontend` | `make test-frontend` | `./make.ps1 test-frontend` | `npm run test` in `frontend/` (`vitest run`). |
| `lint` | `make lint` | `./make.ps1 lint` | `uv run ruff check .` in `backend/`, then `npm run typecheck` in `frontend/`. |
| `format` | `make format` | `./make.ps1 format` | `uv run ruff format .` then `uv run ruff check --fix .`, backend only. |
| `typecheck` | `make typecheck` | `./make.ps1 typecheck` | `npm run typecheck` — `tsc --noEmit` against both tsconfigs. |
| `gen-types` | `make gen-types` | `./make.ps1 gen-types` | `npm run gen:types`; regenerates `frontend/src/types/api.d.ts` from the running backend's OpenAPI schema. |
| `build` | `make build` | `./make.ps1 build` | `npm run build` — typecheck then `vite build` into `frontend/dist`. |
| `up` | `make up` | `./make.ps1 up` | `docker compose up --build`. Depends on `env`. |
| `down` | `make down` | `./make.ps1 down` | `docker compose down`. |
| `logs` | `make logs` | `./make.ps1 logs` | `docker compose logs -f`. |
| `ps` | `make ps` | `./make.ps1 ps` | `docker compose ps`. |
| `wiki` | `make wiki` | `./make.ps1 wiki` | `python scripts/publish_wiki.py` from the repo root — publishes `wiki/` to the GitHub wiki, no-op when unchanged. |
| `wiki-check` | `make wiki-check` | `./make.ps1 wiki-check` | `python scripts/publish_wiki.py --check` — exits 1 if the mirror is behind, pushes nothing. |
| `hooks` | `make hooks` | `./make.ps1 hooks` | `python scripts/install_hooks.py` — copies `scripts/hooks/` into the git hooks directory. |
| `hooks-uninstall` | `make hooks-uninstall` | `./make.ps1 hooks-uninstall` | `python scripts/install_hooks.py --uninstall` — removes only hooks carrying this repository's marker line. |
| `clean` | `make clean` | `./make.ps1 clean` | Removes `frontend/dist`, `frontend/node_modules/.vite`, `backend/.pytest_cache`, `backend/.ruff_cache`, every `__pycache__`, and the dev SQLite files. |

Twenty-five targets in all. `make help` prints all twenty-five — every target carries a `## ` comment, `help` included — in alphabetical order, because the recipe pipes through `sort`. `./make.ps1 help` prints twenty-four: its hand-maintained table covers every target except `help` itself, in its own order.

**Where the two diverge.** The target set is identical; only five mechanical details differ.

- `revision` takes its message differently. `make` uses a variable, `m="..."`, because that is how make passes arguments. `make.ps1` takes `-m "..."` as trailing arguments, strips the literal `-m` token, and joins the remainder — and throws `Usage: ./make.ps1 revision -m "message"` if nothing is left.
- `help` is derived in `make` and hardcoded in `make.ps1`. The Makefile's `help` recipe greps `^[a-zA-Z_-]+:.*?## ` out of `$(MAKEFILE_LIST)`, sorts, and formats with `awk`, so a new target documents itself. PowerShell has no equivalent one-liner, so `Show-Help` carries an explicit `[ordered]@{ }` list that must be updated alongside a new target.
- `clean` uses `rm -rf` / `find` in make and `Remove-Item -Recurse -Force` / `Get-ChildItem -Recurse -Directory -Filter '__pycache__'` in PowerShell. `make.ps1` also prints `cleaned` at the end; make is silent.
- Prerequisite chaining is native in make. Five targets declare `env` as a prerequisite — `install: env`, `dev: env`, `backend: env`, `migrate: env` and `up: env`. `make.ps1` has no dependency graph, so each of those five branches calls `Initialize-EnvFile` explicitly as its first step.
- Aggregate targets differ in mechanism. `make test` is `test: test-backend test-frontend`, and make runs the two targets. `./make.ps1 test` inlines both `Invoke-Step` calls rather than re-dispatching through the `switch`. The observable behaviour is the same — backend first, then frontend, stopping on the first failure.

### Makefile

**Path:** `Makefile` — GNU make task runner and the canonical definition of the project's commands.

#### What it does

Every routine action in the project — install, run, migrate, test, lint, containerise, clean — is a make target, so the documented command is short and the underlying invocation can change without the documentation going stale. The recipes are deliberately thin: each one is a `cd` into `backend/` or `frontend/` followed by the real tool (`uv`, `npm`, `docker compose`). There is no build logic in the Makefile itself.

The header comment states the Windows situation directly: GNU make is not installed by default on Windows, and `make.ps1` mirrors every target below. `SHELL := /bin/sh` pins the recipe shell, and `.DEFAULT_GOAL := help` means a bare `make` prints the target list instead of running the first target.

Self-documenting help is the one piece of real logic. Each target carries a `## description` comment, and `help` extracts them:

```make
help: ## Show the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
```

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `SHELL` | Variable | `SHELL := /bin/sh` | Pins the recipe shell so behaviour does not depend on the invoking shell. |
| `.DEFAULT_GOAL` | Special variable | `.DEFAULT_GOAL := help` | A bare `make` prints the target list. |
| `BACKEND` | Variable | `BACKEND := backend` | Backend directory name used by every backend recipe. |
| `FRONTEND` | Variable | `FRONTEND := frontend` | Frontend directory name. |
| `UV` | Variable | `UV := uv` | Python package/runner command. |
| `NPM` | Variable | `NPM := npm` | Node package/runner command. |
| `COMPOSE` | Variable | `COMPOSE := docker compose` | Compose v2 invocation (subcommand, not the legacy `docker-compose` binary). |
| `.PHONY` | Special target | `.PHONY: help env install dev backend frontend migrate revision test test-backend test-frontend lint format typecheck gen-types build up down logs ps clean wiki wiki-check hooks hooks-uninstall` | Declares all twenty-five targets as non-file so a same-named file cannot shadow it. Written across three backslash-continued lines in the source. |
| `help` | Target | `help:` | Greps `## ` comments out of `$(MAKEFILE_LIST)` and formats them. |
| `env` | Target | `env:` | `test -f .env \|\| (cp .env.example .env && echo "created .env")`. |
| `install` | Target | `install: env` | `uv sync` plus `npm install --no-fund`. |
| `dev` | Target | `dev: env` | `python scripts/dev.py`. |
| `backend` | Target | `backend: env` | `uv run uvicorn app.main:app --reload`. |
| `frontend` | Target | `frontend:` | `npm run dev`. |
| `migrate` | Target | `migrate: env` | `uv run alembic upgrade head`. |
| `revision` | Target | `revision:` | `uv run alembic revision --autogenerate -m "$(m)"`. |
| `test` | Target | `test: test-backend test-frontend` | Aggregate; runs both suites in order. |
| `test-backend` | Target | `test-backend:` | `uv run pytest`. |
| `test-frontend` | Target | `test-frontend:` | `npm run test`. |
| `lint` | Target | `lint:` | `uv run ruff check .` then `npm run typecheck`. |
| `format` | Target | `format:` | `uv run ruff format .` then `uv run ruff check --fix .`. |
| `typecheck` | Target | `typecheck:` | `npm run typecheck`. |
| `gen-types` | Target | `gen-types:` | `npm run gen:types`. |
| `build` | Target | `build:` | `npm run build`. |
| `up` | Target | `up: env` | `docker compose up --build`. |
| `down` | Target | `down:` | `docker compose down`. |
| `logs` | Target | `logs:` | `docker compose logs -f`. |
| `ps` | Target | `ps:` | `docker compose ps`. |
| `wiki` | Target | `wiki:` | `python scripts/publish_wiki.py`. |
| `wiki-check` | Target | `wiki-check:` | `python scripts/publish_wiki.py --check`. |
| `hooks` | Target | `hooks:` | `python scripts/install_hooks.py`. |
| `hooks-uninstall` | Target | `hooks-uninstall:` | `python scripts/install_hooks.py --uninstall`. |
| `clean` | Target | `clean:` | Removes build output, caches and `data/ids.db*`. |

#### Notes

- Targets that need configuration declare `env` as a prerequisite rather than assuming `.env` exists, so a fresh clone can go straight to `make dev`.
- `wiki`, `wiki-check`, `hooks` and `hooks-uninstall` are the only targets besides `dev` that invoke the interpreter found as `python` on `PATH` rather than going through `uv run`. That is deliberate — all four scripts import only the standard library, so they work before `uv sync` has ever run — but it means they fail on a machine whose only Python lives inside `backend/.venv`. Every other backend recipe goes through `uv run` and therefore uses the project virtualenv.
- `install` intentionally runs `npm install --no-fund`, not `npm ci`; `npm ci` is reserved for the Docker build, where a reproducible lockfile install is what is wanted.
- `clean` deletes `data/ids.db`, `data/ids.db-wal` and `data/ids.db-shm`. The dev database is disposable — it is rebuilt by `alembic upgrade head`.
- `revision` is the one target with no `env` prerequisite even though it touches the database URL; run `make env` first on a fresh clone.
- Status: implemented.

### make.ps1

**Path:** `make.ps1` — PowerShell stand-in for GNU make so every documented command works on Windows with nothing extra installed.

#### What it does

Windows does not ship GNU make, and requiring contributors to install it before the first command works is friction that shows up in the first five minutes of the project. `make.ps1` removes that: it accepts the same target names as the Makefile and dispatches through a `switch` to the same underlying tool invocations. The comment-based help at the top of the file also points at `winget install ezwinports.make` for anyone who would rather use the real thing.

Everything runs through one helper, `Invoke-Step`, which pushes into a working directory, invokes the command, and — critically — checks `$LASTEXITCODE` and throws when it is non-zero. Native executables do not raise terminating errors in PowerShell, so without that check a failed `pytest` inside `test` would be followed cheerfully by the frontend suite and the script would exit 0. `$ErrorActionPreference = 'Stop'` at the top makes the thrown error fatal. The `finally` block guarantees `Pop-Location` even on failure.

Paths are derived from `$PSScriptRoot`, not from the current directory, so `./make.ps1 test` behaves the same regardless of where it is invoked from.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `$Target` | Parameter | `[Parameter(Position = 0)] [string]$Target = 'help'` | The target name; defaults to `help`. |
| `$Rest` | Parameter | `[Parameter(ValueFromRemainingArguments = $true)] [string[]]$Rest` | Trailing arguments, used by `revision`. |
| `$RepoRoot` | Variable | `$RepoRoot = $PSScriptRoot` | Repo root, independent of the caller's working directory. |
| `$Backend` | Variable | `$Backend = Join-Path $RepoRoot 'backend'` | Backend directory. |
| `$Frontend` | Variable | `$Frontend = Join-Path $RepoRoot 'frontend'` | Frontend directory. |
| `Invoke-Step` | Function | `Invoke-Step -WorkingDirectory <string> -Command <string> [-Arguments <string[]>]` | Runs a command in a directory and throws on a non-zero `$LASTEXITCODE`; always pops the location. |
| `Initialize-EnvFile` | Function | `Initialize-EnvFile` | Copies `.env.example` to `.env` if absent and prints `created .env`. |
| `Show-Help` | Function | `Show-Help` | Prints the ordered target/description table — a hand-maintained `[ordered]@{}` of 24 entries (every target except `help`), formatted with `'    {0,-15} {1}'`. |
| `switch ($Target)` | Dispatch | `switch ($Target) { ... }` | Maps each target name to its steps; the `default` branch prints `unknown target: <name>`, shows help and exits 1. |

#### Notes

- `Invoke-Step`'s exit-code check is the reason `./make.ps1 test` fails the build when the backend suite fails. Removing it would make the script silently green.
- `revision` filters the literal `-m` token out of `$Rest` and joins the remainder, so `./make.ps1 revision -m "add drift table"` and `./make.ps1 revision "add drift table"` both work.
- `Show-Help` is a hand-maintained list of 24 entries, and its order is neither the alphabetical order `make help` prints (that recipe pipes through `sort`) nor exactly the Makefile's source order — `clean` is listed before the four documentation targets, where the Makefile defines it after them. Adding a Makefile target means adding it here too; the script cannot derive it, and nothing checks that the two lists agree.
- The format string is `'    {0,-15} {1}'`. Fifteen characters is exactly the length of the longest target name, `hooks-uninstall`, so every description lines up in the same column.
- `clean` uses `-ErrorAction SilentlyContinue` throughout, so cleaning a tree that was never built is not an error.
- `.gitattributes` keeps this file CRLF (`*.ps1 text eol=crlf`).
- Status: implemented.

---

## Local development

### scripts/dev.py

**Path:** `scripts/dev.py` — supervises the API and the dashboard as a single foreground process for `make dev`.

#### What it does

Running two dev servers together is the common case, and the obvious shell one-liner (`cmd_a & cmd_b & wait`) is not portable across GNU make on Linux, Git Bash on Windows and PowerShell. This script is the portable version. It does four things before starting anything: verifies `uv` and `npm` are on `PATH` with an actionable message when they are not, creates `.env` from `.env.example` if missing, installs dependencies if `backend/.venv` or `frontend/node_modules` is absent, and applies migrations with `alembic upgrade head`. That ordering means a fresh clone reaches a working stack from `make dev` alone.

Ports are read from `.env` by `read_env()`, a deliberately small parser that skips blanks and `#` comments, splits on the first `=`, strips inline comments after a `#`, and strips surrounding quotes. It is not a dotenv implementation — it only needs `IDS_HOST`, `IDS_PORT` and `VITE_DEV_SERVER_PORT` — but it means the URLs printed at startup are the ones actually served, with defaults of `127.0.0.1`, `8000` and `5173` when `.env` is absent or silent. The backend host and port are passed explicitly to uvicorn as `--host` and `--port`; the frontend is started as plain `npm run dev` and picks its port up from the same repo-root `.env` through `vite.config.ts`.

Both children are launched with `stdout=subprocess.PIPE`, `stderr=subprocess.STDOUT` and `text=True`, and each gets a daemon thread running `pump()` that prefixes every line with a coloured `[backend]` or `[frontend]` tag. On Windows the children are started with `shell=True`, because `npm` is a `.cmd` shim, and with `subprocess.CREATE_NEW_PROCESS_GROUP`. The process group matters: when `uvicorn --reload` restarts its worker on Windows it raises a console control event that would otherwise propagate to the launcher and take the frontend down with it, so editing one backend file would kill the dashboard.

Shutdown has three paths, all of which converge on the same `finally` block. The main loop polls both children every 200 ms (via `thread.join(timeout=0.2)`); if either exits, the launcher logs `<source> exited with code <n> -- shutting down` and returns that code, or `1` if the code was 0 — a crashed backend is never hidden behind a still-running frontend. `KeyboardInterrupt` logs `stopping` and returns 0. In every case the `finally` block calls `terminate()` on each still-running child, waits up to 10 seconds, and escalates to `kill()` with a `<source> did not stop -- killing` message on `subprocess.TimeoutExpired`.

**CLI flags:** the script takes none. It is invoked as `python scripts/dev.py` with no arguments, defines no `argparse` parser, and reads no `sys.argv`. Everything it needs comes from `.env` and from what is present on disk. To change ports, edit `.env`; to run one side only, use `make backend` or `make frontend`.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `REPO_ROOT` | Constant | `REPO_ROOT = Path(__file__).resolve().parents[1]` | Repo root, derived from the script location. |
| `BACKEND_DIR` | Constant | `BACKEND_DIR = REPO_ROOT / "backend"` | Working directory for uvicorn, uv and alembic. |
| `FRONTEND_DIR` | Constant | `FRONTEND_DIR = REPO_ROOT / "frontend"` | Working directory for npm. |
| `RESET` | Constant | `RESET = "\033[0m"` | ANSI reset sequence. |
| `COLOURS` | Constant | `COLOURS = {"backend": "\033[36m", "frontend": "\033[35m", "setup": "\033[33m"}` | Cyan, magenta and yellow prefixes for the three log sources. |
| `log` | Function | `log(source: str, message: str) -> None` | Prints `[source] message` with the source colour, `flush=True`. |
| `read_env` | Function | `read_env() -> dict[str, str]` | Parses `.env` far enough to find ports; ignores comments and inline comments; returns `{}` if the file is absent. |
| `ensure_env_file` | Function | `ensure_env_file() -> None` | Copies `.env.example` to `.env` when missing and logs it. |
| `require` | Function | `require(tool: str, hint: str) -> None` | Raises `SystemExit(1)` with a hint when `shutil.which(tool)` is `None`. |
| `ensure_installed` | Function | `ensure_installed() -> None` | Runs `uv sync` if `backend/.venv` is missing, `npm install --no-fund` if `frontend/node_modules` is missing. |
| `migrate` | Function | `migrate() -> None` | Runs `uv run alembic upgrade head` in `backend/` with `check=True`. |
| `pump` | Function | `pump(source: str, process: subprocess.Popen[str]) -> None` | Reads a child's merged stdout line by line and logs each line with its source prefix. |
| `main` | Function | `main() -> int` | Performs preflight, starts both children, supervises them, returns the process exit code. |

#### Notes

- Preflight order is deliberate: tool checks, then `.env`, then install, then migrate, then launch. Each step's failure message is specific — `'uv' not found on PATH. Install from https://docs.astral.sh/uv/` and `'npm' not found on PATH. Install Node.js 20.19+ or 22.12+`.
- Children inherit `os.environ` plus `PYTHONUNBUFFERED=1` and `FORCE_COLOR=1`, so output appears immediately and stays coloured through the pipe.
- The output threads are daemons, so they never block interpreter exit; the `finally` block, not the threads, is what guarantees the children die.
- Exit code semantics: a child exiting 0 still makes the launcher return 1 (`return code or 1`), because in dev a server exiting cleanly on its own is still a failure of `make dev`.
- The startup banner prints `http://localhost:<IDS_PORT>/api/v1/health`, `http://localhost:<IDS_PORT>/docs` and `http://localhost:<VITE_DEV_SERVER_PORT>`.
- Status: implemented.

---

## Containers

### docker-compose.yml

**Path:** `docker-compose.yml` — the containerised stack: the API, the dashboard dev server, and an optional Postgres for portability testing.

#### What it does

The compose file is what `make up` runs. It declares `name: recluse` so the project name does not depend on the directory name, and it sets the build context to the repo root (`context: .`) for both services, with the Dockerfile named explicitly. That is intentional: both images mirror the repo layout internally, so path resolution in `app/config.py` behaves identically inside and outside a container.

Both application services load `.env` with `required: false`, so the stack boots on defaults alone, then override the values that must differ in a container. The backend binds `0.0.0.0` regardless of what `.env` says for local runs, and points its data, artifacts and reports directories at absolute paths under `/srv`. The frontend points its Vite proxy at `http://backend:8000` — the compose service name, not `localhost`, because the proxy runs inside the network.

#### Service: `backend`

| Setting | Value |
| --- | --- |
| Build | `context: .`, `dockerfile: backend/Dockerfile` |
| `env_file` | `.env`, `required: false` |
| Environment | `IDS_HOST: 0.0.0.0`, `IDS_PORT: "8000"`, `IDS_DATABASE_URL: sqlite+pysqlite:///data/ids.db`, `IDS_DATA_DIR: /srv/data`, `IDS_ARTIFACTS_DIR: /srv/backend/artifacts`, `IDS_REPORTS_DIR: /srv/reports`, `IDS_CORS_ORIGINS: http://localhost:${FRONTEND_PORT:-5173},http://127.0.0.1:${FRONTEND_PORT:-5173}` |
| Ports | `"${BACKEND_PORT:-8000}:8000"` |
| Volumes | `./data:/srv/data`, `./backend/artifacts:/srv/backend/artifacts`, `./reports:/srv/reports` |
| `depends_on` | none |
| Healthcheck | `python -c "import urllib.request as u, sys; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)"`, `interval: 10s`, `timeout: 5s`, `retries: 12`, `start_period: 15s` |
| Restart | `unless-stopped` |

#### Service: `frontend`

| Setting | Value |
| --- | --- |
| Build | `context: .`, `dockerfile: frontend/Dockerfile`, `target: dev` |
| `env_file` | `.env`, `required: false` |
| Environment | `VITE_DEV_SERVER_HOST: 0.0.0.0`, `VITE_DEV_SERVER_PORT: "5173"`, `VITE_DEV_PROXY_TARGET: http://backend:8000`, `VITE_API_BASE_URL: /api/v1` |
| Ports | `"${FRONTEND_PORT:-5173}:5173"` |
| Volumes | `./frontend/src:/srv/frontend/src:ro`, `./frontend/index.html:/srv/frontend/index.html:ro`, `./frontend/vite.config.ts:/srv/frontend/vite.config.ts:ro` |
| `depends_on` | `backend`, `condition: service_healthy` |
| Healthcheck | none |
| Restart | `unless-stopped` |

#### Service: `postgres`

| Setting | Value |
| --- | --- |
| Image | `postgres:17-alpine` |
| Profiles | `["postgres"]` — not started by a plain `docker compose up` |
| Environment | `POSTGRES_USER: ${POSTGRES_USER:-ids}`, `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-ids}`, `POSTGRES_DB: ${POSTGRES_DB:-ids}` |
| Ports | `"${POSTGRES_PORT:-5432}:5432"` |
| Volumes | `postgres-data:/var/lib/postgresql/data` (named volume) |
| `depends_on` | none |
| Healthcheck | `pg_isready -U ${POSTGRES_USER:-ids} -d ${POSTGRES_DB:-ids}`, `interval: 10s`, `timeout: 5s`, `retries: 10` |
| Restart | not set |

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `name` | Top-level key | `name: recluse` | Compose project name, so resource names do not depend on the checkout directory. |
| `services.backend` | Service | `build: {context: ., dockerfile: backend/Dockerfile}` | FastAPI API; migrates then serves; owns the healthcheck the frontend waits on. |
| `services.frontend` | Service | `build: {context: ., dockerfile: frontend/Dockerfile, target: dev}` | Vite dev server with HMR, proxying `/api` to `backend:8000`. |
| `services.postgres` | Service | `image: postgres:17-alpine`, `profiles: ["postgres"]` | Opt-in Postgres for verifying the portable-schema claim. |
| `volumes.postgres-data` | Named volume | `postgres-data:` | Persists the Postgres data directory across `docker compose down`. |

#### Notes

- **Build order.** Compose builds both images in parallel, but startup is ordered: `frontend.depends_on.backend.condition: service_healthy` holds the dashboard back until the backend healthcheck passes. With `start_period: 15s` and up to 12 retries at 10 s, the backend has room to run `alembic upgrade head` before it is considered unhealthy. The backend's own CMD is `alembic upgrade head && exec uvicorn ...`, so an empty `./data` volume is migrated on first boot.
- **How the frontend is served.** Compose builds the `dev` stage, so in the default stack the dashboard is the Vite dev server, not nginx — HMR works, and `/api` requests are proxied in-process to `http://backend:8000`, keeping the browser same-origin so CORS never applies. The static path exists in the same Dockerfile: the `build` stage produces `dist`, and the `serve` stage copies it into `nginx:1.29-alpine` with `frontend/nginx.conf`, where nginx serves the bundle and proxies `/api/` to `backend:8000`. That stage is for Phase 8 packaging and is not referenced by this compose file.
- **Why only source is mounted on the frontend.** Mounting all of `./frontend` would shadow the image's `node_modules` with the host's, breaking platform-specific binaries (esbuild, rollup). Only `src`, `index.html` and `vite.config.ts` are mounted, read-only.
- **Why the backend mounts three host directories.** Datasets, the SQLite file, model artifacts and reports are reproducible outputs, not image contents. Keeping them on the host means `docker compose down` does not destroy a training run.
- `IDS_DATABASE_URL` stays relative (`sqlite+pysqlite:///data/ids.db`) even in the container. `Settings.sqlalchemy_url` anchors a relative SQLite path to the repo root, which inside the image is `/srv`, so the file lands at `/srv/data/ids.db` — the mounted `./data`. See [Configuration](Configuration).
- The Postgres profile is a demonstration of the portability claim, not a supported deployment. Starting it is `docker compose --profile postgres up -d postgres`, then running the backend with `IDS_DATABASE_URL=postgresql+psycopg://ids:ids@postgres:5432/ids`. The claim itself is asserted by `backend/tests/test_schema_portability.py` — see [Code Reference — Tests](Code-Backend-Tests) and [Database Schema](Database-Schema).
- **The four `POSTGRES_*` variables are settable but untemplated.** `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` and `POSTGRES_PORT` are compose-level interpolations with inline defaults (`ids`/`ids`/`ids`/`5432`) and are deliberately absent from `.env.example`, because the profile is a portability demonstration rather than a supported deployment. Set them in `.env` or in the shell if you need different credentials; they carry no `IDS_` prefix and the backend never reads them. The healthcheck interpolates the same two defaults again, so a changed user or database name is picked up by `pg_isready` too.
- Host port mapping is driven by `BACKEND_PORT` and `FRONTEND_PORT` from `.env`, with `8000` and `5173` as compose-level defaults; the container-side ports are fixed.
- Status: implemented.

### backend/Dockerfile

**Path:** `backend/Dockerfile` — builds the API image on the official uv + Python 3.12 base.

#### What it does

The image is single-stage and built from `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, so uv and a matching interpreter are present without a separate install step. `WORKDIR /srv/backend` is the load-bearing choice: it mirrors the repo layout, so the repo-root-relative path resolution in `app/config.py` (which walks up two parents from `app/config.py` to reach the repo root) resolves to `/srv` and behaves identically inside and outside a container.

Dependencies are installed in two passes to keep layer caching useful. The first copies only `pyproject.toml`, `uv.lock` and `.python-version` and runs `uv sync --locked --no-dev --no-install-project`, so editing application code does not force a re-resolve of the ML stack (torch, lightgbm, shap). The second copies the rest of `backend/` and runs `uv sync --locked --no-dev` to install the project itself. `--locked` means the lockfile is honoured exactly and the build fails rather than silently re-resolving; `--no-dev` leaves pytest, httpx and ruff out of the image. Both passes mount a uv cache at `/root/.cache/uv`.

Because the build context is the repo root, every `COPY` is written with the `backend/` prefix.

#### Build stages

| Stage | Base | Produces |
| --- | --- | --- |
| (single, unnamed) | `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` | `/srv/backend` containing the application and a `--no-dev` virtualenv at `/srv/backend/.venv`, on `PATH`, with a CMD that migrates then serves |

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `FROM` | Instruction | `FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim` | Base image with uv and Python 3.12. |
| `ENV` | Instruction | `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`, `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`, `UV_PROJECT_ENVIRONMENT=/srv/backend/.venv` | Unbuffered logs, no stray `.pyc` in the source tree, precompiled bytecode in the venv, copy instead of hardlink across the cache mount, and a fixed venv location. |
| `WORKDIR` | Instruction | `WORKDIR /srv/backend` | Mirrors the repo layout so `REPO_ROOT` resolves to `/srv`. |
| `COPY` (deps) | Instruction | `COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./` | Dependency manifests only, for layer caching. |
| `RUN` (deps) | Instruction | `uv sync --locked --no-dev --no-install-project` | Installs third-party dependencies without the project. |
| `COPY` (app) | Instruction | `COPY backend/ ./` | The application, migrations, training package and tests. |
| `RUN` (project) | Instruction | `uv sync --locked --no-dev` | Installs the project itself into the same venv. |
| `ENV PATH` | Instruction | `ENV PATH="/srv/backend/.venv/bin:$PATH"` | Makes `uvicorn` and `alembic` callable without `uv run`. |
| `EXPOSE` | Instruction | `EXPOSE 8000` | Documentation only; the published port comes from compose. |
| `CMD` | Instruction | `["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host ${IDS_HOST:-0.0.0.0} --port ${IDS_PORT:-8000}"]` | Migrates, then replaces the shell with uvicorn so signals reach the server. |

#### Notes

- `exec` in the CMD is deliberate: without it, `sh` stays as PID 1 and `docker stop` does not forward SIGTERM to uvicorn.
- Host and port are read from the environment with shell defaults, so nothing about the network is baked into the image.
- The image contains the test suite (it copies all of `backend/`) but not the test dependencies, since `--no-dev` excludes pytest.
- `UV_LINK_MODE=copy` avoids hardlink warnings when the uv cache is on a different filesystem from the target venv, which is the case with a cache mount.
- Status: implemented.

### frontend/Dockerfile

**Path:** `frontend/Dockerfile` — four-stage image providing both the dev server compose runs and the static production bundle.

#### What it does

One file covers two very different jobs. `deps` installs node modules once from the lockfile; `dev` and `build` both branch off it, so the dependency layer is shared and installed exactly once. `dev` is what `docker-compose.yml` targets: the Vite dev server with HMR, binding all interfaces because a container that binds loopback publishes a port that reaches nothing. `build` runs `npm run build` (which typechecks first, per `package.json`) and `serve` copies the resulting `dist` into nginx.

The `serve` stage is the Phase 8 packaging target. It is a complete stage — `docker build -f frontend/Dockerfile --target serve .` is the command that builds it — but nothing in the default stack references it, and no image has been built from it in this repository.

#### Build stages

| Stage | Base | Produces |
| --- | --- | --- |
| `deps` | `node:24-alpine` | `/srv/frontend/node_modules` installed with `npm ci --no-fund --no-audit` from `package.json` + `package-lock.json` |
| `dev` | `deps` | The full source plus `VITE_DEV_SERVER_HOST=0.0.0.0`; runs `npm run dev -- --host ... --port ...` on 5173 |
| `build` | `deps` | `/srv/frontend/dist` — the typechecked, minified production bundle |
| `serve` | `nginx:1.29-alpine` | Static image: `dist` at `/usr/share/nginx/html`, `nginx.conf` at `/etc/nginx/conf.d/default.conf`, listening on 80 |

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `deps` | Stage | `FROM node:24-alpine AS deps` | Lockfile-exact dependency install, shared by `dev` and `build`. |
| `npm ci` | Instruction | `RUN npm ci --no-fund --no-audit` | Reproducible install; fails if `package-lock.json` and `package.json` disagree. |
| `dev` | Stage | `FROM deps AS dev` | Development server stage targeted by `docker-compose.yml`. |
| `ENV VITE_DEV_SERVER_HOST` | Instruction | `ENV VITE_DEV_SERVER_HOST=0.0.0.0` | Binds all interfaces so the published port reaches the server. |
| `CMD` (dev) | Instruction | `["sh", "-c", "npm run dev -- --host ${VITE_DEV_SERVER_HOST:-0.0.0.0} --port ${VITE_DEV_SERVER_PORT:-5173}"]` | Passes host and port through to Vite, overriding `vite.config.ts`. |
| `build` | Stage | `FROM deps AS build` | Runs `npm run build`, which is `npm run typecheck && vite build`. |
| `serve` | Stage | `FROM nginx:1.29-alpine AS serve` | Static production image for Phase 8. |
| `COPY --from=build` | Instruction | `COPY --from=build /srv/frontend/dist /usr/share/nginx/html` | The only artifact carried out of the Node stages. |

#### Notes

- `dev` and `build` each `COPY frontend/ ./` separately rather than sharing a source layer, so a source edit invalidates only the stage being built.
- The `serve` image contains no Node runtime and no `node_modules`; it is nginx plus static files.
- The `--host`/`--port` flags on the dev CMD override `vite.config.ts`, which is what allows the container to bind `0.0.0.0` while local runs keep the dual-stack `::` default.
- Status: implemented. All four stages are written and none is a placeholder, but only `dev` is exercised by anything in the repository today; `serve` is the Phase 8 packaging target and is not part of the default `make up` stack.

### frontend/nginx.conf

**Path:** `frontend/nginx.conf` — the server block for the static production image, serving the SPA and proxying `/api/` to the backend.

#### What it does

Two concerns, one file. First, single-page-app routing: `try_files $uri $uri/ /index.html` means a deep link like `/alerts/1234` returns the app shell rather than a 404, and the client router resolves the path. Second, the API proxy: `/api/` is forwarded to `http://backend:8000` with the usual forwarded headers, so the browser talks to one origin and CORS never enters the picture in production.

The proxy block is tuned for the alert stream. The alert feed is server-sent events, and the nginx defaults would break it — response buffering holds events until a buffer fills, and the default `proxy_read_timeout` of 60 s would drop an idle stream. `proxy_http_version 1.1`, `proxy_buffering off`, `proxy_cache off` and `proxy_read_timeout 24h` are what make a long-lived SSE connection behave. See [API Reference](API-Reference) for the stream endpoint itself.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `server` | Block | `server { listen 80; server_name _; }` | Default server on port 80, matching any host. |
| `root` | Directive | `root /usr/share/nginx/html;` | Where the `serve` stage places the built bundle. |
| `index` | Directive | `index index.html;` | The SPA shell. |
| `location /` | Block | `try_files $uri $uri/ /index.html;` | Client-side routing fallback. |
| `location /api/` | Block | `proxy_pass http://backend:8000;` | Forwards API calls to the compose service name. |
| forwarded headers | Directives | `proxy_set_header Host $host;`, `X-Real-IP $remote_addr`, `X-Forwarded-For $proxy_add_x_forwarded_for`, `X-Forwarded-Proto $scheme` | Preserves client identity and scheme through the proxy. |
| SSE settings | Directives | `proxy_http_version 1.1;`, `proxy_buffering off;`, `proxy_cache off;`, `proxy_read_timeout 24h;` | Keeps the server-sent-events feed unbuffered and open. |

#### Notes

- `proxy_pass http://backend:8000` uses the compose service name, so this config only works inside a network where `backend` resolves.
- `proxy_pass` is written without a trailing path, so the full `/api/...` URI is forwarded unchanged and the backend's `IDS_API_V1_PREFIX` still matches.
- Used only by the `serve` stage of `frontend/Dockerfile`; the default compose stack runs the Vite dev server's own proxy instead.
- Status: implemented, unused by the default stack until Phase 8 packaging.

### .dockerignore

**Path:** `.dockerignore` — trims the build context so images are small, fast to build and free of secrets.

#### What it does

Both Dockerfiles use the repo root as their build context, which without this file would ship the entire working tree — including multi-gigabyte CICIDS2017 CSVs — to the daemon on every build. Each entry removes one class of thing that must not be in an image: version control, host-installed dependencies that would shadow the image's own, caches, datasets, reports, databases, and real `.env` files.

Shadowing is the most damaging case. If `node_modules` or `.venv` were copied in, the host's platform-specific binaries would overwrite the ones the image installed and the container would fail at runtime in a confusing way.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| VCS | Patterns | `.git`, `.gitignore` | History and ignore rules are not needed to build. |
| Host dependencies | Patterns | `**/node_modules`, `**/.venv` | Prevents host installs from shadowing the image's own. |
| Build output | Pattern | `**/dist` | The image builds its own bundle. |
| Caches | Patterns | `**/__pycache__`, `**/.pytest_cache`, `**/.ruff_cache` | Stale local caches. |
| Datasets | Patterns | `data/raw`, `data/interim`, `data/processed` | Large, reproducible from source; mounted at runtime instead. |
| Scratch and output | Patterns | `temp`, `reports` | Working files and generated reports. |
| Databases | Patterns | `*.db`, `*.sqlite3` | The dev database is disposable and rebuilt by migrations. |
| Secrets | Patterns | `.env`, `.env.local` | Real configuration is injected at runtime by compose, never baked in. |

#### Notes

- `.env.example` is *not* ignored — only the concrete `.env` files are — so the template is available in the context.
- `data/` itself is not excluded, only its three subdirectories, so `.gitkeep` placeholders and the directory structure still exist in the image.
- The ignored `data`, `reports` and `backend/artifacts` directories are supplied as bind mounts by `docker-compose.yml`, which is why excluding them costs nothing.
- Status: implemented.

---

## Python project config

### backend/pyproject.toml

**Path:** `backend/pyproject.toml` — the backend's dependency set, packaging definition, and lint/test configuration.

#### What it does

This is the single source of truth for what the Python side installs and how it is checked. `requires-python = ">=3.11,<3.13"` is an upper bound with a stated reason: Python 3.13 and 3.14 do not yet have settled wheels for the whole ML stack (torch, lightgbm, and shap's numba dependency), so 3.12 is the supported target. The dependency list is grouped by purpose with comments — API, persistence, data, models — and several entries name the phase or model they exist for.

Packaging uses hatchling, with `packages = ["app", "training"]`. Both are shipped, which is the packaging expression of a core constraint: `backend/training/features.py` is imported by training *and* by serving, so the training package cannot be a dev-only extra. Dev tools live in a `[dependency-groups] dev` block that `uv sync` installs locally and `uv sync --no-dev` omits from the image.

#### Dependencies

| Package | Constraint | Why this project needs it |
| --- | --- | --- |
| `fastapi` | `>=0.115` | The API framework; supplies the v1 route surface and the OpenAPI schema the frontend types are generated from. |
| `uvicorn[standard]` | `>=0.32` | ASGI server used by `make dev`, `make backend` and the container CMD. |
| `pydantic` | `>=2.9` | Request/response schemas for every route. |
| `pydantic-settings` | `>=2.6` | Backs `app/config.py`; every port, path and threshold arrives through it. |
| `sqlalchemy` | `>=2.0` | ORM with the portable column types that keep SQLite and Postgres interchangeable. |
| `alembic` | `>=1.14` | Schema migrations; `make migrate` and the container's startup command. |
| `pandas` | `>=2.2` | Flow-record handling in the Phase 1 data pipeline. |
| `numpy` | `>=1.26` | Numeric arrays underneath feature extraction and both models. |
| `pyarrow` | `>=17.0` | Parquet writer for `data/interim` (Phase 1). |
| `scikit-learn` | `>=1.5` | Stage 1 baseline, `RandomForestClassifier`, plus preprocessing. |
| `lightgbm` | `>=4.5` | Stage 1 upgrade from the RandomForest baseline. |
| `torch` | `>=2.4` | Stage 2 autoencoder. |
| `shap` | `>=0.46` | TreeSHAP explanations for Stage 1 alerts. |

| Dev package | Constraint | Why |
| --- | --- | --- |
| `pytest` | `>=8.3` | The backend test suite. |
| `httpx` | `>=0.27` | Required by `fastapi.testclient`. |
| `ruff` | `>=0.7` | Lint and format. |

#### Tool configuration

| Block | Setting | What it enforces |
| --- | --- | --- |
| `[tool.hatch.build.targets.wheel]` | `packages = ["app", "training"]` | Both the service and the training package are installed, so serving code can import `training.features`. |
| `[tool.pytest.ini_options]` | `testpaths = ["tests"]` | `pytest` with no arguments collects only `backend/tests`. |
| | `pythonpath = ["."]` | `backend/` is on `sys.path`, so tests import `app.*` and `training.*` without an editable install step. |
| | `addopts = "-q --strict-markers"` | Quiet output, and an unregistered marker is an error rather than a silent typo. |
| | `markers = ["integration: needs a live backend process (deselect with -m 'not integration')"]` | Declares the one custom marker; tests needing a running server can be excluded with `-m 'not integration'`. |
| `[tool.ruff]` | `line-length = 100` | Line length for both the linter and the formatter. |
| | `target-version = "py312"` | Rules and rewrites target Python 3.12 syntax. |
| `[tool.ruff.lint]` | `select = ["E", "F", "I", "UP", "B", "W"]` | pycodestyle errors, pyflakes, import sorting, pyupgrade rewrites, flake8-bugbear, pycodestyle warnings. |
| | `ignore = ["B008"]` | Function call in a default argument — `Depends()` in a FastAPI signature is the documented idiom. |
| `[tool.ruff.lint.per-file-ignores]` | `"alembic/versions/*" = ["E501"]` | Autogenerated migrations carry long literal lines that are not worth rewrapping. |

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `project.name` | Metadata | `name = "recluse-ids"` | Distribution name. |
| `project.version` | Metadata | `version = "0.1.0"` | Version, matching `frontend/package.json`. |
| `project.description` | Metadata | `"Recluse — two-stage ML network intrusion detection with SOC triage dashboard"` | One-line summary. |
| `project.requires-python` | Metadata | `">=3.11,<3.13"` | Upper-bounded because the ML stack lacks settled 3.13+ wheels. |
| `project.dependencies` | List | 13 runtime packages | See the dependency table above. |
| `build-system` | Table | `requires = ["hatchling"]`, `build-backend = "hatchling.build"` | Build backend. |
| `dependency-groups.dev` | List | `pytest`, `httpx`, `ruff` | Installed by `uv sync`, excluded by `uv sync --no-dev`. |

#### Notes

- Constraints are lower bounds only; exact versions are pinned by `backend/uv.lock`, which the Docker build installs with `--locked`. `.gitattributes` marks that lockfile `linguist-generated=true -diff`.
- `requires-python` allows 3.11 as a floor but `.python-version` pins 3.12 and ruff targets `py312`, so 3.12 is what is actually used and tested.
- Shipping `training` in the wheel is a deliberate consequence of the shared-feature-module constraint; see [ML Models](ML-Models) and [Anti-Patterns](Anti-Patterns).
- Status: implemented. The ML dependencies are installed and importable; the training code that uses them is largely stubbed until Phases 1–4 — see [Code Reference — Training](Code-Backend-Training).

### backend/.python-version

**Path:** `backend/.python-version` — pins the interpreter uv provisions for the backend.

#### What it does

A single line, `3.12`. uv reads it when creating or syncing `backend/.venv`, so every contributor and the Docker build get the same interpreter without anyone choosing one. It is copied into the image in the dependency layer of `backend/Dockerfile`, alongside `pyproject.toml` and `uv.lock`, which is why a change to it invalidates the dependency cache — which is correct, because a different interpreter needs different wheels.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| interpreter pin | File contents | `3.12` | The Python version uv installs and uses for `backend/.venv`. |

#### Notes

- Consistent with `requires-python = ">=3.11,<3.13"` and `target-version = "py312"` in `pyproject.toml`, and with the `python3.12` base image tag.
- Status: implemented.

### backend/uv.lock and frontend/package-lock.json

**Path:** `backend/uv.lock`, `frontend/package-lock.json` — the two lockfiles that turn the version *ranges* in `pyproject.toml` and `package.json` into one exact, reproducible dependency set.

#### What it does

Both are generated, both are committed, and both are load-bearing at build time. `pyproject.toml` and `package.json` declare lower bounds; the lockfiles are what make two machines — and the Docker build — resolve to the same versions. Committing them is what lets the container build be reproducible without pinning exact versions in the human-edited manifests.

Both are marked `linguist-generated=true -diff` in `.gitattributes`, so they collapse in review and do not skew the repository's language statistics. That suppresses the textual diff; it does not stop them being committed, and they must be.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `backend/uv.lock` | Generated lockfile | resolved from `backend/pyproject.toml` | Regenerate with `uv sync` (or `uv lock`) in `backend/`. `backend/Dockerfile` installs from it twice with `uv sync --locked`. |
| `frontend/package-lock.json` | Generated lockfile | resolved from `frontend/package.json` | Regenerate with `npm install` in `frontend/`. `frontend/Dockerfile`'s `deps` stage installs from it with `npm ci --no-fund --no-audit`. |

#### Notes

- `--locked` and `npm ci` both **fail** rather than re-resolve when the lockfile and its manifest disagree. That is the point: a build that silently upgraded a transitive dependency would not be reproducible. If the Docker build fails with a lockfile complaint, the fix is to run the regeneration command locally and commit the result, not to drop the flag.
- `make install` deliberately runs `npm install --no-fund` rather than `npm ci`, because a developer adding a dependency needs the lockfile updated; `npm ci` is reserved for the image build, where honouring the lockfile exactly is what is wanted.
- Never hand-edit either file. Both are machine-resolved graphs, and an edited entry will be overwritten or rejected on the next resolve.
- Status: implemented.

### .env.example

**Path:** `.env.example` — the committed template for the single repo-root `.env` that both the backend and the frontend read.

#### What it does

One env file serves the whole stack. The backend reads it through pydantic-settings with the `IDS_` prefix (`app/config.py` sets `env_file = REPO_ROOT / ".env"`), and Vite reads the same file because `vite.config.ts` sets `envDir` to the repo root — two files would drift. `.env` itself is gitignored; this template is not, and `make env`, `./make.ps1 env` and `scripts/dev.py` all create `.env` from it when absent.

The file is also where the false-positive budget is stated. `tau_sup` is not a default of 0.5; it is derived from analyst capacity, and the three inputs to that arithmetic are configuration, not constants buried in a training script.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IDS_ENV` | `development` | Deployment environment: `development`, `staging` or `production`. |
| `IDS_LOG_LEVEL` | `INFO` | Log level. `config.py` accepts `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`; the template's inline comment lists only the four common ones. |
| `IDS_HOST` | `127.0.0.1` | Interface the API binds. Containers override to `0.0.0.0`. |
| `IDS_PORT` | `8000` | API port. |
| `IDS_API_V1_PREFIX` | `/api/v1` | Prefix every v1 route is mounted under. |
| `IDS_DATABASE_URL` | `sqlite+pysqlite:///data/ids.db` | SQLAlchemy URL. Switching to Postgres is this line only. |
| `IDS_DB_ECHO` | `false` | Echo SQL statements to the log. |
| `IDS_DATA_DIR` | `data` | Dataset root. Relative values resolve against the repo root. |
| `IDS_ARTIFACTS_DIR` | `backend/artifacts` | Where trained model bundles are read from and written to. |
| `IDS_REPORTS_DIR` | `reports` | Where evaluation reports are written. |
| `IDS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API. |
| `IDS_EXPECTED_DAILY_FLOW_VOLUME` | `1000000` | Flows per day, the denominator of the target false-positive rate. |
| `IDS_ANALYST_CAPACITY_PER_HOUR` | `40` | Alerts one analyst can triage per hour. |
| `IDS_ANALYST_SHIFT_HOURS` | `8` | Shift length; with capacity it gives `max_alerts_per_day`. |
| `IDS_DEDUPE_WINDOW_SECONDS` | `300` | Dedupe key window — `(src_host, alert_class, floor(ts, 5min))`. |
| `IDS_ALLOW_AUTO_BLOCK` | `false` | Present so the no-auto-block constraint is greppable. Setting it true is rejected at startup; there is no value that enables blocking. |
| `BACKEND_PORT` | `8000` | Host port compose publishes the backend on. |
| `FRONTEND_PORT` | `5173` | Host port compose publishes the dashboard on. |
| `VITE_API_BASE_URL` | `/api/v1` | Base URL the browser client calls; relative, so requests stay same-origin. |
| `VITE_DEV_SERVER_HOST` | `::` | Dev server bind address; dual-stack locally, `0.0.0.0` in containers. |
| `VITE_DEV_SERVER_PORT` | `5173` | Dev server port. |
| `VITE_DEV_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the Vite dev proxy forwards `/api`. Compose overrides it to `http://backend:8000`. |
| `VITE_HEALTH_POLL_MS` | `5000` | Dashboard health poll interval in milliseconds. |

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `IDS_*` | Variable group | 16 variables | Read by `app/config.py` through pydantic-settings with `env_prefix="IDS_"`. |
| `BACKEND_PORT` / `FRONTEND_PORT` | Variable group | 2 variables | Read by `docker-compose.yml` for host port mapping only. |
| `VITE_*` | Variable group | 5 variables | Read by Vite; only `VITE_`-prefixed names reach the browser bundle. |

#### Notes

- One backend setting is deliberately not templated. `Settings.app_name` (default `"Recluse"`) has no `IDS_APP_NAME` line here, because it is the product name rather than an environment knob; it is still settable through the environment like any other field. Every other field on `Settings` has a matching line in this file.
- The four `POSTGRES_*` variables that `docker-compose.yml` interpolates are also absent, and for a different reason: they belong to the opt-in `postgres` profile, which is a portability demonstration rather than a supported deployment. See the compose notes above.
- Only `VITE_`-prefixed variables are exposed to the browser. Never put a secret behind a `VITE_` name.
- `IDS_CORS_ORIGINS` is a comma-separated string, not JSON, because pydantic-settings would otherwise demand a JSON array in the env file for a list-typed field; `Settings.cors_origin_list` splits it.
- Inline `#` comments are used throughout, and `scripts/dev.py`'s parser strips them — but only the three port/host variables are read that way. The backend parses the file with pydantic-settings.
- Relative paths (`data`, `backend/artifacts`, `reports`) are anchored to the repo root by `_resolve()` in `app/config.py`, so they mean the same thing regardless of the working directory the process started in.
- Full semantics, validation rules and derived values are documented in [Configuration](Configuration).
- Status: implemented. The false-positive budget variables are read and surfaced today; the Phase 2 threshold selection that consumes them is not yet implemented.

---

## Repository hygiene

### .gitignore

**Path:** `.gitignore` — keeps large, reproducible and secret files out of version control.

#### What it does

The organising rule is that the repository holds source, not output. Anything a training run, a build, a migration or a package manager can regenerate is excluded, and the exclusions are grouped with the reason stated in the section header.

**Data.** `data/raw/*`, `data/interim/*` and `data/processed/*` are ignored, with negations for their `.gitkeep` files, and `*.csv`, `*.parquet`, `*.pcap` and `*.pcapng` are ignored everywhere. CICIDS2017 is gigabytes of redistributable CSVs; committing it would make every clone enormous and would not make the data any more available than downloading it from the source does. Phase 1 adds the download and cleaning steps.

**Model artifacts.** `backend/artifacts/*` is ignored with negations for `.gitkeep` and `README.md`, and `*.pkl`, `*.pt` and `*.joblib` are ignored globally. A trained model is the output of a documented, reproducible training run, and a binary blob in git history cannot be reviewed, diffed or trusted. `backend/artifacts/README.md` stays tracked precisely so the *contract* for those files is under review even though the files are not.

**Databases.** `*.db`, `*.sqlite`, `*.sqlite3` and `*.db-journal` are ignored. The dev SQLite file is disposable state rebuilt by `alembic upgrade head`; committing it would put one developer's alert rows into everyone's checkout.

**Secrets.** `.env`, `.env.local` and `.env.*.local` are ignored, with `!.env.example` negated back in. The template is reviewable; the file with real values never leaves the machine.

**Everything else.** Python caches and virtualenvs (`__pycache__/`, `*.py[cod]`, `*.egg-info/`, `.venv/`, `venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.coverage`, `htmlcov/`), Node output (`node_modules/`, `dist/`, `.vite/`, `*.tsbuildinfo`), editor and OS files (`.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db`), logs (`*.log`, `logs/`), and `temp/` — the scratch working copy of the build prompt, whose tracked counterpart is `BUILD_PROMPT.md`.

**What this means for a fresh clone.** You get a tree with empty `data/raw`, `data/interim`, `data/processed`, `reports` and `backend/artifacts` directories, no `.env`, no database and no dependencies. That is why `make dev` does preflight work: it creates `.env` from the template, installs both dependency sets, and runs migrations to create `data/ids.db`. It cannot create the dataset or the model — those come from Phase 1's download step and a training run.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| Data section | Pattern group | `data/raw/*`, `data/interim/*`, `data/processed/*`, `!data/*/.gitkeep`, `*.csv`, `*.parquet`, `*.pcap`, `*.pcapng` | Datasets excluded; directory placeholders kept. |
| Artifacts section | Pattern group | `backend/artifacts/*`, `!backend/artifacts/.gitkeep`, `!backend/artifacts/README.md`, `*.pkl`, `*.pt`, `*.joblib` | Trained models excluded; the contract kept. |
| Python section | Pattern group | `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `.venv/`, `venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.coverage`, `htmlcov/` | Interpreter and tool output. |
| Databases section | Pattern group | `*.db`, `*.sqlite`, `*.sqlite3`, `*.db-journal` | Disposable local state. |
| Node section | Pattern group | `node_modules/`, `dist/`, `.vite/`, `*.tsbuildinfo` | Installed packages and build output. |
| Env section | Pattern group | `.env`, `.env.local`, `.env.*.local`, `!.env.example` | Secrets out, template in. |
| Editors / OS section | Pattern group | `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db` | Machine-local noise. |
| Scratch section | Pattern | `temp/` | Working copy of the build prompt. |
| Logs section | Pattern group | `*.log`, `logs/` | Runtime logs. |

#### Notes

- The `dir/*` plus `!dir/.gitkeep` idiom is required: a bare `dir/` would exclude the directory itself and git would never look inside it, so the negation could not take effect.
- `*.csv` is ignored globally, not just under `data/`. If a small fixture CSV is ever needed for a test it must be force-added with `git add -f` or the pattern narrowed.
- Status: implemented.

### .gitattributes

**Path:** `.gitattributes` — normalises line endings, marks binary types, and keeps generated files out of diffs and language statistics.

#### What it does

The first rule is the important one. `* text=auto eol=lf` normalises everything to LF in the repository. Without it, files authored on Windows land with CRLF, and the Linux containers that consume them behave differently than they do on the host — the `sh -c` CMD in `backend/Dockerfile`, `frontend/nginx.conf` and the migration scripts are all read by tools that either reject or mis-parse CRLF.

The exception set is the Windows-only scripts: `*.ps1`, `*.cmd` and `*.bat` keep CRLF locally, because that is what Windows tooling expects. `make.ps1` is the file this exists for.

Binary types are declared explicitly (`*.png`, `*.jpg`, `*.ico`, `*.pkl`, `*.pt`, `*.parquet`, `*.pcap`) so git never attempts line-ending conversion or a textual merge on them — most of these are also gitignored, but the attribute protects the case where one is force-added.

The last group marks files that are produced rather than written. `frontend/package-lock.json` and `backend/uv.lock` are `linguist-generated=true -diff`, so they collapse in review and do not skew the repository's language breakdown. `frontend/src/types/api.d.ts` is `linguist-generated=true` — it is regenerated by `make gen-types` from the backend's OpenAPI schema and must never be hand-edited; see [Code Reference — Frontend](Code-Frontend). `BUILD_PROMPT.md` is `linguist-documentation=true` so a long specification does not register the project as mostly Markdown.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| default text rule | Attribute | `* text=auto eol=lf` | Everything is stored and checked out with LF. |
| Windows scripts | Attributes | `*.ps1 text eol=crlf`, `*.cmd text eol=crlf`, `*.bat text eol=crlf` | Checked out with CRLF for native Windows tooling. |
| binary types | Attributes | `*.png binary`, `*.jpg binary`, `*.ico binary`, `*.pkl binary`, `*.pt binary`, `*.parquet binary`, `*.pcap binary` | No conversion, no textual merge. |
| lockfiles | Attributes | `frontend/package-lock.json linguist-generated=true -diff`, `backend/uv.lock linguist-generated=true -diff` | Collapsed in diffs, excluded from language stats. |
| generated types | Attribute | `frontend/src/types/api.d.ts linguist-generated=true` | Produced by `make gen-types`; never hand-edited. |
| specification | Attribute | `BUILD_PROMPT.md linguist-documentation=true` | Counted as documentation, not source. |

#### Notes

- `eol=crlf` on `*.ps1` is why `make.ps1` differs from every other text file in the tree; that is intended, not drift.
- `-diff` suppresses the textual diff for the lockfiles but does not prevent them being committed — they must be, since the Docker build installs with `--locked` and `npm ci`.
- Status: implemented.

---

## Documentation toolchain

The pages you are reading are files in this repository, under `wiki/`, and the copy GitHub serves is a mirror. Four files do that mirroring: a publisher, a hook installer, the hook itself, and the workflow that runs the publisher in CI. This section is the file-by-file reference for all four. For the task-level guide — how to publish, how to add a page, how to rename one — see [Wiki Publishing](Wiki-Publishing).

The constraint that shapes all four is structural: GitHub does not serve its wiki from the code repository. It serves it from a second git repository, `<repo>.wiki.git`, which a normal `git push` never touches. `wiki/` is the source of truth and everything under `<repo>.wiki.git` is disposable output.

### scripts/publish_wiki.py

**Path:** `scripts/publish_wiki.py` — clones the GitHub wiki repository, makes it byte-identical to `wiki/`, and pushes only when something actually differs.

#### What it does

A single-file script with no third-party dependencies; it shells out to `git` for everything. It resolves `wiki/` from its own location rather than from the working directory, so it behaves the same whether it is run from the repo root, from a hook, or from a CI runner.

It is idempotent by design, which is what allows the post-commit hook and the Actions workflow to both be wired to it without either having to know about the other. A second run immediately after a successful one finds no difference, logs `already up to date, nothing to push`, and returns `0` before touching git.

The sync is a true mirror rather than an overlay. `sync()` deletes files the checkout has and `wiki/` does not, which is what makes deleting a page work — and what makes an edit typed into the GitHub wiki web UI disappear on the next publish, silently and unrecoverably except from the wiki repository's own history.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `REPO_ROOT` | Constant | `REPO_ROOT = Path(__file__).resolve().parents[1]` | Repo root, derived from the script's location. |
| `SOURCE_DIR` | Constant | `SOURCE_DIR = REPO_ROOT / "wiki"` | Default page source; overridable with `--source`. |
| `PUBLISHED_SUFFIXES` | Constant | `(".md", ".png", ".jpg", ".jpeg", ".svg", ".gif")` | The only suffixes that are mirrored. Everything else in `wiki/` stays local. |
| `RESET`, `COLOURS` | Constants | `COLOURS = {"wiki": ..., "git": ..., "warn": ..., "error": ...}` | ANSI prefixes for the four log sources. |
| `log` | Function | `log(source: str, message: str) -> None` | Prints `[source] message` with the source colour, `flush=True`. |
| `run` | Function | `run(args: list[str], cwd: Path \| None = None, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]` | Thin `subprocess.run` wrapper in text mode. Every git invocation goes through it. |
| `git_output` | Function | `git_output(args: list[str], cwd: Path \| None = None, default: str = "") -> str` | Runs `git <args>` and returns stripped stdout, or `default` on `CalledProcessError`/`FileNotFoundError`. |
| `wiki_remote_url` | Function | `wiki_remote_url(explicit: str \| None = None) -> str` | Precedence: `--remote`, then `WIKI_REMOTE_URL`, then `origin` with `.git` → `.wiki.git`. A URL already ending `.wiki.git` is used as is; one with no suffix gets `.wiki.git` appended. Raises `SystemExit` when there is no `origin` and no `--remote`. |
| `authenticated_url` | Function | `authenticated_url(url: str) -> str` | Rewrites `https://` to `https://x-access-token:<token>@` when a token is in the environment. Returns the URL untouched for SSH remotes, for no token, and for a URL that already carries credentials. |
| `redact` | Function | `redact(url: str) -> str` | `re.sub(r"//[^/@]+@", "//***@", url)` — every URL the script prints goes through this. |
| `display_path` | Function | `display_path(path: Path) -> str` | Renders a path relative to `REPO_ROOT` when it is inside it, falling back to the absolute form. `--source` may legitimately point elsewhere, so the relative form is readability, not an assumption. |
| `published_files` | Function | `published_files(root: Path) -> dict[str, Path]` | Walks `root.rglob("*")`, keeps files whose lower-cased suffix is in `PUBLISHED_SUFFIXES`, and skips anything with a dot-prefixed path part. Returns publish-relative POSIX path → source path. |
| `existing_files` | Function | `existing_files(root: Path) -> dict[str, Path]` | Everything currently in the wiki checkout, ignoring its `.git` directory. |
| `sync` | Function | `sync(source: Path, target: Path) -> tuple[list[str], list[str], list[str]]` | Makes `target` contain exactly `source`. Returns `(added, updated, removed)`. Byte comparison, parent directories created, extras unlinked. |
| `default_message` | Function | `default_message() -> str` | `Update wiki from <short sha> — <subject of HEAD>`, degrading to `Update wiki from <sha>` and then to `Update wiki`. |
| `committer_identity` | Function | `committer_identity() -> tuple[str, str]` | `WIKI_GIT_NAME`/`WIKI_GIT_EMAIL`, then this repository's `user.name`/`user.email`, then `recluse-docs` / `recluse-docs@users.noreply.github.com`. |
| `report` | Function | `report(added: list[str], updated: list[str], removed: list[str]) -> None` | Prints the change list as `+`, `~` and `-` lines. |
| `parse_args` | Function | `parse_args(argv: list[str] \| None = None) -> argparse.Namespace` | Defines the five flags below. |
| `main` | Function | `main(argv: list[str] \| None = None) -> int` | Validate, resolve, clone, sync, commit, push. Returns the process exit code. |

#### CLI flags

| Flag | Effect |
| --- | --- |
| `-m`, `--message <text>` | Commit message for the wiki commit, instead of `default_message()`. |
| `--remote <url>` | Publish to this wiki repository instead of the one derived from `origin`. |
| `--source <dir>` | Read pages from this directory. Defaults to `wiki/`. |
| `--dry-run` | Clone, compare, print the change list, stop. Nothing is committed or pushed. |
| `--check` | Implies `--dry-run`, and exits `1` with `wiki is out of date (--check)` when the mirror is behind. For gating. |

#### Environment variables

| Variable | Effect |
| --- | --- |
| `WIKI_REMOTE_URL` | The wiki repository URL, used when `--remote` is absent. |
| `GITHUB_TOKEN` | Injected into an `https://` clone URL as `x-access-token`. |
| `GH_TOKEN` | Checked only when `GITHUB_TOKEN` is unset; same effect. |
| `WIKI_GIT_NAME` | Author name for the wiki commit. |
| `WIKI_GIT_EMAIL` | Author email for the wiki commit. |

#### Exit codes

| Code | When |
| --- | --- |
| `0` | A successful publish; `already up to date, nothing to push`; any `--dry-run` that is not `--check`; and `git found nothing to commit after normalisation`. |
| `1` | The source directory does not exist; it holds no publishable file; `git` is not on `PATH`; the clone failed; `--check` found the mirror behind; the push failed. |
| `SystemExit` | Raised with a message from `wiki_remote_url()` when there is no `origin` remote and no `--remote`. |

#### Notes

- **The invariant it protects.** `wiki/` is the source of truth; the GitHub wiki is a mirror. `sync()` removes files the checkout has and `wiki/` does not, so a page deleted from the repository disappears from the wiki, and a page edited in the web UI is overwritten without warning.
- **The line-ending flags are the least obvious thing in the file, and the most important on Windows.** The clone runs as `git -c core.autocrlf=false -c core.eol=lf clone --depth 1 --quiet ...`, and the same two values are set again with `git config` inside the checkout before committing. The wiki repository carries no `.gitattributes` of its own, so `core.autocrlf` would otherwise check every page out with CRLF while the source in `wiki/` is LF. Every file would compare as changed on every run, and `--check` would never go green.
- The clone is shallow (`--depth 1`) into a `tempfile.TemporaryDirectory(prefix="recluse-wiki-")`, so nothing is left on disk. The wiki's history is never needed, only its current tree.
- A clone failure whose stderr contains `not found` or `does not exist` is treated specially: the script prints the one-time web-UI setup instructions rather than a raw git error, because that is the failure every new repository hits. Any other clone failure is reported verbatim.
- No token is ever written to disk. `authenticated_url()` builds the credentialed URL only as a subprocess argument, and every URL that reaches a log line passes through `redact()` first.
- **Wiring.** Invoked by `make wiki` and `make wiki-check`, by `scripts/hooks/post-commit`, and by `.github/workflows/publish-wiki.yml`. It imports nothing from the project — only `argparse`, `filecmp`, `os`, `re`, `shutil`, `subprocess`, `sys`, `tempfile` and `pathlib` — which is why the Makefile can call it with a bare `python` before `uv sync` has run.
- Status: implemented. No test covers it; `sync()` and `wiki_remote_url()` are pure enough to test directly, and that gap is named in [Testing](Testing).

### scripts/install_hooks.py

**Path:** `scripts/install_hooks.py` — copies the hooks in `scripts/hooks/` into the repository's git hooks directory, and removes them again.

#### What it does

Git does not version-control `.git/hooks`, so a hook committed to the repository would never install itself. The real hooks live in `scripts/hooks/` — a directory that *is* version-controlled and reviewable — and this script copies them into place on demand. It copies rather than symlinks, because symlink creation on Windows needs either developer mode or elevation.

The single rule it enforces is that it never clobbers a hook it did not write. Every copy it installs carries a marker line, and both `uninstall()` and the overwrite check in `install()` look for it.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `REPO_ROOT` | Constant | `REPO_ROOT = Path(__file__).resolve().parents[1]` | Repo root, from the script's location. |
| `HOOK_SOURCE` | Constant | `HOOK_SOURCE = REPO_ROOT / "scripts" / "hooks"` | The version-controlled hook directory. Currently holds one file, `post-commit`. |
| `MARKER` | Constant | `MARKER = "# Installed by scripts/install_hooks.py — do not edit here; edit scripts/hooks/."` | Written into every installed copy; the basis of every ownership decision. |
| `RESET`, `COLOURS` | Constants | `COLOURS = {"hooks": ..., "warn": ..., "error": ...}` | ANSI prefixes for the three log sources. |
| `log` | Function | `log(source: str, message: str) -> None` | Prints `[source] message` with the source colour. |
| `hooks_dir` | Function | `hooks_dir() -> Path` | Resolves the destination by asking git: `core.hooksPath` if configured (absolute, or relative to `REPO_ROOT`), otherwise `git rev-parse --git-common-dir` plus `hooks`. Raises `SystemExit("Not a git repository, or git is not on PATH")` when that fails. |
| `available_hooks` | Function | `available_hooks() -> list[Path]` | Sorted files in `scripts/hooks/`, skipping dot-prefixed names. Returns `[]` when the directory is absent. |
| `make_executable` | Function | `make_executable(path: Path) -> None` | ORs `S_IXUSR \| S_IXGRP \| S_IXOTH` onto the existing mode. |
| `install` | Function | `install(target_dir: Path, force: bool) -> int` | Copies each source hook, inserting `MARKER` as the second line after the shebang, writing with `newline="\n"`, then marking it executable. Always returns `0`. |
| `uninstall` | Function | `uninstall(target_dir: Path) -> int` | Deletes each installed hook that carries `MARKER`; reports and leaves alone any that does not. Always returns `0`. |
| `show` | Function | `show(target_dir: Path) -> int` | Prints the hooks directory and, per hook, `installed`, `not installed` or `present, but not ours`. Always returns `0`. |
| `main` | Function | `main(argv: list[str] \| None = None) -> int` | Parses the flags, checks `git` is on `PATH`, resolves `hooks_dir()`, then dispatches to `show`, `uninstall` or `install`. |

#### CLI flags

| Flag | Effect |
| --- | --- |
| *(none)* | Install: copy every file in `scripts/hooks/` into the hooks directory and mark it executable. |
| `--list` | Print hook status and exit. Takes precedence over `--uninstall`. |
| `--uninstall` | Remove only hooks carrying `MARKER`. |
| `--force` | Overwrite a hook that is present but was not installed by this script. |

#### Exit codes

| Code | When |
| --- | --- |
| `0` | Every normal path, including skipping an unrelated hook — that is a warning, not a failure. |
| `1` | `git` is not on `PATH`. |
| `SystemExit` | Raised from `hooks_dir()` with `"Not a git repository, or git is not on PATH"` when `git rev-parse --git-common-dir` fails. |

#### Notes

- **The invariant it protects.** It never destroys a hook it did not write. `install()` skips a target that lacks `MARKER` and tells you to re-run with `--force`; `uninstall()` leaves such a file alone and says so. That is why the marker is inserted rather than left implicit: ownership has to be readable from the installed file itself.
- The marker is inserted as the *second* line, immediately after the shebang, so the copy is still a valid executable script. A source file with no shebang gets the marker prepended instead.
- `hooks_dir()` honours `core.hooksPath` first and otherwise uses `--git-common-dir` rather than `--git-dir`, which is what makes it correct inside a git worktree, where `.git` is a file pointing elsewhere.
- Installed copies are written with `newline="\n"` explicitly. A `post-commit` hook with CRLF line endings fails on some shells with an obscure error, and this is a Windows-first repository.
- Editing the installed copy has no lasting effect, which is exactly what the marker line says. Edit `scripts/hooks/post-commit` and re-run the installer.
- **Wiring.** `make hooks` and `make hooks-uninstall`. Like the publisher, it imports only the standard library (`argparse`, `shutil`, `stat`, `subprocess`, `sys`, `pathlib`).
- Status: implemented. No test covers it.

### scripts/hooks/post-commit

**Path:** `scripts/hooks/post-commit` — the version-controlled source of the git hook that publishes the wiki after a commit that touched `wiki/`.

#### What it does

A POSIX `sh` script under `set -u`, opt-in per developer. It exists so that a contributor writing several pages in a row sees them rendered immediately rather than waiting for a push to `main`. It is not installed by cloning; `make hooks` installs it.

Its behaviour, in order:

1. **Skip switch.** `exit 0` immediately when `RECLUSE_SKIP_WIKI_PUBLISH=1`.
2. **Locate the repository.** `cd` to `git rev-parse --show-toplevel`, exiting `0` if either the rev-parse or the `cd` fails.
3. **Did this commit touch `wiki/`?** `git diff-tree --no-commit-id --name-only -r --root HEAD | grep -q '^wiki/'`. The `--root` flag is what makes the check work on a repository's very first commit, which has no parent to diff against. A commit that changed nothing under `wiki/` exits `0`.
4. **The branch gate.** `RECLUSE_WIKI_BRANCHES` is a space-separated allow-list defaulting to `main`. On any other branch the hook prints `[wiki] wiki/ changed on '<branch>'; publishing only from: <list>`, plus a hint to run `python scripts/publish_wiki.py` by hand, and exits `0`. The gate exists because the GitHub wiki has no branches of its own — anything pushed to it is immediately live — so publishing from a feature branch would put unreviewed pages in front of readers.
5. **Find an interpreter.** `command -v python3 || command -v python`, skipping with `[wiki] python not found on PATH; skipping wiki publish` if neither exists.
6. **Publish.** Runs `"$python" scripts/publish_wiki.py`. On failure it prints `[wiki] publish failed. The commit is fine; re-run:` followed by the manual command.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| shebang | Directive | `#!/bin/sh` | POSIX shell, not bash — the hook has to run under whatever `sh` a Windows git ships. |
| `set -u` | Directive | `set -u` | Unset variable is an error. Note that `set -e` is deliberately *not* used; the script handles its own failures. |
| `RECLUSE_SKIP_WIKI_PUBLISH` | Environment variable | `${RECLUSE_SKIP_WIKI_PUBLISH:-0}` | Set to `1` to skip the hook for one commit. |
| `repo_root` | Variable | `repo_root=$(git rev-parse --show-toplevel 2>/dev/null)` | The hook runs from an unspecified directory, so it relocates itself. |
| change detection | Pipeline | `git diff-tree --no-commit-id --name-only -r --root HEAD \| grep -q '^wiki/'` | The gate that makes the hook free on every non-documentation commit. |
| `branch` | Variable | `branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)` | Current branch name. |
| `RECLUSE_WIKI_BRANCHES` | Environment variable | `allowed=${RECLUSE_WIKI_BRANCHES:-main}` | Space-separated allow-list, iterated with a `for` loop against `branch`. |
| `python` | Variable | `python=$(command -v python3 \|\| command -v python)` | Interpreter resolution, `python3` preferred. |
| final `exit 0` | Directive | `exit 0` | The last line. Every path reaches it or exits `0` earlier. |

#### Notes

- **The exit code is always `0`.** A failed publish never rewrites, undoes or blocks the commit. `post-commit` runs after the commit object exists, and a hook that failed here would be reporting a documentation problem as a source-control one.
- **The branch gate is the piece that stops unreviewed pages going live**, and it is the reason the hook is safe to install on a machine where feature branches are normal. Override it per commit with `RECLUSE_WIKI_BRANCHES="main develop" git commit -m "..."`.
- **Wiring.** Copied into the git hooks directory by `scripts/install_hooks.py`. The installed copy carries the `MARKER` line as its second line and must not be edited in place. It invokes `scripts/publish_wiki.py` and nothing else.
- Status: implemented, opt-in per developer. Nothing installs it automatically, and a contributor who never runs `make hooks` is relying on the Actions workflow instead.

### .github/workflows/publish-wiki.yml

**Path:** `.github/workflows/publish-wiki.yml` — the repository's only GitHub Actions workflow. It mirrors `wiki/` into the GitHub wiki from a hosted runner.

#### What it does

This is the default publishing path, because it needs no local setup from any contributor and it publishes the state that was actually merged rather than whatever happened to be in one person's checkout. It is named **Publish wiki**.

It is worth stating plainly that this is the *only* workflow in the repository. There is no job that runs the backend suite, the frontend suite, ruff or `tsc`. Both test suites are run locally with `make test`. That is a gap rather than a decision — see [Testing](Testing) and [Roadmap](Roadmap).

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `name` | Top-level key | `name: Publish wiki` | The name shown in the Actions tab. |
| `on.push` | Trigger | `branches: [main]`, `paths: ["wiki/**", "scripts/publish_wiki.py", ".github/workflows/publish-wiki.yml"]` | Fires only on `main`, and only for those three paths. The last two are included so a change to the publishing machinery is exercised immediately. |
| `on.workflow_dispatch` | Trigger | one optional input, `message` (`required: false`, `default: ""`) | Manual re-publish from the Actions tab, with an optional commit message. |
| `permissions` | Top-level key | `contents: write` | The wiki repository sits inside the same repository's permission scope, so this is what lets the job push. |
| `concurrency` | Top-level key | `group: publish-wiki`, `cancel-in-progress: false` | The wiki has one branch and no merge story, so simultaneous pushes would race; and a run already pushing must be allowed to finish rather than be killed mid-push. |
| `jobs.publish` | Job | `name: Mirror wiki/ to the GitHub wiki`, `runs-on: ubuntu-latest` | The single job. |
| checkout step | Step | `actions/checkout@v4`, `fetch-depth: 1` | One commit is all `default_message()` reads. |
| python step | Step | `actions/setup-python@v5`, `python-version: "3.11"` | The publisher is standard-library-only, so the version only has to be recent. |
| publish step | Step | `run:` with an `env` block | `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`, `WIKI_REMOTE_URL: https://github.com/${{ github.repository }}.wiki.git`, `WIKI_GIT_NAME: ${{ github.actor }}`, `WIKI_GIT_EMAIL: ${{ github.actor }}@users.noreply.github.com`. Calls `python scripts/publish_wiki.py -m "..."` when the dispatch input is non-empty, and `python scripts/publish_wiki.py` otherwise. |

#### Notes

- **The token never reaches a command line.** `WIKI_REMOTE_URL` is a plain `https://` URL; `authenticated_url()` inside the script rewrites it to `https://x-access-token:<token>@github.com/...` only as an argument to the clone and push subprocesses.
- The wiki commit is attributed to whoever pushed, because `WIKI_GIT_NAME` and `WIKI_GIT_EMAIL` come from `github.actor` rather than a service identity.
- **The workflow cannot create the wiki repository.** GitHub creates `<repo>.wiki.git` lazily, on the first page saved through the web UI; until that has happened the job fails on the clone with the setup instructions the script prints. That one-time step is still a prerequisite. See [Wiki Publishing](Wiki-Publishing).
- `python-version: "3.11"` on the runner is intentionally looser than the backend's `3.12` pin. The publisher is not backend code and shares none of its dependencies.
- Status: implemented.

---

## Data and report directories

### data/raw/.gitkeep, data/interim/.gitkeep, data/processed/.gitkeep, reports/.gitkeep

**Path:** `data/raw/.gitkeep`, `data/interim/.gitkeep`, `data/processed/.gitkeep`, `reports/.gitkeep` — zero-byte placeholders that keep otherwise-ignored output directories present in a clone.

#### What it does

Git tracks files, not directories, so an empty directory cannot be committed. Each of these four directories is ignored by content (`data/raw/*`, and `reports` via the log and report patterns) but must exist on a fresh clone, because the pipeline and the API write into them and a missing directory is a crash rather than a helpful message. A zero-byte `.gitkeep`, negated back in by `.gitignore`, is the standard way to express that.

The three `data/` directories are the stages of the Phase 1 pipeline: `raw` holds the downloaded CICIDS2017 CSVs exactly as published, `interim` holds cleaned Parquet written by pyarrow, and `processed` holds the temporally split, feature-extracted matrices that training consumes. `reports/` is where Phase 4 writes `loao.md` and the other evaluation output. Keeping the three data stages separate means the raw download is never mutated in place and any stage can be rebuilt from the one before it. See [Data Pipeline](Data-Pipeline).

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `data/raw/.gitkeep` | Placeholder file | 0 bytes | Keeps the download target directory in the tree. |
| `data/interim/.gitkeep` | Placeholder file | 0 bytes | Keeps the cleaned-Parquet directory in the tree. |
| `data/processed/.gitkeep` | Placeholder file | 0 bytes | Keeps the split/feature-matrix directory in the tree. |
| `reports/.gitkeep` | Placeholder file | 0 bytes | Keeps the evaluation report directory in the tree. |

#### Notes

- `Settings.ensure_directories()` in `app/config.py` also creates `data_path`, `artifacts_path` and `reports_path` idempotently at startup, so the placeholders are belt and braces rather than the only defence.
- `docker-compose.yml` bind-mounts `./data` and `./reports` into the backend container, so these host directories must exist before `make up`.
- `data/` itself is not listed in `.dockerignore`, only its three subdirectories, so the structure survives into the build context.
- Status: implemented. The directories are empty on a fresh clone and stay empty until Phase 1 populates `data/` and Phase 4 populates `reports/`.

### backend/artifacts/.gitkeep and backend/artifacts/README.md

**Path:** `backend/artifacts/.gitkeep`, `backend/artifacts/README.md` — placeholder and written contract for the model artifact directory.

#### What it does

`.gitkeep` is a zero-byte file keeping the directory in the tree while `.gitignore` excludes its contents. `README.md` is the interesting file: it is the tracked, reviewable specification of what the untracked binaries must contain, and it is negated back in by `.gitignore` alongside `.gitkeep` for exactly that reason.

The README states that everything in the directory is produced by `backend/training/` on the local machine, is gitignored, and is reproducible output rather than source. It then lists which file each training script writes and in which phase:

| File | Written by | Phase |
| --- | --- | --- |
| `preprocessing.pkl` | `split.py` / `features.py` | 1 |
| `supervised_model.pkl` | `train_supervised.py` | 2 |
| `autoencoder.pt` | `train_autoencoder.py` | 3 |
| `model_card.json` | `evaluate.py` | 2/3 |

The core of the document is the bundle contract. `preprocessing.pkl` must contain, together in one bundle:

```python
{
    "scaler": fitted_scaler,  # RobustScaler, fit on train only
    "feature_order": [...],  # exact column order
    "dropped_columns": [...],
    "port_encoding": {...},
    "schema_hash": "sha256:...",  # checked at startup
}
```

The stated reason for keeping them together is that none of the scaler, the column order or the hash is alone enough to reproduce the training-time feature matrix. `app/inference.py` recomputes the hash from `feature_order` at startup and refuses to serve on a mismatch, because train/serve skew produces garbage scores without raising anything — the check has to be the thing that makes noise. The README closes by recording that nothing here is ever loaded from an untrusted source: the API has no artifact upload path, and torch weights are read with `weights_only=True`.

#### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `backend/artifacts/.gitkeep` | Placeholder file | 0 bytes | Keeps the artifact directory in a fresh clone. |
| `backend/artifacts/README.md` | Documentation | 32 lines | Tracked contract for the untracked artifact bundle. |
| artifact table | Section | `\| File \| Written by \| Phase \|` | Maps each artifact to its producing script and phase. |
| `preprocessing.pkl` contract | Section | five required keys: `scaler`, `feature_order`, `dropped_columns`, `port_encoding`, `schema_hash` | What the preprocessing bundle must contain for the startup check to pass. |
| provenance note | Section | `weights_only=True`, no upload path | Records that artifacts are never loaded from an untrusted source. |

#### Notes

- Tracking the README while ignoring the artifacts is the point: the contract is reviewable in a pull request even though the binaries never are.
- The `schema_hash` check is the enforcement mechanism for the shared-feature-module constraint described in [Anti-Patterns](Anti-Patterns) and [ML Models](ML-Models).
- `IDS_ARTIFACTS_DIR` points here by default (`backend/artifacts`) and the container mounts the host directory at `/srv/backend/artifacts`, so a training run on the host is immediately visible to the container.
- Status: implemented as documentation. None of the four artifacts exists yet — Phase 0 ships no trained model, and `/api/v1/health` reports `model_version: "unloaded"`. The producing scripts are stubs; see [Code Reference — Training](Code-Backend-Training) and [Roadmap](Roadmap).

---

See also: [Getting Started](Getting-Started), [Configuration](Configuration), [Repository Layout](Repository-Layout), [Architecture](Architecture), [Testing](Testing), [Wiki Publishing](Wiki-Publishing), [Home](Home).
