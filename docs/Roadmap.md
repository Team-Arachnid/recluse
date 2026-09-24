# Roadmap and Phase Status

This page is the authoritative answer to "what is built and what is not". It
carries the master phase table, a section per phase with goal, scope,
acceptance criteria and artifacts, the execution discipline the project is
built under, and the full acceptance checklist that Phase 8 is measured
against. It is for anyone picking up the next piece of work, and for anyone
auditing a claim made elsewhere in these docs against reality.

**Status as of this writing: Phase 0 complete, Phases 1 through 9 not
started.** Nothing below marked *not started* has code behind it beyond a
documented stub that raises `NotImplementedError` or an endpoint that answers
`501` naming the phase.

---

## Master table

| Phase | Name | Delivers | Checkpoint | Status |
| ----- | ---- | -------- | ---------- | ------ |
| 0 | Scaffolding | Repo that runs end to end with no ML: FastAPI service, migrations, full v1 route surface, React dashboard rendering live health | `make dev`, open the browser, see live health data fetched from FastAPI | **done** |
| 1 | Data and features | Cleaned CICIDS2017 in Parquet, temporal splits including a benign-only set, and a persisted preprocessing bundle carrying all five keys together: scaler, feature order, dropped columns, port encoding and schema hash | Row counts per split per class, zero duplicate rows across splits, no NaN or Inf surviving | not started |
| 2 | Supervised classifier | `supervised_model.pkl`, `tau_sup` from a false-positive budget, per-class metrics, PR and ROC curves | Classification report on the held-out test day plus a written interpretation of which classes are handled poorly and why | not started |
| 3 | Anomaly detector | `autoencoder.pt`, `tau_anom` from a benign validation percentile, persisted benign error histogram, PyOD baselines | Histogram of benign vs attack reconstruction error with the threshold line drawn; distributions visibly separate | not started |
| 4 | Fusion and LOAO | Two-stage `classify()`, the `UNCLASSIFIED_ANOMALY` path, and the leave-one-attack-out table | The completed LOAO table committed as `reports/loao.md` | not started |
| 5 | Backend API | Batch scoring, alert pipeline (explain, narrate, MITRE map, recommend, dedupe, enrich, persist), SSE stream, replay engine | Start a replay at 10x, watch alerts over `curl -N .../stream`, confirm dedup collapses bursts | not started |
| 6 | Frontend | Seven screens: triage queue, alert detail, live monitor, model performance, drift, feedback, analytics | Full walkthrough: replay, open an alert, read why / what / how-to-fix, submit a verdict, see it reflected downstream | not started |
| 7 | Drift and active learning | Nightly PSI job, guarded benign re-fit, champion/challenger retraining, full scoring audit trail | PSI snapshots stored and a challenger evaluated against the champion on the same held-out set | not started |
| 8 | Packaging | `docker compose up` with models pre-loaded, a new `make seed` target (no such target exists today), parity and contract tests, complete README | Every line of the acceptance checklist below is true | not started |
| 9 | Real traffic | Live-capture path into the same feature module, shadow-mode burn-in, locally recomputed `tau_anom`, self-run attacks | Burn-in complete with both thresholds documented, and at least one self-run attack per testable family caught and explained end to end | not started |

Status for Phase 0 is taken from `README.md`, which is the source of truth for
this table; Phases 1 through 9 are recorded there as *not started*.

---

## Phase 0 — Scaffolding

**Status: done.**

**Goal.** A repository that runs end to end, with fake or absent data, before
any ML exists — so that every later phase is adding a real component to a
working system rather than building a system around a model.

**What gets built**

- Python backend managed with `uv`, pinned to 3.12 by
  `backend/.python-version` and constrained to `>=3.11,<3.13` in
  `backend/pyproject.toml` because torch, LightGBM and shap's numba dependency
  have no settled 3.13 wheels: FastAPI, uvicorn, SQLAlchemy, Alembic, pandas,
  numpy, pyarrow, scikit-learn, LightGBM, torch, shap, pydantic-settings. Ruff
  is pinned to the same interpreter with `target-version = "py312"`.
- Vite + React 18 + TypeScript frontend: TanStack Query, TanStack Table and
  TanStack Virtual, Recharts, Tailwind, shadcn/ui primitives, lucide-react.
  Table, virtualiser and charts are installed in Phase 0 but unused until
  Phase 6 — the dependency set is complete so no later phase has to re-open
  packaging.
- SQLite via SQLAlchemy with Alembic migrations, using Postgres-compatible
  column types only so the swap is a config change.
- `GET /api/v1/health` returning `{status, model_version, uptime_s}`.
- A React page calling `/health` through TanStack Query and rendering the
  result, with skeleton, empty and error states.
- `docker-compose.yml` with backend and frontend services; `make dev` brings
  both up, and `make.ps1` mirrors every target for Windows.
- `.env.example` plus pydantic-settings config. No hardcoded paths or ports.
- The full v1 route surface registered so the OpenAPI schema — and therefore
  the generated frontend types — is complete from the start. Unimplemented
  routes answer `501` with a machine-readable body naming the phase.

**Acceptance criteria**

- [x] `make dev` brings up both processes and the browser shows live health
      data fetched from FastAPI.
- [x] `model_version` reports `"unloaded"` rather than a hardcoded string
      (`backend/tests/test_health.py`).
- [x] Every route from the specification's endpoint list appears in the served
      OpenAPI schema (`backend/tests/test_api_surface.py`).
- [x] Every unimplemented route answers `501` with a `phase` and an `endpoint`
      field; none fabricates data.
- [x] No route path contains `block`, `drop` or `quarantine`.
- [x] `IDS_ALLOW_AUTO_BLOCK=true` is rejected at startup.
- [x] Every ORM column type compiles for both SQLite and Postgres
      (`backend/tests/test_schema_portability.py`).
- [x] The schema-hash startup guard exists and refuses an inconsistent bundle,
      written before there is anything to load.
- [x] The dashboard renders no accuracy figure
      (`frontend/src/pages/SystemHealth.test.tsx`).

**Artifacts produced.** No model artifacts — that is the point of the phase.
It produces the running scaffold, the migration `9a6857dcba76_initial_schema`,
the generated type file `frontend/src/types/api.d.ts`, and the test suites that
pin the constraints.

**Links.** [Getting-Started](Getting-Started.md),
[Repository-Layout](Repository-Layout.md), [Configuration](Configuration.md),
[API-Reference](API-Reference.md), [Database-Schema](Database-Schema.md),
[Code-Infrastructure](Code-Infrastructure.md)

---

## Phase 1 — Data and features

**Status: not started.** `backend/training/clean.py`,
`backend/training/split.py` and `build_feature_matrix` in
`backend/training/features.py` all raise `NotImplementedError` naming this
phase.

**Goal.** Turn the published CICIDS2017 CSVs into clean, temporally split
Parquet, and persist a preprocessing bundle that training and serving both
read.

**What gets built**

- `clean.py`, handling each documented dataset defect explicitly: header
  whitespace, `Inf`/`NaN` in `flow_bytes_s` and `flow_packets_s` from
  zero-duration flows, exact-duplicate rows, zero-variance columns, negative
  duration and IAT values. Output to `data/interim/` as Parquet, not CSV.
- `split.py`, producing the temporal split from the day structure recorded in
  its own docstring: Tuesday and Wednesday train, Thursday validation, Friday
  test, with Monday reserved in full for the benign-only Stage 2 training set.
  Output to `data/processed/`.
- The transforms in `features.py`: drop the leakage columns, apply the port
  encoding, reindex to `feature_order`, apply the fitted `RobustScaler`.
- The two destination-port encodings prepared and recorded: raw port, and port
  bucketed into service groups plus a one-hot column for the top 20 ports
  computed on the training split only. Which one an artifact was built with is
  recorded in the bundle's `port_encoding` key. BUILD_PROMPT Part 4 lists the
  ablation under this phase because this is where the encodings are defined,
  but the pair of training runs that compares them, and the report, belong to
  Phase 2 — Phase 1 fits no classifier.
- `artifacts/preprocessing.pkl` written through `build_preprocessing_bundle`
  and `save_preprocessing_bundle`.

**Acceptance criteria**

- [ ] Temporal split; zero duplicate rows shared across splits.
- [ ] IP, port and timestamp leakage columns dropped and logged.
- [ ] Destination-port ablation run and reported. *(Encodings prepared here;
      the comparison runs in Phase 2.)*
- [ ] Scaler, feature order, dropped columns, port encoding and schema hash
      persisted together in one bundle — all five keys, not three.
- [ ] No NaN or Inf survives into `data/processed/`.
- [ ] Row counts per split per class printed and reported.

**Artifacts produced.** `data/interim/*.parquet`,
`data/processed/{train,val,test}.parquet`,
`data/processed/benign_train.parquet` (Monday in full plus the benign rows of
Tuesday and Wednesday, asserted attack-free in code — it is the file the whole
Stage 2 claim rests on), `backend/artifacts/preprocessing.pkl`. Every one of
these paths is gitignored; they are reproducible output, not source.

**Links.** [Data-Pipeline](Data-Pipeline.md),
[Code-Backend-Training](Code-Backend-Training.md),
[Anti-Patterns](Anti-Patterns.md)

---

## Phase 2 — Supervised classifier

**Status: not started.** `backend/training/train_supervised.py` and
`backend/training/evaluate.py` raise `NotImplementedError` naming this phase.

**Goal.** A working, honestly evaluated Stage 1 model with an operating
threshold derived from an analyst budget rather than from `argmax`.

**What gets built**

- `RandomForestClassifier` baseline: `n_estimators=300`, `max_depth` tuned
  against the validation day, multi-class. Committed as a running baseline
  before anything else is touched.
- LightGBM as a swap-in upgrade afterwards, keeping the RandomForest artifact
  as a fallback.
- `class_weight='balanced'` or explicit per-class weights. No SMOTE; if
  demonstrated at all it is an ablation that underperforms.
- Early stopping against the validation day.
- Class collapse to: `benign`, `dos`, `ddos`, `brute_force`, `port_scan`,
  `web_attack`, `botnet`, `infiltration`, with the sub-family mapping logged.
  The attack subset of that vocabulary is already fixed in code as
  `ALERT_FAMILIES` in `backend/app/models.py`.
- `tau_sup` selection from the false-positive budget, using
  `Settings.target_fpr`, and persisted into the artifact bundle.
- Metrics: per-class precision, recall, F1 and support; confusion matrix; PR
  and ROC curves rendered side by side; PR-AUC as headline; FPR at the chosen
  threshold; projected alerts per analyst per hour.

**Acceptance criteria**

- [ ] RandomForest baseline trained, evaluated and committed before the
      LightGBM upgrade is attempted.
- [ ] `tau_sup` derived from the stated false-positive budget, not from 0.5.
- [ ] PR-AUC reported as the headline; accuracy appears in a table at most.
- [ ] Classification report produced on the held-out test day.
- [ ] A one-paragraph written interpretation of which classes the model handles
      poorly and why.

**Artifacts produced.** `backend/artifacts/supervised_model.pkl`,
`backend/artifacts/model_card.json` carrying `version`, `schema_hash` and
`thresholds.tau_sup` (the fields `ModelBundle._load_model_card` reads), plus
curve and confusion-matrix outputs under `reports/`.

**Links.** [ML-Models](ML-Models.md),
[Code-Backend-Training](Code-Backend-Training.md),
[Configuration](Configuration.md), [Testing](Testing.md)

---

## Phase 3 — Anomaly detector

**Status: not started.** `backend/training/train_autoencoder.py` raises
`NotImplementedError` naming this phase.

**Goal.** A benign-only autoencoder whose reconstruction error separates
benign from attack traffic, with a threshold set from a benign percentile.

**What gets built**

- Training set of benign rows only: Monday in full plus the benign rows of
  Tuesday and Wednesday, with the attack-free property asserted in code.
- Architecture `input(d) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(d)`, ReLU,
  MSE loss, Adam, early stopping on benign validation loss, dropout 0.1 in the
  encoder, batch norm.
- Score as per-row mean squared reconstruction error.
- `tau_anom` as the 99.5th percentile of reconstruction error on held-out
  benign validation data.
- The benign error distribution persisted as histogram bins, not raw rows —
  the dashboard threshold slider and drift detection both read it. The bundle
  field `benign_error_histogram` already exists on `ModelBundle`.
- PyOD baselines on the same split: IsolationForest, LOF and ECOD. `pyod` is
  not yet a dependency — `backend/pyproject.toml` declares scikit-learn,
  LightGBM, torch and shap but no PyOD, so Phase 3 adds it before it can run
  the comparison.

**Acceptance criteria**

- [ ] Autoencoder training set asserted attack-free in code, not merely
      intended.
- [ ] `tau_anom` set from the benign validation percentile.
- [ ] PyOD baselines run and compared; a baseline that wins is reported rather
      than hidden.
- [ ] Histogram of benign vs attack reconstruction error produced with the
      threshold line drawn, and the distributions visibly separate.

**Artifacts produced.** `backend/artifacts/autoencoder.pt` (a state dict,
loaded with `weights_only=True`), the benign error histogram, and `tau_anom` in
the model card.

**Links.** [ML-Models](ML-Models.md),
[Code-Backend-Training](Code-Backend-Training.md),
[Frontend-Screens](Frontend-Screens.md)

---

## Phase 4 — Fusion and the headline evaluation

**Status: not started.** `ModelBundle.score_batch` in
`backend/app/inference.py` raises `NotImplementedError` naming this phase, and
`backend/training/loao.py` raises one too.

**Goal.** Join the two stages into one decision function, and measure the
project's headline claim.

**What gets built**

- The fusion rule: if the max attack-class probability is at or above
  `tau_sup`, emit `KNOWN` with the argmax family; otherwise score the row with
  the autoencoder and, if the reconstruction error is at or above `tau_anom`,
  emit `UNCLASSIFIED_ANOMALY` with no family; otherwise emit nothing.
- `score_batch`, batch-only by design.
- The leave-one-attack-out loop: for each family, drop it from supervised
  training, retrain Stage 1, leave Stage 2 untouched, run fusion over a test
  set containing the family, and record what fraction was flagged and by which
  stage.

**Acceptance criteria**

- [ ] LOAO table complete for every attack family, including a **Missed**
      column.
- [ ] The autoencoder is confirmed unchanged across LOAO runs.
- [ ] `UNCLASSIFIED_ANOMALY` survives the pipeline as a distinct kind, never
      collapsed into a family label.

**Artifacts produced.** `reports/loao.md`, one row per attack family with the
columns *Held-out family*, *Caught by Stage 1*, *Caught by Stage 2*, *Total
recall* and *Missed* — the shape fixed by the docstring of
`backend/training/loao.py`. The empty template is in
[ML-Models](ML-Models.md#result-table). **Not measured yet — this phase
produces those numbers.** `reports/` currently contains only a `.gitkeep`.

**Links.** [ML-Models](ML-Models.md), [Architecture](Architecture.md),
[Code-Backend-Training](Code-Backend-Training.md)

---

## Phase 5 — Backend API

**Status: not started.** Every v1 route other than `/health` answers `501`
naming this phase or a later one, and `app/explain.py`, `app/mitre.py`,
`app/remediation.py` and `app/replay.py` all raise `NotImplementedError`
naming it.

**Goal.** Turn scores into alerts an analyst can work, and stream them live.

**What gets built**

- Batch scoring behind `POST /score`, accepting a list of flow records and
  scoring them as a single matrix.
- The alert pipeline, in order: **explain** (TreeSHAP top-5 for Stage 1,
  reconstruction-error top-5 for Stage 2), **narrate** (per-feature phrase
  templates into English), **MITRE map and recommend** (static reviewed lookup
  in `app/mitre.py` and `app/remediation.py`), **dedupe** (on
  `(src_host, alert_class, floor(ts, window))` — `dedupe_key` in
  `app/dedupe.py` is already implemented), **enrich** (asset criticality, prior
  alert count), **persist**, **push over SSE**.
- The replay engine as an asyncio background task streaming held-out test rows
  at 1x, 10x or 100x, scoring in batches.
- SSE rather than WebSockets: the feed is one-directional.
- The alert query endpoints: filter, sort, cursor-paginate; detail; verdict;
  related-by-host within 24 hours.
- Metrics endpoints: per-class metrics and curves, threshold what-if,
  analytics summary, MITRE coverage.

**Acceptance criteria**

- [ ] Batch scoring; models loaded once at startup.
- [ ] Schema-hash check fails fast on mismatch. *(Already true from Phase 0.)*
- [ ] Explanation attached to every alert.
- [ ] Dedup verified under burst load.
- [ ] SSE stream stable through a 100x replay.
- [ ] Zero auto-block code paths. *(Already asserted from Phase 0.)*
- [ ] A 10x replay produces alerts visible over
      `curl -N localhost:8000/api/v1/stream`, with dedup visibly collapsing
      bursts.

**Artifacts produced.** Rows in `alerts`, and an OpenAPI schema whose
operations return real payloads instead of `501`.

**Links.** [API-Reference](API-Reference.md),
[Code-Backend-Routes](Code-Backend-Routes.md),
[Code-Backend-Pipeline](Code-Backend-Pipeline.md),
[Database-Schema](Database-Schema.md)

---

## Phase 6 — Frontend

**Status: not started.** The frontend currently renders one page,
`SystemHealth`, built from `HealthPanel` and the `useHealth` query hook.

**Goal.** Seven screens, ordered as a design argument rather than a list.

**What gets built**

1. **Triage Queue** — the landing page, not an overview dashboard. TanStack
   Table sorted by risk score, filters including a one-click
   `UNCLASSIFIED_ANOMALY` chip, bulk dismiss, a thin stat strip, and no
   accuracy tile.
2. **Alert Detail** — a side drawer structured around why this was flagged,
   what it likely is, and how to fix it, with ground truth shown only in replay
   mode and badged demo-only. No block button.
3. **Live Traffic Monitor** — SSE ticker, flows/sec and alerts/sec sparklines,
   replay speed control, and the anomaly-score histogram with a draggable
   threshold line that updates projected alerts/hour off `/metrics/threshold`,
   debounced at about 150 ms.
4. **Model Performance** — per-class table, confusion-matrix heatmap, PR and
   ROC side by side with the caption explaining the gap, and the LOAO panel
   given real visual weight.
5. **Drift Monitor** — PSI per feature with warning bands at 0.1 and 0.25, the
   training benign distribution overlaid on the last 24 hours, a
   retrain-recommended banner, and the model registry.
6. **Feedback Loop** — new labels since last retrain, TP/FP breakdown,
   model-analyst disagreement rate, and a retrain button.
7. **Analytics** — alerts over time stacked by family vs unclassified, family
   breakdown, top hosts, ports and sources, SOC throughput, and a MITRE
   coverage heatmap.

**Acceptance criteria**

- [ ] Triage queue is the landing page, sorted by risk.
- [ ] `UNCLASSIFIED_ANOMALY` visually distinct and filterable in one click.
- [ ] Draggable threshold updating projected alert volume live.
- [ ] PR and ROC rendered side by side with an explanatory caption.
- [ ] LOAO panel present and prominent.
- [ ] Verdict submission invalidates and refreshes the queue.
- [ ] No accuracy hero tile anywhere.
- [ ] Alert Detail answers why, what-it-is and how-to-fix for every alert,
      including the honest no-playbook case for unclassified anomalies.
- [ ] Analytics shows trends by family, top hosts and sources, SOC throughput,
      and MITRE coverage.
- [ ] Alert table virtualised; skeleton, empty and error states on every
      screen.

**Artifacts produced.** The seven screens, and a regenerated
`frontend/src/types/api.d.ts` matching the Phase 5 schema.

**Links.** [Frontend-Screens](Frontend-Screens.md),
[Code-Frontend](Code-Frontend.md), [API-Reference](API-Reference.md)

---

## Phase 7 — Drift and active learning

**Status: not started.** `population_stability_index` in `backend/app/drift.py`
raises `NotImplementedError` naming this phase, and `GET /metrics/drift` and
`GET /models` return `501` naming it.

**Goal.** Close the loop from analyst judgement back to the model, and notice
when the world has moved.

**What gets built**

- A nightly job computing PSI per feature against the training reference
  distribution, storing snapshots. Bands at 0.1 moderate and 0.25 significant.
- Autoencoder benign-baseline re-fit on recent confirmed-benign traffic,
  guarded against poisoning: a row enters the refit pool only after an analyst
  has confirmed it a false positive, and the fraction contributed by any single
  source host is capped.
- A retraining pipeline consuming `analyst_verdicts`, producing a challenger,
  evaluating champion against challenger on the same held-out set, and
  promoting only on improvement, with the comparison logged.
- A full audit trail of which model version scored which alert.

**Acceptance criteria**

- [ ] PSI snapshots stored per feature over time.
- [ ] Benign refit pool requires analyst FP confirmation and caps per-host
      contribution.
- [ ] Champion and challenger evaluated on the same held-out set; promotion
      only on improvement, comparison logged.
- [ ] Every alert records the model version that produced it.

**Artifacts produced.** PSI snapshots, challenger model versions, and rows in
`model_versions` with `stage` in `champion`, `challenger` or `archived`. The
columns for all of this — `Alert.model_version`,
`AnalystVerdict.consumed_at`, the whole `ModelVersion` table — already exist
from Phase 0.

**Links.** [Code-Backend-Pipeline](Code-Backend-Pipeline.md),
[Database-Schema](Database-Schema.md), [Frontend-Screens](Frontend-Screens.md)

---

## Phase 8 — Packaging

**Status: not started.**

**Goal.** A clean clone that demos in one command, with the documentation that
makes the results readable.

**What gets built**

- `docker compose up` bringing the whole stack up with models pre-loaded.
- `make seed` populating a demo database so the dashboard is never empty on
  first open. No `seed` target exists yet — `Makefile` and `make.ps1` currently
  expose `help`, `env`, `install`, `dev`, `backend`, `frontend`, `migrate`,
  `revision`, `test`, `test-backend`, `test-frontend`, `lint`, `format`,
  `typecheck`, `gen-types`, `build`, `up`, `down`, `logs`, `ps`, `docs`,
  `docs-serve` and `clean`, and Phase 8 adds `seed`
  to both.
- Tests: a feature-parity test asserting `features.py` produces identical
  output on the training and serving paths, the schema-hash mismatch test, a
  dedup test, and API contract tests.
- A README carrying the architecture diagram, the LOAO table, the PR-vs-ROC
  explanation, the false-positive-budget arithmetic, the alert-not-block
  justification, the limitations and honest next steps. The limitations section
  states all five plainly: flow-level features cannot see encrypted payload
  content; CICIDS2017 is synthesised lab traffic and a real enterprise baseline
  is messier and drifts faster; the autoencoder flags *unusual*, which is not
  synonymous with *malicious* — a new backup job will fire alerts; an adaptive
  adversary can shape traffic to stay under the threshold; and LOAO measures
  generalisation to held-out *known* attacks, which is a proxy for genuinely
  novel ones rather than proof. See
  [Project-Overview](Project-Overview.md).

**Acceptance criteria.** Every line of the
[acceptance checklist](#acceptance-checklist) below.

**Artifacts produced.** A seeded demo database, a complete README, and a
compose stack that works from a clean clone.

**Links.** [Getting-Started](Getting-Started.md), [Testing](Testing.md),
[Code-Infrastructure](Code-Infrastructure.md),
[Docs-Publishing](Docs-Publishing.md)

---

## Phase 9 — Real traffic

**Status: not started.** `start_ingest` in `backend/app/live_capture.py` raises
`NotImplementedError` naming this phase, and `POST /ingest/start` returns `501`
naming it.

**Goal.** Point the pipeline at traffic that CICIDS2017 never shaped, and
report what actually happened.

**What gets built**

- A live-capture path alongside replay, never replacing it, feeding the exact
  same `features.py`, the exact same inference path and the exact same alert
  pipeline. Live traffic needing its own scoring code would break the
  train/serve-skew defence outright.
- Capture from an isolated lab of two to four VMs on one virtual switch, or a
  router mirror port, or a single machine's own interface. Zeek or Suricata
  flow logs, or `tcpdump` plus CICFlowMeter over the pcap.
- A shadow-mode burn-in: score everything, alert no one, collect the local
  reconstruction-error distribution, and recompute `tau_anom` from that local
  percentile rather than the CICIDS2017-derived one.
- Self-run attacks inside the owned lab — `nmap` port scan, `hydra` brute force
  against a throwaway service, `hping3` or a slowloris-style tool against an
  owned endpoint — giving exact ground truth.
- A README section recording the recalibration: the local threshold against the
  dataset-derived one, and what happened when the self-generated attack traffic
  hit the pipeline.

**Acceptance criteria**

- [ ] All capture and attack testing scoped to networks and hosts owned or
      explicitly authorised for testing.
- [ ] Shadow-mode burn-in run and a local `tau_anom` computed before any live
      alert reaches the queue.
- [ ] Both thresholds documented, with the gap between them explained.
- [ ] At least one self-run attack per testable family caught and correctly
      explained end to end.

**Artifacts produced.** A locally derived `tau_anom`, the burn-in distribution,
and the recalibration section of the README.

**Expected result, stated in advance.** A false-positive rate well above what
the CICIDS2017 validation numbers promise. A 2017 lab baseline is not today's
mostly-encrypted traffic, so nearly everything looks anomalous relative to it.
That is domain shift, and it is why the burn-in exists. Stage 1 faces the same
shift from the other direction: it can only name families it saw at training
time, in a feature space shaped by 2017 traffic, so expect it to under-fire and
expect Stage 2 to carry more of the weight than it did on the dataset.

**Links.** [Project-Overview](Project-Overview.md),
[Code-Backend-Pipeline](Code-Backend-Pipeline.md), [FAQ](FAQ.md)

---

## Execution discipline

**One phase per session.** Read the specification, execute one phase only, stop
at its checkpoint and report. This is not ceremony. A build handed ten phases
at once produces nine stubs and a claim of completion, and the failure is hard
to see afterwards because stubs and finished work look identical from the
outside until something is exercised.

The loop is:

```
read the phase  ->  build only that phase  ->  stop at the checkpoint
      ^                                               |
      |                                               v
      +-------  advance  <-------  verify acceptance criteria
```

**Stop at the checkpoint, verify, then advance.** Each phase has acceptance
criteria stated above that can be checked without judgement. Verification means
running them, not reading them. The Phase 0 criteria are asserted by tests for
exactly this reason: they cannot rot silently between sessions.

**The time-constrained path.** If there is not time for all ten phases, build
**Phases 0 through 5, plus the Triage Queue and Alert Detail screens** from
Phase 6. That delivers the complete narrative — a trained model, a measured
novel-attack result, a live replay, and a queue an analyst can actually work —
at roughly half the total work.

What that path deliberately drops:

| Dropped | Why it is droppable |
| ------- | ------------------- |
| Phase 6 screens 3 to 7 | The queue and the detail drawer carry the argument. The rest are supporting evidence. |
| Phase 7 (drift, active learning) | Polish. It demonstrates system design, not detection. |
| Phase 8 (packaging) | Polish. Valuable, but it does not carry the argument. |
| Phase 9 (real traffic) | The strongest possible addition, and the correct thing to build *last* — after the core story from Phases 0 to 6 is solid and demoable on its own. |

---

## Acceptance checklist

Phase 8 is not complete until every line here is true. Boxes are ticked only
where the property is true in the repository today.

### Data

- [ ] Temporal split; zero duplicate rows shared across splits
- [ ] IP, port and timestamp leakage columns dropped and logged
- [ ] Destination-port ablation run and reported
- [ ] Scaler, feature order and schema hash persisted together

### Models

- [ ] RandomForest baseline trained, evaluated and committed before attempting
      the LightGBM upgrade
- [ ] Autoencoder training set asserted attack-free in code
- [ ] `tau_sup` derived from a stated false-positive budget, not 0.5
- [ ] `tau_anom` set from a benign validation percentile
- [ ] PyOD baselines run and compared
- [ ] LOAO table complete, including a **Missed** column

### Backend

- [ ] Batch scoring; models loaded once at startup
- [x] Schema-hash check fails fast on mismatch
- [ ] Explanation attached to every alert
- [ ] Dedup verified under burst load
- [ ] SSE stream stable through a 100x replay
- [x] Zero auto-block code paths

### Frontend

- [ ] Triage queue is the landing page, sorted by risk
- [ ] `UNCLASSIFIED_ANOMALY` visually distinct and filterable in one click
- [ ] Draggable threshold updating projected alert volume live
- [ ] PR and ROC rendered side by side with an explanatory caption
- [ ] LOAO panel present and prominent
- [ ] Verdict submission invalidates and refreshes the queue
- [x] No accuracy hero tile anywhere
- [ ] Alert Detail answers why, what-it-is and how-to-fix for every alert,
      including the honest no-playbook case for unclassified anomalies
- [ ] Analytics screen shows trends by family, top hosts and sources, SOC
      throughput, and MITRE coverage

### Real-world testing (Phase 9, optional but strongly recommended)

- [ ] All capture and attack testing scoped to networks and hosts owned or
      explicitly authorised for testing
- [ ] Shadow-mode burn-in run and a local `tau_anom` computed before any live
      alert reaches the queue
- [ ] Both thresholds — dataset-derived and local — documented, with the gap
      between them explained
- [ ] At least one self-run attack per testable family caught and correctly
      explained end to end

### Docs

- [ ] README carries LOAO results, FP arithmetic, alert-not-block
      justification, limitations
- [ ] `docker compose up` works from a clean clone

Three boxes are ticked, and all three are Phase 0 guarantees asserted by tests
rather than by assertion: the schema-hash guard
(`test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash`), the absence
of any blocking code path (`test_no_route_mentions_blocking` plus
`test_auto_block_cannot_be_enabled`), and the absence of an accuracy figure in
the UI (`renders no accuracy figure anywhere`). Everything else is open work.
