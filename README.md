# Recluse

Machine-learning network intrusion detection with a SOC triage dashboard.

Two models, trained here, on labelled flow data:

| Model       | What it is                                   | Trained on                      | Answers                                | Artifact                |
| ----------- | -------------------------------------------- | ------------------------------- | -------------------------------------- | ----------------------- |
| **Stage 1** | scikit-learn `RandomForestClassifier` → LightGBM | Labelled flows: benign + known attack families | "Which named attack is this?"          | `supervised_model.pkl`  |
| **Stage 2** | PyTorch autoencoder                          | **Benign traffic only**, no attack labels | "How unlike normal traffic is this?"   | `autoencoder.pt`        |

Stage 1 names what it knows. Stage 2 catches what nobody named. The claim the
project has to defend is that it detects attack traffic it was never trained
on, and the leave-one-attack-out evaluation in Phase 4 is what makes that
measurable rather than asserted.

**The system alerts, ranks and explains. It never blocks traffic.**

---

## Status

Phase 0 of 9 is complete. **There is no trained model yet**, and no measured
detection results exist — `/api/v1/health` reports `model_version: "unloaded"`
because that is the truth. Sections below that will carry numbers are marked
as pending rather than filled with placeholders.

| Phase | Scope                      | State       |
| ----- | -------------------------- | ----------- |
| 0     | Scaffolding                | **done**    |
| 1     | Data + features            | not started |
| 2     | Supervised classifier      | not started |
| 3     | Anomaly detector           | not started |
| 4     | Fusion + LOAO evaluation   | not started |
| 5     | Backend API                | not started |
| 6     | Frontend (seven screens)   | not started |
| 7     | Drift + active learning    | not started |
| 8     | Packaging                  | not started |
| 9     | Real traffic               | not started |

Phase 0 delivers a stack that runs end to end before any ML exists: a FastAPI
service with migrations and the full v1 route surface, and a React dashboard
that renders live health data fetched from it. Endpoints later phases
implement answer `501` with the phase that fills them in, so "not built yet"
is distinguishable from "built and broken".

`BUILD_PROMPT.md` is the full specification, including the phases not yet
started.

---

## Architecture

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
point of the second stage and is a visually distinct badge in the UI.

---

## Quickstart

Requires [uv](https://docs.astral.sh/uv/), Node 20.19+ or 22.12+, and
optionally Docker.

```bash
git clone https://github.com/Team-Arachnid/recluse.git
cd recluse
cp .env.example .env
```

**Native, with hot reload:**

```bash
make dev          # Linux / macOS / Git Bash with GNU make
./make.ps1 dev    # Windows PowerShell — no make needed
```

That installs dependencies if missing, applies migrations, and runs both
processes with prefixed output:

- Dashboard — <http://localhost:5173>
- API health — <http://localhost:8000/api/v1/health>
- API docs — <http://localhost:8000/docs>

**Containerised:**

```bash
make up           # docker compose up --build
```

`make help` (or `./make.ps1 help`) lists every target: `test`, `lint`,
`migrate`, `revision`, `gen-types`, `build`, `down`, `clean`.

> On Windows, GNU make is not installed by default. `make.ps1` mirrors every
> Makefile target, so nothing needs installing. To use real make instead:
> `winget install ezwinports.make`.

---

## Layout

```
recluse/
├── BUILD_PROMPT.md            full specification, all phases
├── docker-compose.yml         backend + frontend (+ optional postgres profile)
├── Makefile / make.ps1        task runner, and its Windows equivalent
├── .env.example               every port, path and threshold input
├── data/                      raw / interim / processed — gitignored
├── reports/                   loao.md and friends (Phase 4)
├── scripts/dev.py             runs both processes for `make dev`
├── backend/
│   ├── training/              offline batch: clean, split, train, evaluate, loao
│   │   └── features.py        ← imported by training AND serving
│   ├── artifacts/             models + scaler + feature order + thresholds
│   ├── alembic/               migrations
│   ├── app/
│   │   ├── main.py            app factory, lifespan, /health
│   │   ├── config.py          pydantic-settings
│   │   ├── models.py          SQLAlchemy — portable column types only
│   │   ├── inference.py       loads artifacts once, at startup
│   │   └── routes/            alerts, score, metrics, analytics, replay, stream
│   └── tests/
└── frontend/
    ├── src/api/               typed client + TanStack Query hooks
    ├── src/types/api.d.ts     GENERATED from the OpenAPI schema
    ├── src/components/
    └── src/pages/
```

---

## Non-negotiable constraints

These are properties of the build, not preferences. Several are asserted by
tests so they cannot rot.

**No auto-block, anywhere.** There is no endpoint, button or config flag that
drops traffic. `IDS_ALLOW_AUTO_BLOCK` exists only so the constraint is
greppable: setting it to true is rejected at startup
(`backend/tests/test_config.py`), and no route path may contain `block`,
`drop` or `quarantine` (`backend/tests/test_api_surface.py`).

The arithmetic is the argument. At the configured volume of **1,000,000 flows
per day**, a false-positive rate of just 0.1% is **1,000 false alerts a day**.
Auto-blocking on that takes production down. So the system alerts, ranks and
explains, and containment — if it is added at all — stays manual, confirmed
and audited.

**The threshold is a budget, not a default.** `tau_sup` is chosen from analyst
capacity rather than set to 0.5:

```
max_alerts_per_day = ANALYST_CAPACITY_PER_HOUR × ANALYST_SHIFT_HOURS
target_FPR         = max_alerts_per_day / EXPECTED_DAILY_FLOW_VOLUME
tau_sup            = smallest threshold where FPR(tau) ≤ target_FPR
```

With the committed defaults — V = 1,000,000 flows/day, C = 40 alerts/hour, an
8-hour shift — that is **320 alerts/day** and a target FPR of **3.2 × 10⁻⁴**.
All three inputs are in `.env.example`; the service logs the resulting budget
at startup.

**Temporal splits only.** `train_test_split(shuffle=True)` is never used. Flow
records in this dataset are heavily duplicated, so random splitting leaks
near-identical rows across train and test and manufactures fake 99.9% scores.

**The anomaly model never sees attack labels.** It trains on benign traffic
exclusively, and Phase 3 asserts that in code rather than intending it. This
is what makes novel-attack detection a real claim instead of a relabelled
supervised model.

**Train and serve share one feature module.** `backend/training/features.py`
is imported by both. The API never reimplements a transform. The scaler,
feature order and a SHA-256 hash of that order are persisted in one bundle,
and the service recomputes the hash at startup and refuses to run on a
mismatch — mismatched column order produces garbage scores without raising
anything, so the check is what makes it loud.

**No training in a request handler.** Training is offline batch; the API loads
artifacts once, in the lifespan context.

**Every alert is explainable.** TreeSHAP top-5 for Stage 1, per-feature
reconstruction error for Stage 2. An alert with a score and no reason is an
alert an analyst ignores.

**Accuracy is never a headline number.** On traffic that is 99% benign, a model
that always answers "benign" scores 99%. Reported metrics are PR-AUC, per-class
recall, false-positive rate, and alerts/analyst/hour. There is no accuracy tile
in the UI, and a test asserts the dashboard renders no accuracy figure.

---

## Dataset

**CICIDS2017**, used for its day structure:

| Day       | Contents                                                        | Role               |
| --------- | --------------------------------------------------------------- | ------------------ |
| Monday    | Benign only                                                     | Autoencoder train  |
| Tuesday   | FTP-Patator, SSH-Patator                                        | Train              |
| Wednesday | DoS Hulk / GoldenEye / Slowloris / Slowhttptest, Heartbleed      | Train              |
| Thursday  | Web attacks (AM), infiltration (PM)                             | Validation         |
| Friday    | Botnet, port scan, DDoS                                         | Test               |

Monday being benign-only is a clean autoencoder training set with zero label
contamination.

Two things worth stating up front: the original CICIDS2017 labels contain
documented errors and corrected re-releases exist, and NSL-KDD is avoided
entirely as a primary dataset because it derives from 1999 traffic.

The dataset is not committed. `data/` is gitignored; Phase 1 adds the download
and cleaning steps.

---

## Results

Pending. Phases 2–4 produce them, and nothing is reported here until a real
training run has happened:

- per-class precision / recall / F1, and the confusion matrix
- PR and ROC curves side by side, with the gap between them explained
- the leave-one-attack-out table, including a **Missed** column —
  `reports/loao.md`
- FPR at the chosen threshold, and projected alerts/analyst/hour

---

## Limitations

Stating these makes the work more credible, not less.

- Flow-level features cannot see encrypted payload content.
- CICIDS2017 is synthesised lab traffic. A real enterprise baseline is messier
  and drifts faster.
- The autoencoder flags *unusual*, which is not synonymous with *malicious*. A
  new backup job will fire alerts.
- An adaptive adversary can shape traffic to stay under the threshold.
- Leave-one-attack-out measures generalisation to held-out *known* attacks. It
  is a proxy for genuinely novel ones, not proof.
- A model trained on 2017 lab traffic pointed at today's mostly-TLS traffic
  will over-fire until it is recalibrated against a local baseline. That is
  domain shift, and Phase 9 handles it with a shadow-mode burn-in rather than
  treating it as a bug.

## Authorisation

Phase 9 captures live traffic and runs self-generated attacks. Both are scoped
to networks and hosts that are owned or explicitly authorised for testing.
Packet capture on a network you do not control is illegal in most
jurisdictions regardless of intent, and so is pointing an attack tool at a
host you do not own.

---

## Development

```bash
make test         # backend pytest + frontend vitest
make lint         # ruff check + tsc --noEmit
make migrate      # alembic upgrade head
make revision m="add drift table"
make gen-types    # regenerate frontend types from the running backend
```

Frontend API types are **generated** from the FastAPI OpenAPI schema into
`frontend/src/types/api.d.ts` and are not hand-written, so the client cannot
drift from the server.

SQLite is the development database. The ORM uses portable column types
exclusively — `BigInteger` with a SQLite `Integer` variant for keys,
`String` + `CheckConstraint` instead of native enums, generic `JSON` — so
moving to Postgres is a change to `IDS_DATABASE_URL` and nothing else.
`backend/tests/test_schema_portability.py` compiles every column type against
both dialects to keep that true, and `docker-compose.yml` carries a
`postgres` profile for trying it.
