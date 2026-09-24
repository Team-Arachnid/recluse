# Repository Layout

Every tracked file and directory in the repository, what each one is for, and
where new work belongs. Written for anyone opening the repository for the first
time, and as the index that the per-file reference pages hang off.

**Status of this page.** The tree below is the repository as it stands with
Phase 0 of 9 complete. Directories that exist only as a `.gitkeep` placeholder
are marked, with the phase that fills them. Nothing in the tree is aspirational:
if a file is listed, it exists.

---

## The tree

```
recluse/
├── BUILD_PROMPT.md              complete specification, all 15 parts, all 9 phases
├── README.md                    project voice: claim, status, constraints, dataset
├── Makefile                     task runner (GNU make)
├── make.ps1                     PowerShell mirror of every Makefile target
├── docker-compose.yml           backend + frontend, plus an opt-in postgres profile
├── .env.example                 every port, path, threshold input — copy to .env
├── .gitignore                   data, artifacts, .env, node_modules, .venv, dbs
├── .dockerignore                keeps data, venvs and caches out of build contexts
├── .gitattributes               line-ending and diff settings
│
├── .github/
│   └── workflows/
│       └── jekyll-gh-pages.yml  builds docs/ and deploys it to GitHub Pages
│
├── docs/                        the documentation site — content and theme
│   ├── index.md                 landing page (layout: home)
│   ├── Project-Overview.md      what the project claims and why
│   ├── Architecture.md          pipeline, fusion rule, request lifecycle, topology
│   ├── Repository-Layout.md     this page
│   ├── Getting-Started.md       install, run, verify
│   ├── Configuration.md         every IDS_* and VITE_* variable
│   ├── Data-Pipeline.md         CICIDS2017 cleaning, splits, feature contract
│   ├── ML-Models.md             Stage 1 and Stage 2, thresholds, artifacts
│   ├── API-Reference.md         the v1 surface, request and response shapes
│   ├── Database-Schema.md       tables, columns, constraints, indexes
│   ├── Frontend-Screens.md      the seven screens and what each answers
│   ├── Roadmap.md               phases 0–9 and their acceptance criteria
│   ├── Anti-Patterns.md         the failure modes this build refuses
│   ├── Testing.md               what each test asserts and why it exists
│   ├── Glossary.md              terms, from PR-AUC to UNCLASSIFIED_ANOMALY
│   ├── FAQ.md                   questions the repository keeps being asked
│   ├── Docs-Publishing.md       how these pages reach the published site
│   ├── Code-Backend-Core.md     app/ — config, db, models, schemas, inference, main
│   ├── Code-Backend-Pipeline.md app/ — explain, mitre, remediation, dedupe, drift,
│   │                            replay, live_capture; training/features.py
│   ├── Code-Backend-Routes.md   app/routes/ — every endpoint module
│   ├── Code-Backend-Training.md training/ — clean, split, train, evaluate, loao
│   ├── Code-Backend-Migrations.md  alembic/ — env.py and each revision
│   ├── Code-Backend-Tests.md    backend/tests/ — what each test pins
│   ├── Code-Frontend.md         frontend/src/ — every module
│   ├── Code-Infrastructure.md   Dockerfiles, compose, nginx, Makefile, scripts
│   ├── _config.yml              site metadata, plugins, permalinks
│   ├── _data/nav.yml            sidebar groups and page order
│   ├── _layouts/                default, page and home shells
│   ├── _includes/               head and sidebar fragments
│   ├── assets/css/style.css     the whole stylesheet
│   ├── assets/js/search.js      client-side search and the mobile drawer
│   ├── search.json              search index, generated at build time
│   └── Gemfile                  local preview only
│
├── scripts/
│   └── dev.py                   runs uvicorn + vite together with prefixed output
│
├── data/                        gitignored contents; only .gitkeep is tracked
│   ├── raw/.gitkeep             downloaded CICIDS2017 CSVs land here (Phase 1)
│   ├── interim/.gitkeep         cleaned Parquet from clean.py (Phase 1)
│   └── processed/.gitkeep       temporal train/val/test splits (Phase 1)
│
├── reports/
│   └── .gitkeep                 loao.md, curves and the classification report (Phase 4)
│
├── backend/
│   ├── pyproject.toml           dependencies, ruff and pytest config, packages
│   ├── uv.lock                  resolved dependency lock
│   ├── .python-version          pinned interpreter (3.11–3.12; 3.13+ lacks wheels)
│   ├── Dockerfile               uv + python3.12-bookworm-slim, migrate then serve
│   ├── alembic.ini              Alembic config; the URL comes from settings, not here
│   │
│   ├── alembic/
│   │   ├── env.py               takes the database URL from pydantic-settings
│   │   ├── script.py.mako       revision template
│   │   └── versions/
│   │       └── 9a6857dcba76_initial_schema.py   alerts, analyst_verdicts, model_versions
│   │
│   ├── artifacts/               model artifacts; gitignored except these two files
│   │   ├── .gitkeep             keeps the directory in the tree
│   │   └── README.md            what each artifact file is and who writes it
│   │
│   ├── app/                     the serving layer
│   │   ├── __init__.py          package docstring and __version__ = "0.1.0"
│   │   ├── main.py              create_app(), lifespan, GET /health
│   │   ├── config.py            pydantic-settings; every path, port and budget input
│   │   ├── db.py                engine, SessionLocal, Base, naming convention, pragmas
│   │   ├── models.py            SQLAlchemy models — portable column types only
│   │   ├── schemas.py           Pydantic wire contracts; source of the OpenAPI schema
│   │   ├── inference.py         ModelBundle, load_bundle, the schema-hash check
│   │   ├── explain.py           Phase 5 — TreeSHAP / reconstruction-error explanations
│   │   ├── mitre.py             Phase 5 — family → ATT&CK technique lookup
│   │   ├── remediation.py       Phase 5 — family → static response playbook
│   │   ├── dedupe.py            Phase 5 — (src_host, class, 5-min bucket) collapsing
│   │   ├── replay.py            Phase 5 — asyncio replay of held-out rows at 1/10/100x
│   │   ├── drift.py             Phase 7 — PSI per feature against the training reference
│   │   ├── live_capture.py      Phase 9 — live traffic ingestion, same feature module
│   │   └── routes/
│   │       ├── __init__.py      not_implemented() helper, api_router assembly
│   │       ├── alerts.py        queue, detail, verdict, related        (Phase 5, 501)
│   │       ├── score.py         POST /score, batch only                (Phase 5, 501)
│   │       ├── metrics.py       model metrics, threshold what-if, drift, registry
│   │       ├── analytics.py     summary and MITRE coverage             (Phase 5, 501)
│   │       ├── replay.py        replay start/stop, ingest start        (Phase 5/9, 501)
│   │       └── stream.py        GET /stream, server-sent events        (Phase 5, 501)
│   │
│   ├── training/                offline batch; never imported by a request handler
│   │   ├── __init__.py          package docstring stating that rule
│   │   ├── features.py          the feature contract shared with serving
│   │   ├── clean.py             Phase 1 — the six documented CICIDS2017 defects
│   │   ├── split.py             Phase 1 — temporal splits by capture day
│   │   ├── train_supervised.py  Phase 2 — Model A: RandomForest, then LightGBM
│   │   ├── train_autoencoder.py Phase 3 — Model B: benign-only autoencoder
│   │   ├── evaluate.py          Phase 2/3 — PR-AUC, per-class recall, FPR, curves
│   │   └── loao.py              Phase 4 — leave-one-attack-out, the headline result
│   │
│   └── tests/
│       ├── conftest.py          shared fixtures: app client, isolated database
│       ├── test_health.py       the Phase 0 checkpoint contract
│       ├── test_config.py       settings behaviour, including the auto-block rejection
│       ├── test_api_surface.py  every v1 route exists, answers honestly, names no block
│       ├── test_features.py     the shared feature contract and the schema hash
│       └── test_schema_portability.py  every column type compiles on SQLite and Postgres
│
└── frontend/
    ├── package.json             scripts and dependencies
    ├── package-lock.json        resolved lockfile
    ├── tsconfig.json            app TypeScript config
    ├── tsconfig.node.json       config for vite.config.ts and scripts
    ├── vite.config.ts           dev server, proxy, aliases, vitest config
    ├── components.json          shadcn/ui component generator config
    ├── index.html               the SPA shell, mounts #root
    ├── nginx.conf               Phase 8 static hosting, /api proxy, SSE-safe timeouts
    ├── Dockerfile               deps → dev | build | serve targets
    ├── scripts/
    │   └── generate-types.mjs   writes src/types/api.d.ts from the live OpenAPI schema
    └── src/
        ├── main.tsx             React root, StrictMode
        ├── App.tsx              QueryClientProvider, ErrorBoundary, current screen
        ├── index.css            Tailwind entry and design tokens
        ├── vite-env.d.ts        Vite ambient types
        ├── api/
        │   ├── client.ts        fetch wrapper and the ApiError class
        │   ├── queryClient.ts   TanStack Query defaults; never retries a 501
        │   ├── queries.ts       query keys and the useHealth hook
        │   └── types.ts         readable aliases over the generated OpenAPI types
        ├── types/
        │   └── api.d.ts         GENERATED — do not hand-edit; make gen-types rewrites it
        ├── lib/
        │   ├── env.ts           typed import.meta.env access with defaults in one place
        │   └── utils.ts         the cn() class-name helper
        ├── components/
        │   ├── ErrorBoundary.tsx   catches render errors instead of blanking the page
        │   ├── HealthPanel.tsx     renders the live /health response
        │   └── ui/                 shadcn/ui primitives
        │       ├── badge.tsx
        │       ├── button.tsx
        │       ├── card.tsx
        │       └── skeleton.tsx
        ├── pages/
        │   ├── SystemHealth.tsx      the one screen Phase 0 ships
        │   └── SystemHealth.test.tsx its test, including the no-accuracy-tile assertion
        └── test/
            └── setup.ts          vitest + jest-dom setup
```

Directories that exist only as a placeholder today:

| Path | Tracked content | What fills it | Phase |
| --- | --- | --- | --- |
| `data/raw/` | `.gitkeep` | Downloaded CICIDS2017 day CSVs | 1 |
| `data/interim/` | `.gitkeep` | Cleaned Parquet written by `clean.py` | 1 |
| `data/processed/` | `.gitkeep` | Temporal train / validation / test splits | 1 |
| `reports/` | `.gitkeep` | `loao.md`, the classification report, PR and ROC curves | 2–4 |
| `backend/artifacts/` | `.gitkeep`, `README.md` | `preprocessing.pkl`, `supervised_model.pkl`, `autoencoder.pt`, `model_card.json` | 1–3 |

---

## Top-level directories

### Repository root

| Path | Contains | Documented in |
| --- | --- | --- |
| `BUILD_PROMPT.md` | The full specification: 15 parts covering all nine phases, their acceptance criteria and their non-negotiable constraints | [Roadmap](Roadmap.md) |
| `README.md` | The project's public face: the claim, current status, constraints, dataset, limitations | [Project Overview](Project-Overview.md) |
| `Makefile`, `make.ps1` | Task runner and its PowerShell mirror: `dev`, `test`, `lint`, `migrate`, `gen-types`, `up`, `docs` | [Code: Infrastructure](Code-Infrastructure.md) |
| `docker-compose.yml` | Backend and frontend services, host port mapping, bind mounts, the opt-in `postgres` profile | [Code: Infrastructure](Code-Infrastructure.md) |
| `.env.example` | Every configurable value with its default and a comment explaining it | [Configuration](Configuration.md) |
| `.gitignore`, `.dockerignore`, `.gitattributes` | What never enters git, and what never enters a build context | [Code: Infrastructure](Code-Infrastructure.md) |

### `.github/`

| Path | Contains | Documented in |
| --- | --- | --- |
| `.github/workflows/jekyll-gh-pages.yml` | The CI job that builds `docs/` with Jekyll and deploys it to GitHub Pages | [Docs Publishing](Docs-Publishing.md) |

### `docs/`

| Path | Contains | Documented in |
| --- | --- | --- |
| `docs/*.md` | These pages. Authored in this repository so a documentation change is reviewed in the same commit as the code change that caused it | [Docs Publishing](Docs-Publishing.md) |
| `docs/_config.yml`, `docs/_data/`, `docs/_layouts/`, `docs/_includes/`, `docs/assets/` | The site itself: configuration, navigation, layouts and the stylesheet | [Code: Infrastructure](Code-Infrastructure.md) |

`docs/` is the whole website. GitHub Actions builds it with Jekyll on every
push to `main` that touches it, and deploys the result to GitHub Pages. There
is no second copy: the repository is the only source, and `docs/_site/` is
build output that is never committed.

### `scripts/`

| Path | Contains | Documented in |
| --- | --- | --- |
| `scripts/dev.py` | The native dev supervisor behind `make dev`: dependency check, `.env` creation, migrations, then uvicorn and Vite as prefixed child processes | [Code: Infrastructure](Code-Infrastructure.md) |

### `backend/`

| Path | Contains | Documented in |
| --- | --- | --- |
| `backend/app/` | The serving layer: app factory, settings, database, ORM models, wire schemas, model loading | [Code: Backend Core](Code-Backend-Core.md) |
| `backend/app/` (pipeline modules) | `explain.py`, `mitre.py`, `remediation.py`, `dedupe.py`, `replay.py`, `drift.py`, `live_capture.py` — the alert pipeline and traffic sources | [Code: Backend Pipeline](Code-Backend-Pipeline.md) |
| `backend/app/routes/` | One module per endpoint group, the `not_implemented()` helper, and `api_router` | [Code: Backend Routes](Code-Backend-Routes.md), [API Reference](API-Reference.md) |
| `backend/training/` | The offline batch pipeline and `features.py`, the one module both training and serving import | [Code: Backend Training](Code-Backend-Training.md), [Data Pipeline](Data-Pipeline.md) |
| `backend/alembic/` | Migration environment and revisions; the URL comes from settings, never from `alembic.ini` | [Code: Backend Migrations](Code-Backend-Migrations.md), [Database Schema](Database-Schema.md) |
| `backend/artifacts/` | Trained model artifacts. Gitignored except `.gitkeep` and `README.md` | [ML Models](ML-Models.md) |
| `backend/tests/` | The assertions that keep the constraints from rotting | [Code: Backend Tests](Code-Backend-Tests.md), [Testing](Testing.md) |
| `backend/pyproject.toml`, `uv.lock`, `.python-version` | Dependencies, ruff and pytest configuration, the pinned interpreter | [Getting Started](Getting-Started.md) |
| `backend/Dockerfile` | The API image: dependencies first for layer caching, then migrate and serve | [Code: Infrastructure](Code-Infrastructure.md) |

### `frontend/`

| Path | Contains | Documented in |
| --- | --- | --- |
| `frontend/src/api/` | The typed fetch client, TanStack Query defaults and hooks, and readable aliases over generated types | [Code: Frontend](Code-Frontend.md) |
| `frontend/src/types/api.d.ts` | Generated from the backend's OpenAPI schema. Never hand-edited | [Code: Frontend](Code-Frontend.md) |
| `frontend/src/components/` | Shared components, including the shadcn/ui primitives under `ui/` | [Code: Frontend](Code-Frontend.md), [Frontend Screens](Frontend-Screens.md) |
| `frontend/src/pages/` | One file per screen, with its test beside it | [Frontend Screens](Frontend-Screens.md) |
| `frontend/src/lib/` | Environment access and small helpers; the only place a default port or path appears | [Configuration](Configuration.md) |
| `frontend/vite.config.ts`, `tsconfig*.json`, `components.json` | Build, dev-server, proxy, alias, test and type configuration | [Code: Frontend](Code-Frontend.md) |
| `frontend/Dockerfile`, `nginx.conf` | The `dev`, `build` and `serve` image targets, and SSE-safe static hosting | [Code: Infrastructure](Code-Infrastructure.md) |

### `data/` and `reports/`

| Path | Contains | Documented in |
| --- | --- | --- |
| `data/raw/` | Downloaded dataset CSVs. Gitignored | [Data Pipeline](Data-Pipeline.md) |
| `data/interim/` | Cleaned Parquet. Gitignored | [Data Pipeline](Data-Pipeline.md) |
| `data/processed/` | Temporal splits. Gitignored | [Data Pipeline](Data-Pipeline.md) |
| `data/ids.db` | The development SQLite database. Gitignored | [Database Schema](Database-Schema.md) |
| `reports/` | Evaluation output, including `loao.md` | [Roadmap](Roadmap.md) |

---

## Where things go

**A new API route.** A module under `backend/app/routes/`, exporting a `router`
built as `APIRouter(prefix=..., tags=[...])`, registered in
`app/routes/__init__.py` with `api_router.include_router(...)`. Never include the
`/api/v1` prefix in the route path: `app/main.py` mounts `api_router` with
`prefix=settings.api_v1_prefix`, so the prefix stays configurable. Request and
response models belong in `app/schemas.py`, not in the route module, because that
file is what the OpenAPI schema and therefore the frontend types are generated
from. If the endpoint is not implemented yet, return
`not_implemented("<METHOD /path>", "<Phase N (name)>")` and declare
`responses={501: {"model": NotImplementedResponse}}`. Then run `make gen-types`
so the frontend sees it, and add the route to
[API Reference](API-Reference.md).

**A new training script.** A module under `backend/training/`, importing
`features.py` for anything that touches columns. It must not import from
`backend/app/` — the dependency crosses one way only, and training has to stay
runnable without a web framework or a database. Anything it writes goes to
`settings.artifacts_path` or `settings.reports_path`, never next to the source
file.

**A new feature transform.** `backend/training/features.py`, and nowhere else. A
transform written a second time inside the API is the train/serve skew failure
mode, which is silent. If the transform changes the set or order of columns, the
`schema_hash` changes with it and every existing artifact bundle is correctly
rejected at startup until it is retrained.

**A new React page.** A component under `frontend/src/pages/`, with its test
beside it as `<Name>.test.tsx`. Data comes from a hook in
`frontend/src/api/queries.ts`, never from a `useEffect` fetch chain, and types
come from `frontend/src/api/types.ts` rather than from the generated file
directly. Shared presentation goes to `frontend/src/components/`; shadcn/ui
primitives go under `components/ui/`.

**A new database table or column.** `backend/app/models.py`, using the portable
types only — `PK` for keys, `String` plus a `CheckConstraint` instead of a native
enum, `DateTime(timezone=True)`, generic `JSON`. Then
`make revision m="add drift table"` to autogenerate the migration, read the
generated file before committing it, and `make migrate` to apply it.

**Model artifacts** land in `backend/artifacts/`, resolved from
`IDS_ARTIFACTS_DIR`. **Reports** land in `reports/`, resolved from
`IDS_REPORTS_DIR`. Both directories are created at startup by
`settings.ensure_directories()`, so a script never has to `mkdir` first.

**Raw data** lands in `data/raw/`, resolved from `IDS_DATA_DIR`. It is gitignored
for three reasons: CICIDS2017 is large, it is redistributable from its original
source so committing it duplicates someone else's hosting, and a repository
carrying hundreds of megabytes of CSV is unclonable for anyone who only wants the
code. `.gitignore` also blocks `*.csv`, `*.parquet`, `*.pcap` and `*.pcapng`
anywhere in the tree, so a capture file dropped in the wrong directory does not
get committed by accident.

**Documentation** goes in `docs/`, which is both the source and the published
site. The data-phase notes that once lived in `docs/phase-1-guide.txt` are
superseded by [Data Pipeline](Data-Pipeline.md) and
[Code: Training](Code-Backend-Training.md).

---

## What a fresh clone does and does not include

A clone gives you every source file, every configuration file, the lockfiles,
the migrations and the generated API types. It does not give you anything that a
command can regenerate.

| Not in a clone | Why | Command that produces it |
| --- | --- | --- |
| `data/raw/*` — the CICIDS2017 CSVs | Large, and redistributable from the original source | Phase 1 adds the download step; until then, fetch the day CSVs manually into `data/raw/` |
| `data/interim/*`, `data/processed/*` | Derived from `data/raw` | `clean.py` then `split.py` — Phase 1 |
| `backend/artifacts/*.pkl`, `*.pt`, `model_card.json` | Reproducible training output, not source | `train_supervised.py` (Phase 2), `train_autoencoder.py` (Phase 3), `evaluate.py` |
| `data/ids.db` and its `-wal` / `-shm` files | Local development database | `make migrate` / `./make.ps1 migrate`, which runs `alembic upgrade head`; `make dev` does it for you |
| `.env` | Holds local values and would leak them | `make env` / `./make.ps1 env`, which copies `.env.example`; `scripts/dev.py` does it on first run |
| `backend/.venv/` | Platform-specific binaries | `cd backend && uv sync`, or `make install` |
| `frontend/node_modules/` | Platform-specific binaries, and large | `cd frontend && npm install`, or `make install` |
| `frontend/dist/` | Build output | `make build` (`npm run typecheck && vite build`) |
| `reports/*` | Evaluation output | `evaluate.py` and `loao.py` — Phases 2–4 |
| `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `*.tsbuildinfo` | Caches | Recreated on the next run; `make clean` removes them |

One generated file **is** committed: `frontend/src/types/api.d.ts`. It is
produced by `make gen-types` from the running backend's OpenAPI schema, and it is
tracked so that a clone type-checks before the backend has ever been started, and
so that a schema change shows up as a reviewable diff rather than as a silent
regeneration on someone's machine.

The shortest path from clone to running stack:

```bash
git clone <repo-url>
cd recluse
cp .env.example .env
make dev          # or ./make.ps1 dev on Windows
```

That installs what is missing, applies migrations, and starts both processes.
Full detail, including prerequisites and how to verify the result, is in
[Getting Started](Getting-Started.md).
