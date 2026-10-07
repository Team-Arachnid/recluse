# Roadmap and Phase Status

This page is the authoritative answer to "what is built and what is not". It
carries the master phase table, a section per phase with goal, scope,
acceptance criteria and artifacts, the execution discipline the project is
built under, and the full acceptance checklist that Phase 8 is measured
against. It is for anyone picking up the next piece of work, and for anyone
auditing a claim made elsewhere in these docs against reality.

**Status as of this writing: Phases 0 through 8 complete; Phase 9 is next.**
Every v1 endpoint but one returns live data; `POST /ingest/start` belongs to
Phase 9 and answers `501` naming it. Phases 1 through 8 have been run end to end
against the real 2.83M-row CICIDS2017 release; their numbers below are measured,
not estimated. Both models are trained and fused by one rule that the serving
path and the hold-out evaluation share, so the two-stage claim is measured as a
system, and the
[leave-one-attack-out table](#the-leave-one-attack-out-table-measured) is where that
measurement lives. Since Phase 8 one trained pair is committed under
`backend/release/` with a sha256 manifest, so a clean clone serves both models:
`docker compose up` installs the release and seeds a demo by replaying real
held-out flows through the real pipeline. Everything else training writes stays
gitignored reproducible output, and a model trained locally always takes
precedence over the release.

---

## Master table

| Phase | Name | Delivers | Checkpoint | Status |
| ----- | ---- | -------- | ---------- | ------ |
| 0 | Scaffolding | Repo that runs end to end with no ML: FastAPI service, migrations, full v1 route surface, React dashboard rendering live health | `make dev`, open the browser, see live health data fetched from FastAPI | **done** |
| 1 | Data and features | Cleaned CICIDS2017 in Parquet, temporal splits including a benign-only set, and a persisted preprocessing bundle carrying all five keys together: scaler, feature order, dropped columns, port encoding and schema hash | Row counts per split per class, zero duplicate rows across splits, no NaN or Inf surviving | **done** |
| 2 | Supervised classifier | `supervised_model.pkl`, `tau_sup` from a false-positive budget, per-class metrics, PR and ROC curves | Classification report on the held-out test day plus a written interpretation of which classes are handled poorly and why | **done** |
| 3 | Anomaly detector | `autoencoder.pt`, `tau_anom` from a benign validation percentile, persisted benign error histogram, PyOD baselines | Histogram of benign vs attack reconstruction error with the threshold line drawn; distributions visibly separate | **done** |
| 4 | Fusion and LOAO | Two-stage `classify()`, the `UNCLASSIFIED_ANOMALY` path, and the leave-one-attack-out table | The completed LOAO table committed as `reports/loao.md` | **done** |
| 5 | Backend API | Batch scoring, alert pipeline (explain, narrate, MITRE map, recommend, enrich, dedupe, persist), SSE stream, replay engine | Start a replay at 10x, watch alerts over `curl -N .../stream`, confirm dedup collapses bursts | **complete** — `reports/phase5_api.md` |
| 6 | Frontend | Seven screens: triage queue, alert detail, live monitor, model performance, drift, feedback, analytics | Full walkthrough: replay, open an alert, read why / what / how-to-fix, submit a verdict, see it reflected downstream | **done** — `scripts/phase6_checkpoint.py` |
| 7 | Drift and active learning | Nightly PSI job, guarded benign re-fit, champion/challenger retraining, full scoring audit trail | PSI snapshots stored and a challenger evaluated against the champion on the same held-out set | **done** — `reports/phase7_retrain.md` |
| 8 | Packaging | `docker compose up` with models pre-loaded, `make seed`, parity and contract tests, complete README | Every line of the acceptance checklist below is true | **done** — the checklist below |
| 9 | Real traffic | Live-capture path into the same feature module, shadow-mode burn-in, locally recomputed `tau_anom`, self-run attacks | Burn-in complete with both thresholds documented, and at least one self-run attack per testable family caught and explained end to end | not started |

Phase 9 is the one phase not yet built.

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
- [x] Every unimplemented route answers `501` with a `phase` and an `endpoint`. Since Phase 5 the suite also asserts the converse — that every *implemented* route does **not** answer 501 — because that is the half which rots silently once a route leaves the deferred list
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

**Status: done.** `clean.py`, `split.py`, `preprocess.py`, `console.py` and
the transforms in `features.py` are covered by 85 tests, and the pipeline has
been run end to end against the real release — 2,830,743 raw flow records in,
four Parquet files and a preprocessing bundle out. Every number on this page is
measured.

The dataset came from the Kaggle mirror of CICIDS2017 rather than from
[unb.ca](https://www.unb.ca/cic/datasets/ids-2017.html), whose download sits
behind a licence form that cannot be scripted. `make data-fetch` pulls it.
Two consequences of that mirror are worth knowing, because both changed the
code:

* It is the **MachineLearningCSV** release, which CIC published with `Flow ID`,
  both IP columns, `Source Port` **and `Timestamp`** already removed. Most of
  the leakage deny-list is therefore moot here — those columns never arrive —
  and the temporal split recovers the capture day from the file names instead
  of from a timestamp.
* Its web-attack labels carry `U+FFFD` where the original cp1252 en dash was;
  whoever converted the files to UTF-8 replaced the byte rather than decoding
  it. Left alone that splits one attack family across two class names, and it
  cannot be encoded to cp1252 at all, so printing the per-class table ended the
  run on Windows after all the work was done.

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
  recorded in the bundle's `port_encoding` key. The specification lists the
  ablation under this phase because this is where the encodings are defined,
  but the pair of training runs that compares them, and the report, belong to
  Phase 2 — Phase 1 fits no classifier.
- `artifacts/preprocessing.pkl` written through `build_preprocessing_bundle`
  and `save_preprocessing_bundle`.

**Acceptance criteria**

- [x] **Temporal split; zero duplicate rows shared across splits.** Verified
      on the written Parquet: train/val, train/test and val/test share zero
      rows. Splitting is by capture day, and because dropping the splitting key
      can make rows from different days identical, **41,984** such rows were
      detected and removed rather than assumed away.
- [x] **IP, port and timestamp leakage columns dropped and logged.** Partly
      free here: the MachineLearningCSV release ships without `Flow ID`, the
      IPs, `Source Port` and `Timestamp`, so `dropped_columns` is empty on this
      data. The deny-list still runs, still drops them when present, and the
      capture day is dropped at the end of splitting. What is verified is the
      outcome — none of those columns appears in any split or in
      `feature_order`.
- [x] **Destination-port encodings prepared and recorded.** Both fitted on the
      real training split: `raw` gives 70 features, `bucketed` gives 92 (IANA
      service groups plus a top-20 one-hot, fitted on train alone). The two
      produce different schema hashes, so a bundle cannot be confused for the
      other. *The comparison itself runs in Phase 2.*
- [x] **Scaler, feature order, dropped columns, port encoding and schema hash
      persisted together** — all five keys, not three. Asserted on the written
      file, and the hash re-verifies on load.
- [x] **No NaN or Inf survives into `data/processed/`.** Checked across all
      four written files: zero of each.
- [x] **Row counts per split per class printed and reported.** The table is
      below.

### Measured, on the real release

Cleaning, across all eight files:

| | Rows |
| --- | --- |
| Raw | 2,830,743 |
| After cleaning | 2,572,640 *(258,103 dropped, 9.12%)* |
| Exact duplicate rows removed | 255,236 |
| Rows dropped for non-finite rates | 2,867 *(from 4,376 Inf values)* |
| Negative durations and IATs clipped | 3,253 |
| Globally zero-variance columns dropped | 8 |

The duplicate count is the one to notice. A quarter of a million exact
duplicates is what the brief means by *the single largest source of inflated
scores published on this dataset* — leave them in, shuffle, and the same flow
lands in train and test.

Splits, by capture day:

| Split | Rows | Classes |
| --- | --- | --- |
| `train` (Tue + Wed) | 1,024,072 | BENIGN 821,166 · DoS Hulk 172,846 · DoS GoldenEye 10,286 · FTP-Patator 5,931 · DoS slowloris 5,385 · DoS Slowhttptest 5,228 · SSH-Patator 3,219 · Heartbleed 11 |
| `val` (Thu) | 398,507 | BENIGN 396,328 · Web Attack Brute Force 1,470 · Web Attack XSS 652 · Infiltration 36 · Web Attack Sql Injection 21 |
| `test` (Fri) | 595,894 | BENIGN 375,238 · DDoS 128,014 · PortScan 90,694 · Bot 1,948 |
| `benign_train` (Mon + benign Tue/Wed) | 1,331,862 | BENIGN only, asserted in code |

No attack family appears in both train and test, which is what makes the
Phase 4 hold-out evaluation mean anything.

**Three classes are vanishingly rare:** Heartbleed has 11 rows, Web Attack Sql
Injection 21, Infiltration 36. Phase 2 cannot report a trustworthy per-class
recall for any of them from a handful of examples, and should say so rather
than print a number. This is the clearest thing the real run surfaced that the
specification did not anticipate.

The preprocessing bundle: 70 features under the `raw` port encoding, schema
hash `sha256:ae1b67b1…`, loaded and re-verified through the API's startup
check. That is what `make data` writes. Phase 2 then rewrites
`preprocessing.pkl` with the champion's own bundle — 92 features under the
bucketed encoding it selected, hash `sha256:76724838…` — so that the model and
its scaler always ship as a pair.

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

**Status: complete.** Trained on the real release; the write-up is
`reports/phase2_supervised.md` and the ablation is `reports/port_ablation.md`.

**What the real data turned out to be.** One property of CICIDS2017 reshapes
this whole phase, and the specification did not anticipate it: **each attack
family runs on exactly one capture day.** A temporal split therefore never
gives the supervised model a same-family train/test pair. Tuesday and Wednesday
carry brute force and denial of service; Thursday carries web attacks and
infiltration; Friday carries DDoS, port scan and botnet. Stage 1's vocabulary is
whatever the training days hold — `benign`, `dos`, `brute_force` — and *every*
family on the validation and test days is one it has never seen.

That is not a defect in the split. It is the project's thesis arriving early: a
supervised stage cannot name what it was never shown, which is the entire reason
there is a Stage 2. It does mean the per-class recall for Friday's families is a
structural zero rather than a measurement of a model that tried and failed, and
the write-up says so in those words.

**Goal.** A working, honestly evaluated Stage 1 model with an operating
threshold derived from an analyst budget rather than from `argmax`.

**What gets built**

- `training/labels.py` — the class collapse, keyed on an exact canonical form of
  the published label so `Web Attack Brute Force` cannot fall into
  `brute_force` by substring. An unmapped label raises instead of becoming
  benign. A support floor holds families below 100 rows out of the vocabulary.
- `training/metrics.py` — the threshold arithmetic and the quantities the phase
  reports, shared by the trainer and the evaluator so the two cannot compute the
  same number two ways.
- `training/estimators.py` — the LightGBM wrapper, in a module that is never run
  as a script so the artifact survives leaving the process that wrote it.
- `training/train_supervised.py` — RandomForest baseline, LightGBM upgrade,
  `tau_sup` from the budget, champion promotion, and the port ablation.
- `training/evaluate.py` — the held-out test day, once, after every choice.

**Acceptance criteria**

- [x] **RandomForest baseline trained, evaluated and committed before the
      LightGBM upgrade.** `n_estimators=300`, `class_weight="balanced"`,
      `max_depth` swept against the validation day over 8 / 16 / 24 / 32 / 48 /
      64 → PR-AUC 0.0855 / 0.2904 / 0.4010 / 0.4327 / 0.4433 / 0.4433. It
      settles at 48 and is identical at 64 because no tree on this data grows
      deeper, so the choice ends on a measured plateau rather than at the edge
      of a grid. It survives as the fallback artifact `supervised_rf.pkl` with
      its own matching `preprocessing_rf.pkl`, written up in
      `reports/phase2_supervised_rf.md`.
- [x] **LightGBM promoted on a measured comparison, not an assumption.**
      Validation PR-AUC 0.8816 against the forest's 0.7010. Promotion compares
      the challenger with the incumbent recorded in `model_card.json` and logs
      the result; a weaker challenger stays on disk under its own name.
- [x] **`tau_sup` derived from the stated false-positive budget, not 0.5.**
      `tau_sup = 0.387908`, the smallest validation-day threshold whose FPR fits
      320 alerts/day: 126 false alerts in 396,328 benign rows, an FPR of
      3.18 × 10⁻⁴ against a target of 3.20 × 10⁻⁴.
- [x] **PR-AUC reported as the headline; accuracy appears in one table cell.**
      Accuracy is 0.630 on a test day that is 63.0% benign, and the report says
      so on the line below it.
- [x] **Classification report produced on the held-out test day**, at the
      operating point rather than at `argmax`, because that is what the served
      system emits.
- [x] **A written interpretation of which classes the model handles poorly and
      why.** Generated from the measured numbers rather than written once by
      hand, so a rerun cannot leave a stale claim behind.
- [x] **Destination-port ablation run and reported.** The criterion Phase 1
      deferred to here; `reports/port_ablation.md`.
- [x] **No SMOTE, no shuffled split, no `.fit()` reachable from a request
      handler.**

### Measured, on the real release

Champion `stage1-lgbm`: LightGBM, 92 features under the bucketed port encoding,
early stopping at iteration 227 of a possible 1,000.

| | Validation (Thu) | Test (Fri) |
| --- | --- | --- |
| Rows | 398,507 | 595,894 |
| Benign share | 99.5% | 63.0% |
| **PR-AUC** | **0.8816** | **0.8468** |
| ROC-AUC | 0.9965 | 0.8820 |
| FPR at `tau_sup` | 3.18 × 10⁻⁴ | 1.63 × 10⁻⁴ |
| Projected false alerts/day at V = 1,000,000 | 318 | 163 |
| Alerts per analyst per hour | 39.7 | 20.3 |
| Accuracy *(non-headline)* | — | 0.630 |

The two ROC-AUC figures are the same model on two days, and they differ by 0.11
while PR-AUC differs by 0.03. The variable is the benign share, not the model.
That is the argument for PR-AUC as the headline, made as a measurement rather
than a claim.

Detection on the test day, per family — all three unseen in training:

| Family | Rows | Flagged | Recall |
| --- | --- | --- | --- |
| `ddos` | 128,014 | 48,600 | 38.0% |
| `port_scan` | 90,694 | 501 | 0.6% |
| `botnet` | 1,948 | 0 | 0.0% |

DDoS partially generalises from Wednesday's DoS Hulk, which is the same shape of
attack. Port scan and botnet look like nothing in the training days. Across the
whole day Stage 1 surfaces 22.3% of the attack traffic, so **77.7% of it
produces no Stage 1 alert** — and that number is the measured size of the job
Phase 3 is being built to do.

**Two findings worth recording because they are not obvious.**

*The champion is not the better ranker on the test day.* The RandomForest
scores a higher test PR-AUC (0.9449) than the LightGBM that beat it on
validation (0.8468) — yet at their respective budgeted thresholds the forest
flags 17.7% of DDoS and LightGBM flags 38.0%, and on the validation day the gap
is 3.0% against 87.6%. The forest ranks well and then buries almost everything
under a `tau_sup` of 0.653, because a 300-tree vote concentrates its
probabilities near the extremes and leaves nothing to cut between. PR-AUC
measures ranking; the threshold decides what actually reaches a queue, and a
model that cannot be usefully thresholded at the budget is not the better model
however well it ranks. The promotion is decided on the validation day either
way — the test day is not a model-selection input, and comparing on it after the
fact is how a held-out set stops being held out. It is recorded here because a
reader who saw only the champion's report would reasonably wonder whether the
baseline had been beaten on the number that was actually reported.

*`web_attack` is 11 rows.* It collapses from Heartbleed on Wednesday, and under
`class_weight="balanced"` 11 rows against 821,166 benign earn a weight above
20,000 — enough to bend the whole decision surface chasing them. The support
floor holds any family under 100 rows out of the vocabulary and reports which
ones went. Those rows are still scored; being unnameable by Stage 1 is the
condition Stage 2 exists for.

### Destination-port ablation

Phase 1 defined both encodings and deferred the comparison here. Trained twice
with LightGBM on Tuesday+Wednesday, scored on Thursday, everything but the
encoding identical:

| Encoding | Features | Validation PR-AUC | tau_sup | Attack recall at tau |
| --- | --- | --- | --- | --- |
| `raw` | 70 | 0.8724 | 0.2849 | 88.2% |
| `bucketed` | 92 | **0.8816** | 0.3879 | 87.6% |

The rule was stated in advance: *if raw port produces a large gain, treat that
gain as suspect.* It produces no gain — raw is 1.0% *worse* — so nothing here
rests on memorising the lab's port assignments. Bucketed ships, and not because
of that 1%, which is noise. It ships because it asks what kind of service a flow
hit rather than which port this lab happened to use, and Phase 9 points the same
model at a network whose assignments are nothing like CICIDS2017's.

**Artifacts produced.** `backend/artifacts/supervised_model.pkl` and the
`preprocessing.pkl` it was fitted against (copied from the champion's own pair,
so the two can never disagree), `supervised_rf.pkl` + `preprocessing_rf.pkl` as
the fallback, `model_card.json` carrying `version`, `schema_hash`,
`thresholds.tau_sup` and the full training record, `metrics_supervised.json`
with 512-point PR and ROC curves for the dashboard, and
`reports/phase2_supervised.md` + `reports/port_ablation.md` committed to the
repository. The artifacts directory is gitignored; the reports are not.

**Links.** [ML-Models](ML-Models.md),
[Code-Backend-Training](Code-Backend-Training.md),
[Configuration](Configuration.md), [Testing](Testing.md)

---

## Phase 3 — Anomaly detector

**Status: complete.** Trained on the real release; the write-up is
`reports/phase3_anomaly.md` and the input ablation is
`reports/input_ablation.md`.

**Goal.** A benign-only autoencoder whose reconstruction error separates benign
from attack traffic, with a threshold set from a benign percentile.

**What the real data turned out to be.** The first run of this phase produced a
detector that ranked attack traffic *below* benign traffic: ROC-AUC 0.2337 on the
shared validation-day arena, where the three classical baselines scored 0.71 to
0.86 on the same rows, and 0.4676 on the test day. The cause was not the network,
and it is the finding of the phase.

`RobustScaler` divides each column by its interquartile range, and when a
column's IQR is **zero** scikit-learn leaves the divisor at 1.0: the column
passes through essentially unscaled. CICIDS2017 has such columns. Over three
quarters of benign flows report `idle_std` of exactly zero, so its 25th and 75th
percentiles are both zero, while the flows that do idle report values up to
7.6 × 10⁷ microseconds. Squared, that one column accounted for **93.9%** of the
total magnitude an MSE loss could see, `active_std` for another 4.0%, and the top
three for 98.7% between them.

Under those conditions MSE is not a reconstruction objective. The gradient
belongs to one column, eighty-nine features are invisible to it, and the score
that comes out is a proxy for *does this flow have a large idle gap* — which
benign traffic has more of than attack traffic does. The loss was also still
falling monotonically at epoch 60, in the tens of billions, having never
triggered early stopping.

The scaler is correct for what Phase 1 chose it for, and Stage 1 was unaffected:
a tree ensemble does not care what a column's units are. A robust scaler still
assumes the tail has a middle to be measured against, and a column that is
constant for most rows and enormous for the rest has no middle. So the fix
belongs to Stage 2, not to the bundle — changing the scaler would change the
schema hash and retrain Stage 1 for nothing.

**What gets built**

- `training/autoencoder.py` — the architecture and the scoring, in a module with
  no CLI, because `app/inference.py` has to rebuild the network from a bare state
  dict at startup. `Autoencoder.geometry` reads the layer widths back out of the
  tensors, so the architecture recorded on the model card can only ever be a
  readout of the file that shipped.
- `prepare_input` — `sign(x) * log1p(|x|)` clipped to ±6, applied to the shared
  matrix. Monotonic in `|x|`, so *further from normal is more anomalous* survives
  it. Applied in exactly two places because it is not idempotent, with
  `BenignData` holding shared-space rows so nothing can apply it twice.
- `training/train_autoencoder.py` — the benign-only fit, `tau_anom`, the
  baselines, the model-card update and the write-up.
- Both threshold rules now live in `training/metrics.py`, next to each other:
  `select_threshold` cuts from a budget, `select_anomaly_threshold` from a
  percentile.
- `pyod>=2.0` added to `backend/pyproject.toml` for ECOD. IsolationForest and LOF
  come from scikit-learn.

**Acceptance criteria**

- [x] **Autoencoder training set asserted attack-free in code, not merely
      intended.** Twice: `split.py` raises `AttackInBenignTrainingSet` when it
      assembles the split, and `assert_attack_free` re-checks the label column
      before the optimiser is constructed. Both fatal.
- [x] **`tau_anom` set from the benign validation percentile.** The 99.5th of
      reconstruction error on the Thursday validation day's benign rows —
      `0.1098`, from 396,328 rows. The budget-equivalent threshold is recorded
      beside it.
- [x] **PyOD baselines run and compared; a baseline that wins is reported rather
      than hidden.** All four on one arena, all fitted benign-only, all seeing
      the same transformed input. `_baseline_table` writes the verdict from the
      result and the tests drive both branches of it.
- [x] **Histogram of benign vs attack reconstruction error produced with the
      threshold line drawn, and the distributions visibly separate.** Rendered as
      text into the committed report, from the same bins the dashboard will draw,
      with the verdict generated from the numbers in three bands.

### Measured, on the real release

`stage2-autoencoder`: `input(92) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(92)`,
17,612 parameters, fitted on 1,191,239 benign flows with 132,359 held back to
early-stop on. 8,264 exact duplicates were dropped first. Early stopping chose
epoch 54 of a possible 60 and its weights were restored.

| Measured on the Friday test day | Stage 2 | Stage 1, for reference |
| --- | --- | --- |
| **PR-AUC** | 0.7728 | **0.8468** |
| ROC-AUC | **0.9045** | 0.8820 |
| Attack recall at its own threshold | 31.0% | 22.3% |
| FPR at that threshold | 5.96 × 10⁻² | 1.63 × 10⁻⁴ |
| Median benign reconstruction error | 5.18 × 10⁻³ | — |
| Median attack reconstruction error | 5.22 × 10⁻² | — |

**The recall figures are not comparable and the table should not be read as if
they were.** Stage 2's 31% is bought with 366 times Stage 1's false-positive
rate, because the two thresholds are cut by different rules. What is comparable
is the ranking: Stage 2 has the higher ROC-AUC and the lower PR-AUC, which is to
say it orders Friday's traffic slightly better on the prevalence-invariant
measure and slightly worse on the precision-sensitive one — **having never been
shown an attack label of any kind.**

Per family, where the average comes apart:

| Family | Rows | Stage 1 recall | Stage 2 recall |
| --- | --- | --- | --- |
| `ddos` | 128,014 | 38.0% | 53.3% |
| `botnet` | 1,948 | 0.0% | 2.2% |
| `port_scan` | 90,694 | 0.6% | 0.2% |
| benign *(false positives)* | 375,238 | 0.02% | 5.96% |

**Three findings worth recording.**

*Port scan is missed by both stages.* 0.6% and 0.2%. A family neither stage
surfaces is a gap in the system rather than in one model, and fusing two
detectors that both look past the same traffic does not produce a third that
does not. Phase 4 expected its DDoS row to look good and its port-scan row not to, and
that is what it measured: DDoS 58.6% fused, port scan 0.7%.

What makes it interesting is that Stage 2's *explanation* of port-scan traffic is
its sharpest: `init_win_bytes_forward` at 31% of the error, `psh_flag_count` at
14%, `ack_flag_count` at 13% — a recognisable SYN-scan signature. The score is a
mean over 92 features, and port-scan flows are short and sparse, so they
reconstruct easily on most columns and a large error on five of them is divided
by ninety-two. The model is responding to the right features and still ranking
the row below the line. That is a limitation of the aggregate, not of the
representation.

*The threshold costs far more than the queue can absorb.* `tau_anom` alerts on
0.50% of Thursday's benign flows by construction and on 5.96% of Friday's — 11.9
times more often, for 59,594 false alerts a day against a 320/day budget. Nothing
about the model changed between those two numbers; the benign traffic did. That
is domain shift measured across two days of one lab network, and it is Phase 9's
shadow-mode burn-in argument arriving as evidence rather than as a worry. The
brief specifies the percentile, so the percentile ships; the budget-equivalent
threshold (0.4244, the 99.968th percentile) is recorded beside it and the
dashboard's threshold slider is where an operator moves between them.

*The autoencoder earns its complexity, and LOF is closer than the other two.*

| Detector | PR-AUC | ROC-AUC |
| --- | --- | --- |
| **Autoencoder** | **0.6232** | **0.9670** |
| LOF | 0.3545 | 0.9323 |
| IsolationForest | 0.0954 | 0.7342 |
| ECOD | 0.0803 | 0.7136 |

One shared 42,179-row arena from the validation day — 2,179 attack flows and
40,000 benign — because choosing between detectors is a choice and choices are
not made on the test day. Every detector is fitted benign-only and sees the same
transformed input; handing the baselines the raw scaled matrix would flatter the
autoencoder for free, since LOF is Euclidean and IsolationForest partitions axis
by axis and both are pulled apart by the same column that broke the network. The
classical detectors are fitted on 40,000 reference rows rather than all 1.19M
because LOF is a k-nearest-neighbour method and scoring against a million
reference rows does not finish.

### The input transform ablation

Chosen on the validation day, three seeds per candidate, twelve epochs each
(`reports/input_ablation.md`):

| Clip (log units) | Benign val loss | Validation ROC-AUC |
| --- | --- | --- |
| none | 0.02547 | 0.7848 ± 0.0830 |
| ±4 | 0.01509 | 0.7061 ± 0.0678 |
| **±6** | 0.01932 | **0.8996 ± 0.0167** |
| ±8 | 0.02187 | 0.8748 ± 0.0464 |
| ±12 | 0.02577 | 0.8600 ± 0.0246 |

Three seeds rather than one because the spread *within* a bound is wider than the
gaps between the bounds' means — the unclipped candidate ranges from 0.6731 to
0.8720 across its three runs, a spread of 0.20. What makes ±6 a result rather
than a draw is the strong form: its worst run (0.8805) still beats every other
candidate's mean. PR-AUC puts the candidates in the same order, so the choice does
not rest on which metric is quoted.

**Artifacts produced.** `backend/artifacts/autoencoder.pt` (a bare state dict,
loaded with `weights_only=True`), `training_autoencoder.json`,
`metrics_anomaly.json`, and `tau_anom` plus the benign error histogram added to
`model_card.json`. Reports: `reports/phase3_anomaly.md` and
`reports/input_ablation.md`.

**Links.** [ML-Models](ML-Models.md),
[Code-Backend-Training](Code-Backend-Training.md),
[Frontend-Screens](Frontend-Screens.md)

---

## Phase 4 — Fusion and the headline evaluation

**Status: done.** The cascade is `backend/training/fusion.py`, imported by both
`ModelBundle.score_batch` and `backend/training/loao.py` so the hold-out table
measures the rule that ships rather than a copy of it. The table is committed as
`reports/loao.md` and the full record as `backend/artifacts/metrics_loao.json`.
Run with `make loao`.

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

- [x] LOAO table complete for every attack family, including a **Missed**
      column.
- [x] The autoencoder is confirmed unchanged across LOAO runs —
      `test_stage2_scores_the_arena_through_the_unchanged_autoencoder`
      recomputes its scores from the artifact the API loads and compares them
      against the array every fold was scored with.
- [x] `UNCLASSIFIED_ANOMALY` survives the pipeline as a distinct kind, never
      collapsed into a family label — `fuse` sets `family=None` for it, which
      mirrors the `family_matches_kind` constraint on the alerts table.

### The leave-one-attack-out table, measured

| Held-out family | Rows | Caught by Stage 1 | Caught by Stage 2 | Total recall | Missed |
| --- | --- | --- | --- | --- | --- |
| `dos` | 193,745 | 0.0% | **75.5%** | 75.5% | 24.5% |
| `ddos` | 128,014 | 38.0% | **20.7%** | 58.6% | 41.4% |
| `brute_force` | 9,150 | 0.0% | **0.2%** | 0.2% | 99.8% |
| `port_scan` | 90,694 | 0.6% | **0.1%** | 0.7% | 99.3% |
| `web_attack` | 2,154 | 88.6% | **4.6%** | 93.2% | 6.8% |
| `botnet` | 1,948 | 0.0% | **2.2%** | 2.2% | 97.8% |
| `infiltration` | 36 | 0.0% | **44.4%** | 44.4% | 55.6% |

Three LightGBM fits produced this: the control plus the two families the
training days actually carry. The temporal split had already held the other
five out, and each fold records how many rows its removal took out of the fit
so that difference is visible rather than asserted.

**`dos` is the row that carries the claim.** Stage 1 refitted with all 193,745
DoS rows removed — vocabulary `benign, brute_force`, `tau_sup` re-cut from
0.3879 to 0.0515 to hold the same false-positive budget — named none of them.
The benign-only autoencoder surfaced 75.5%, and 24.5% got through.

**`brute_force` is the same experiment with the opposite answer**, and the more
instructive row: 100% caught with it in training, 0.2% without. The 100% is
in-sample and the report labels it so — `brute_force` lives only on the
training days, so the control is scored on rows it was itself fitted on and
no out-of-sample with-it-in-training figure exists to quote instead. The
conclusion survives that; the size of the contrast does not. What makes
brute force obvious is the repetition, and a per-flow feature vector cannot see
repetition.

Three qualifications, all of them in the report:

- **Caught is not named.** `attack_confidence` is the largest single
  attack-class probability, so a held-out family can clear `tau_sup` under
  another family's label. That is the whole `web_attack` row: Thursday's HTTP
  brute force resembles Tuesday's FTP and SSH brute force, so Stage 1 flags
  88.6% of it and names 0% of it `web_attack`. `ddos` at 38% is the same
  mechanism, alerting as `dos`.
- **Stage 2's column is marginal**, not standalone: 20.7% on DDoS against
  53.3% alone, because both stages respond to the same extreme flows.
- **Neither threshold is a finished answer.** At `tau_anom` Stage 2 flags 5.96%
  of the test day's benign flows, which is roughly 7,450 alerts per analyst per hour
  against a budget of 40. At the budget-equivalent threshold that falls 10.6×
  — to 705 per analyst per hour, still **17.6× over budget** — while DoS
  recall falls from 75.5% to 4.6% and four families reach 0.0%. `budget_tau`
  was cut on the validation day's benign distribution, so it fits the budget
  there by construction and does not survive one day forward. What moves this
  is dedup (Phase 5), risk ranking and recalibration against a local baseline
  (Phase 9) rather than a threshold choice.

**Artifacts produced.** `reports/loao.md` — the write-up, with per-fold
thresholds, the false-positive cost of each row, the control comparison, Stage
2 measured alone at both thresholds, Wilson intervals for the small families,
and a Method section stating what the evaluation does not prove.
`backend/artifacts/metrics_loao.json` — the machine-readable record
`GET /api/v1/metrics/model` will serve, written with `allow_nan=False`. A
compact `loao` block is added to `model_card.json` for the dashboard panel.

**Links.** [ML-Models](ML-Models.md), [Architecture](Architecture.md),
[Code-Backend-Training](Code-Backend-Training.md)

---

## Phase 5 — Backend API

**Status: complete.** Thirteen of the sixteen v1 endpoints answer with real
data; the three that do not belong to Phase 7 (`/metrics/drift`, `/models`) and
Phase 9 (`/ingest/start`). `app/explain.py`, `app/mitre.py`,
`app/remediation.py` and `app/replay.py` are implemented, and the phase added
`app/pipeline.py`, `app/events.py`, `app/risk.py`, `app/topology.py` and
`app/metrics_store.py`. Measured checkpoint: `reports/phase5_api.md`.

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

- [x] Batch scoring; models loaded once at startup. Explanation is batched too
      — one explainer call per stage per batch, and an empty stage group is
      skipped before the matrix exists.
- [x] Schema-hash check fails fast on mismatch. *(Already true from Phase 0.)*
- [x] Explanation attached to every alert. TreeSHAP against the **named
      family's** column for Stage 1, per-feature reconstruction error for
      Stage 2, plus a narrated sentence from a static phrase map.
- [x] Dedup verified under burst load. Measured at 10x: 93 alert events over 4
      distinct queue rows, one burst collapsing 85 flows into one row.
- [x] SSE stream stable through a replay. Verified at 10x over `curl -N`.
      *(100x is available and paced by the same arithmetic; the committed
      measurement is 10x, which is what the checkpoint asked for.)*
- [x] Zero auto-block code paths. *(Already asserted from Phase 0.)*
- [x] A 10x replay produces alerts visible over
      `curl -N localhost:8000/api/v1/stream`, with dedup visibly collapsing
      bursts. See `reports/phase5_api.md` for the pasted output, and
      `scripts/phase5_checkpoint.py` to re-run it.

**Artifacts produced.** Rows in `alerts`, `reports/phase5_api.md`, and an
OpenAPI schema whose operations return real payloads instead of `501`.

**Decisions this phase had to make, because nothing specified them**

- **`risk_score` and `severity`** are defined in `app/risk.py`. Both stages
  contribute *relative to their own operating threshold* — how far past the bar,
  as a fraction of the headroom. A raw percentile would have pinned every Stage 2
  alert into `[0.995, 1.0]`, since `tau_anom` sits at the 99.5th percentile of
  benign error, and destroyed the ordering the column exists for. The residual
  cost is disclosed in the module: percentile space has its own ceiling, so very
  different degrees of "extremely anomalous" still compress near 1.
- **Derived addresses.** The MachineLearningCSV release ships no addresses, but
  `alerts.src_ip` is `NOT NULL` and dedupe keys on the source host. Replay rows
  get addresses derived from the published CICIDS2017 lab topology for their
  family, spread across the documented host set so a family does not collapse
  into one dedupe bucket. Every alert carries the breakdown in
  `raw_flow._provenance`: `dst_port` observed, addresses derived, `src_port` and
  `protocol` absent, `detected_at` replay wall-clock.
- **Synthesised replay time.** With no `Timestamp` column there are no
  inter-arrival gaps to accelerate, so one nominal rate stands in and `speed`
  multiplies it. Speed moves the gap between batches, never the batch size.
- **`/metrics/threshold`'s `t` is Stage 2's threshold**, because a Stage 1
  answer is not computable at an arbitrary `t` from what is persisted.

**Links.** [API-Reference](API-Reference.md),
[Code-Backend-Routes](Code-Backend-Routes.md),
[Code-Backend-Pipeline](Code-Backend-Pipeline.md),
[Database-Schema](Database-Schema.md)

---

## Phase 6 — Frontend

**Status: done.** All seven screens are built against the live API, with
TanStack Query for every server call and types generated from the OpenAPI
schema. Phase 8 restyled them in the reference console's visual language
without changing what they claim: every figure on them still traces to the API.
`scripts/phase6_checkpoint.py` walks the checkpoint against a running stack, and
`frontend/src/pages/screens.test.tsx` pins each criterion below.

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

- [x] Triage queue is the landing page, sorted by risk.
- [x] `UNCLASSIFIED_ANOMALY` visually distinct and filterable in one click.
- [x] Draggable threshold updating projected alert volume live.
- [x] PR and ROC rendered side by side with an explanatory caption.
- [x] LOAO panel present and prominent.
- [x] Verdict submission invalidates and refreshes the queue.
- [x] No accuracy hero tile anywhere.
- [x] Alert Detail answers why, what-it-is and how-to-fix for every alert,
      including the honest no-playbook case for unclassified anomalies.
- [x] Analytics shows trends by family, top hosts and sources, SOC throughput,
      and MITRE coverage.
- [x] Alert table virtualised; skeleton, empty and error states on every
      screen.

**Artifacts produced.** The seven screens, and a regenerated
`frontend/src/types/api.d.ts` matching the Phase 5 schema.

**Links.** [Frontend-Screens](Frontend-Screens.md),
[Code-Frontend](Code-Frontend.md), [API-Reference](API-Reference.md)

---

## Phase 7 — Drift and active learning

**Status: done.** `training/drift_job.py` scores PSI per feature over a
systematic sample of scored traffic against quantile bins cut from the training
split (`training/drift_reference.py`) and stores a snapshot per run.
`training/retrain.py` fits a challenger from analyst verdicts and a control
without them, gates promotion on validation-day PR-AUC, logs the comparison in
`retrain_runs` and `reports/phase7_retrain.md`, and then runs the guarded Stage 2
refit (`training/refit_autoencoder.py`). Every alert records both stages'
versions (`stage1-…+stage2-…`), and `GET /models` lists each version with the
alerts it scored. Measured in the verification run: the control reproduced the
champion exactly (validation PR-AUC 0.8816 both), seven labels lifted the
challenger to 0.8865 and it was promoted, and the benign refit pool was refused
by its per-host cap — the expected outcome on a replay, where every unclassified
anomaly is attributed to one derived host.

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

- [x] PSI snapshots stored per feature over time.
- [x] Benign refit pool requires analyst FP confirmation and caps per-host
      contribution.
- [x] Champion and challenger evaluated on the same held-out set; promotion
      only on improvement, comparison logged.
- [x] Every alert records the model version that produced it.

**Artifacts produced.** PSI snapshots, challenger model versions, and rows in
`model_versions` with `stage` in `champion`, `challenger` or `archived`. The
columns for all of this — `Alert.model_version`,
`AnalystVerdict.consumed_at`, the whole `ModelVersion` table — already exist
from Phase 0.

**Links.** [Code-Backend-Pipeline](Code-Backend-Pipeline.md),
[Database-Schema](Database-Schema.md), [Frontend-Screens](Frontend-Screens.md)

---

## Phase 8 — Packaging

**Status: done.**

**Goal.** A clean clone that demos in one command, with the documentation that
makes the results readable.

**What was built**

- **A committed model release.** `backend/release/` holds the LightGBM champion,
  the benign-only autoencoder, the evaluation files the API serves, the drift
  reference and a `MANIFEST.json` of sha256 digests and the library versions
  that wrote the pickles (8.2MB). `app/release.py` installs it into the
  artifacts directory only when nothing is serving there or when what is
  serving is an earlier release exactly as installed; a locally trained model is
  never replaced without `--force`. `make models` runs it, and `make dev` does
  too. The release ships the Phase 2 champion rather than the Phase 7
  verification's challenger, whose labels came from the test day.
- **`docker compose up` with models pre-loaded.** The backend entrypoint
  migrates, installs the release, seeds an empty database and serves; the
  dashboard is built and served by nginx with `/api` and the SSE stream proxied.
  State lives in named volumes, so the container leaves no root-owned files in
  the checkout. The clean-clone run found and fixed one real bug: LightGBM needs
  the OpenMP runtime, which the slim image lacked — invisible until a model is
  actually served.
- **`make seed`.** Replays `backend/release/demo_flows.parquet` — 28,869
  unmodified rows of the two held-out days, cited — through
  `bundle.score_batch` and `ingest_batch`, spread over the previous 24 hours,
  then runs the drift job. It writes no verdicts and refuses to mix into a
  database that already holds data. From empty: 85 queue rows from 4,517
  alerting flows and a drift snapshot, in about 12 seconds.
- **Ground truth per alert bucket.** `alerts.ground_truth_counts` tallies every
  dataset label a dedupe bucket absorbs, because the first flow's label alone
  hid Stage 2's real catches behind its false positives. In the seeded demo one
  unclassified anomaly holds 15 flows, 13 of them infiltration.
- **Tests.** The suite runs against a throwaway database built by the Alembic
  history and the release installed into a throwaway artifacts directory.
  `test_parity.py` (the feature-parity test: the serving matrix equals the
  training matrix bit for bit on real flows, and `POST /score` returns the
  offline scores of both stages), the schema-hash tests in `test_api_surface.py`
  and `test_features.py`, the dedup burst tests in `test_pipeline_alerts.py`,
  and the API contract: `test_api_contract.py` pins the served OpenAPI schema to
  a committed snapshot, and `frontend/src/types/contract.test.ts` requires the
  generated types to be exactly what that snapshot produces. `test_release.py`
  and `test_seed.py` cover the release and the seed.
- **The README**, rewritten around the shipped models: architecture, the LOAO
  table, PR versus ROC, the false-positive budget arithmetic, the
  alert-not-block justification, limitations, reproducibility and next steps.

**Acceptance criteria.** Every line of the
[acceptance checklist](#acceptance-checklist) below.

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
where the property is true in the repository today, and each names what makes it
true.

### Data

- [x] Temporal split; zero duplicate rows shared across splits *(Phase 1)*
- [x] IP, port and timestamp leakage columns dropped and logged *(Phase 1)*
- [x] Destination-port ablation run and reported *(Phase 2 —
      `reports/port_ablation.md`)*
- [x] Scaler, feature order and schema hash persisted together *(Phase 1)*

### Models

- [x] RandomForest baseline trained, evaluated and committed before attempting
      the LightGBM upgrade *(Phase 2 — and kept afterwards as
      `supervised_rf.pkl`, so a regression is a file swap)*
- [x] Autoencoder training set asserted attack-free in code *(Phase 3 — twice:
      the split-time assertion in `split.py` and the re-check in
      `assert_attack_free` before the optimiser is constructed, both fatal)*
- [x] `tau_sup` derived from a stated false-positive budget, not 0.5 *(Phase 2 —
      0.387908, at an FPR of 3.18e-4 against a 3.20e-4 target)*
- [x] `tau_anom` set from a benign validation percentile *(Phase 3 — 0.1032 in
      the release, the 99.5th of reconstruction error on the Thursday validation
      day's benign rows, with the budget-equivalent threshold recorded beside it)*
- [x] PyOD baselines run and compared *(Phase 3 — IsolationForest, LOF and ECOD
      on one shared arena. On the release's training run LOF edges the
      autoencoder, 0.3545 to 0.3083 PR-AUC, and the report says so; an earlier
      run of the same code put the autoencoder at 0.6232, which is why the
      README calls the ranking unsettled)*
- [x] LOAO table complete, including a **Missed** column

### Backend

- [x] Batch scoring; models loaded once at startup *(`app/main.py` loads the
      bundle once in the lifespan; the replay scores fixed 500-row batches and
      `POST /score` takes a list)*
- [x] Schema-hash check fails fast on mismatch
      *(`test_startup_refuses_a_bundle_with_an_inconsistent_schema_hash`)*
- [x] Explanation attached to every alert *(`ingest_batch` raises rather than
      store an alert without one; `test_known_and_anomaly_alerts_are_fully_formed…`,
      and `test_seed.py` checks every seeded alert)*
- [x] Dedup verified under burst load
      *(`test_a_burst_in_one_window_collapses_to_one_row_with_two_published_events`;
      a 10x replay collapsed 93 events into 4 queue rows, `reports/phase5_api.md`)*
- [x] SSE stream stable through a 100x replay *(a full 100x replay of the
      595,894-row test day, streamed to one held-open connection: all 96,370
      alert events arrived, matching the server's own count, the longest silence
      between two was 1.7 s, and the connection was still open when the replay
      ended. Dedup folded those 96,370 alerting flows into 6 queue rows)*
- [x] Zero auto-block code paths

### Frontend

- [x] Triage queue is the landing page, sorted by risk *("renders the queue at /,
      not an overview dashboard", "lists rows in the order the server returned,
      highest risk first")*
- [x] `UNCLASSIFIED_ANOMALY` visually distinct and filterable in one click
      *("marks the anomaly row differently from a named family", "filters to
      anomalies in a single click")*
- [x] Draggable threshold updating projected alert volume live *("exposes the
      threshold as a real slider", "moves on the keyboard and asks the server for
      the new projection")*
- [x] PR and ROC rendered side by side with an explanatory caption *("draws PR
      and ROC with the caption that explains the gap")*
- [x] LOAO panel present and prominent *("gives the LOAO table a Missed column
      with real misses in it")*
- [x] Verdict submission invalidates and refreshes the queue *("posts the
      verdict and refetches the alert queries")*
- [x] No accuracy hero tile anywhere *("renders no accuracy figure anywhere")*
- [x] Alert Detail answers why, what-it-is and how-to-fix for every alert,
      including the honest no-playbook case for unclassified anomalies *("asks
      the three questions in that fixed order", "refuses to invent a technique
      or a playbook for an anomaly")*
- [x] Analytics screen shows trends by family, top hosts and sources, SOC
      throughput, and MITRE coverage *("shows trends, families, ranked hosts,
      throughput and MITRE coverage")*

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

- [x] README carries LOAO results, FP arithmetic, alert-not-block
      justification, limitations
- [x] `docker compose up` works from a clean clone *(run on a fresh clone of the
      branch: images built, migrations from empty, release installed, demo
      seeded, both models loaded, the dashboard served by nginx and a 100x
      replay streamed through its proxy. The sandbox that ran it re-terminates
      TLS, so its builds were given that proxy's CA through an uncommitted
      override; nothing committed depends on it)*

Every box outside Phase 9's is ticked, each naming the test, report or run that
makes it true; the frontend lines quote test names from
`frontend/src/pages/screens.test.tsx`. Phase 9's four stay open until the live
capture path exists.
