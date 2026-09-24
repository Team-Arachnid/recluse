# Recluse

Machine-learning network intrusion detection with a SOC triage dashboard.

Recluse scores network flow records with two models working in sequence and
presents the results to a security analyst as a ranked, explained triage queue.
Stage 1 is a supervised classifier that names the attack families it was
trained on. Stage 2 is an autoencoder trained on benign traffic only, which
flags flows that do not look like normal whether or not anyone has ever
labelled them.

> **The one claim the project has to defend:** it detects attack traffic it was
> never trained on. Everything in this repository exists to make that claim
> measurable rather than asserted.

**The system alerts, ranks and explains. It never blocks traffic.**

---

## Current status

**Phase 0 of 9 is complete.** The stack runs end to end — a FastAPI service
with migrations and the full v1 route surface, and a React dashboard that
renders live health data fetched from it — but **there is no trained model
yet**, and no measured detection results exist.

`GET /api/v1/health` reports `model_version: "unloaded"` because that is the
truth. Endpoints later phases implement answer `501` naming the phase that
fills them in, so "not built yet" is distinguishable from "built and broken".
Pages in this wiki mark planned behaviour as planned and never print a number
that has not been measured.

| Phase | Scope                      | State       |
| ----- | -------------------------- | ----------- |
| 0     | Scaffolding                | **done**    |
| 1     | Data and features          | not started |
| 2     | Supervised classifier      | not started |
| 3     | Anomaly detector           | not started |
| 4     | Fusion and LOAO            | not started |
| 5     | Backend API                | not started |
| 6     | Frontend (seven screens)   | not started |
| 7     | Drift and active learning  | not started |
| 8     | Packaging                  | not started |
| 9     | Real traffic               | not started |

To run what exists: `make dev`, or `./make.ps1 dev` on Windows where GNU make
is not installed. That starts the API on <http://localhost:8000> and the
dashboard on <http://localhost:5173>. [Getting Started](Getting-Started)
covers prerequisites and troubleshooting.

Full detail, per phase, in [Roadmap](Roadmap). The full specification for
Phases 0 through 9, including the parts not yet started, is
[`BUILD_PROMPT.md`](https://github.com/Team-Arachnid/recluse/blob/main/BUILD_PROMPT.md)
at the repository root.

---

## Start here

| You are…                                   | Read, in this order                                                                                                   |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| **New to the project**                     | [Project Overview](Project-Overview) → [Architecture](Architecture) → [Getting Started](Getting-Started)      |
| **Setting up a machine**                   | [Getting Started](Getting-Started) → [Configuration](Configuration) → [Testing](Testing)                      |
| **About to work on the data phase**        | [Data Pipeline](Data-Pipeline) → [Code: Training](Code-Backend-Training) → [Anti-Patterns](Anti-Patterns)     |
| **About to train or evaluate a model**     | [Models and Evaluation](ML-Models) → [Anti-Patterns](Anti-Patterns)                                              |
| **Writing backend code**                   | [Architecture](Architecture) → [Code: Backend Core](Code-Backend-Core) → [API Reference](API-Reference)       |
| **Writing frontend code**                  | [Dashboard Screens](Frontend-Screens) → [Code: Frontend](Code-Frontend) → [API Reference](API-Reference)      |
| **Reviewing or evaluating the work**       | [Project Overview](Project-Overview) → [Models and Evaluation](ML-Models) → [Anti-Patterns](Anti-Patterns)    |
| **Looking for a term you do not recognise** | [Glossary](Glossary) → [FAQ](FAQ)                                                                               |

---

## All pages

### Understanding the project

| Page | What it covers |
| ---- | -------------- |
| [Project Overview](Project-Overview) | What Recluse is, why two models, the non-negotiable constraints, the alert-not-block argument, the stated limitations, and the legal and ethical scope that bounds live capture |
| [Architecture](Architecture) | The two-stage pipeline, the fusion rule, request lifecycle, module dependency map, deployment topology, design decisions |
| [Repository Layout](Repository-Layout) | Annotated tree of every tracked file, where new code goes, what a fresh clone does and does not include |
| [Roadmap](Roadmap) | Phases 0–9 with goals, acceptance criteria and artifacts, plus the full acceptance checklist |
| [Anti-Patterns](Anti-Patterns) | Failure modes that produce impressive numbers rather than crashes, and the mechanisms in this repo that prevent them |
| [Glossary](Glossary) | Every term a new contributor will hit, defined |
| [FAQ](FAQ) | The questions this project actually gets asked, answered specifically |

### Running and configuring it

| Page | What it covers |
| ---- | -------------- |
| [Getting Started](Getting-Started) | Prerequisites, native and containerised quick starts, verifying the install, troubleshooting |
| [Configuration](Configuration) | Every setting, its environment variable, default and effect, including the false-positive budget arithmetic |
| [Testing](Testing) | Running the suites, what is covered, what is not covered yet, linting and type checking |
| [Wiki Publishing](Wiki-Publishing) | How these pages reach the GitHub wiki — `scripts/publish_wiki.py`, the `.github/workflows/publish-wiki.yml` job that runs it on every push to `main` touching `wiki/**`, the optional `post-commit` hook installed by `make hooks`, and how to add or rename a page |

### The machine learning

| Page | What it covers |
| ---- | -------------- |
| [Data Pipeline](Data-Pipeline) | CICIDS2017, its known defects and how each is handled, leakage control, temporal splitting, scaling, the preprocessing bundle contract |
| [Models and Evaluation](ML-Models) | Both models, threshold selection from a false-positive budget, baselines, explanation strategy, the metrics that count, leave-one-attack-out |

### The system

| Page | What it covers |
| ---- | -------------- |
| [API Reference](API-Reference) | Every endpoint: today's behaviour, planned behaviour, schemas, status codes, curl examples |
| [Database Schema](Database-Schema) | Every table, column and index, why the types are Postgres-portable, the audit trail requirement |
| [Dashboard Screens](Frontend-Screens) | The seven planned screens, what each is for, the interactions that carry the argument, what is deliberately absent, and the one screen built today — the Phase 0 System Health page |

### Code reference, file by file

| Page | What it covers |
| ---- | -------------- |
| [Code: Backend Core](Code-Backend-Core) | `app/__init__.py`, `config.py`, `main.py`, `db.py`, `models.py`, `schemas.py`, `inference.py` |
| [Code: Alert Pipeline](Code-Backend-Pipeline) | `explain.py`, `mitre.py`, `remediation.py`, `dedupe.py`, `drift.py`, `replay.py`, `live_capture.py` |
| [Code: API Routes](Code-Backend-Routes) | `routes/__init__.py` and every route module |
| [Code: Training](Code-Backend-Training) | `features.py` and the whole offline batch pipeline |
| [Code: Migrations](Code-Backend-Migrations) | Alembic configuration, environment and the initial schema migration |
| [Code: Backend Tests](Code-Backend-Tests) | Every test module and the invariant it pins down |
| [Code: Frontend](Code-Frontend) | Every file under `frontend/src/`, plus `index.html`, the Vite, TypeScript and package configuration, and the generated API types. The frontend container files (`Dockerfile`, `nginx.conf`) are on [Code: Infrastructure](Code-Infrastructure) |
| [Code: Infrastructure](Code-Infrastructure) | Task runners (`Makefile`, `make.ps1`), `scripts/dev.py`, the container files, `backend/pyproject.toml`, `.env.example`, repository hygiene (`.gitignore`, `.gitattributes`, `.dockerignore`), and the tracked-but-empty data and report directories |

---

## The short version of the design

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

A flow the classifier is confident about is labelled and alerted. A flow it is
unsure about goes to the autoencoder, and if it reconstructs badly it becomes
an `UNCLASSIFIED_ANOMALY` — an alert with no family label, which is the entire
point of the second stage. [Architecture](Architecture) takes this apart box
by box.

---

## Editing this wiki

These pages are authored in the repository under `wiki/` and mirrored to the
GitHub wiki automatically. **Editing a page in the GitHub web UI will be
overwritten by the next publish, and a page created there will be deleted by
it** — edit the file in `wiki/` and commit it.

The mirror is `scripts/publish_wiki.py`. It runs automatically in GitHub
Actions (`.github/workflows/publish-wiki.yml`) on every push to `main` that
touches `wiki/`, and optionally at commit time if you install the
`post-commit` hook with `make hooks`. To publish by hand, run `make wiki` (or
`./make.ps1 wiki`); `make wiki-check` reports whether the wiki is behind
without pushing anything. [Wiki Publishing](Wiki-Publishing) has the
detail.
