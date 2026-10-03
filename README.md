<img src="docs/assets/img/logo-192.png" alt="" width="96" align="right">

# Recluse

Machine-learning network intrusion detection with a SOC triage dashboard.

Two models, trained here, on labelled flow data:

| Model       | What it is                                   | Trained on                      | Answers                                | Artifact                | State |
| ----------- | -------------------------------------------- | ------------------------------- | -------------------------------------- | ----------------------- | ----- |
| **Stage 1** | scikit-learn `RandomForestClassifier` → LightGBM | Labelled flows: benign + known attack families | "Which named attack is this?"          | `supervised_model.pkl`  | trained |
| **Stage 2** | PyTorch autoencoder                          | **Benign traffic only**, no attack labels | "How unlike normal traffic is this?"   | `autoencoder.pt`        | trained |

Stage 1 names what it knows. Stage 2 catches what nobody named. The claim the
project has to defend is that it detects attack traffic it was never trained
on, and the leave-one-attack-out evaluation in Phase 4 is what makes that
measurable rather than asserted.

**The system alerts, ranks and explains. It never blocks traffic.**

---

## Status

Phases 0 to 4 of 9 are complete. **Both models are trained, fused, and measured
against attack families held out of training.** The leave-one-attack-out table
is in [`reports/loao.md`](reports/loao.md) and summarised under
[Results](#fusion-and-leave-one-attack-out-phase-4-measured); its headline is
that Stage 1, refitted with every DoS row removed, named none of them, and the
benign-only autoencoder surfaced 75.5% of the family anyway.

| Phase | Scope                      | State       |
| ----- | -------------------------- | ----------- |
| 0     | Scaffolding                | done        |
| 1     | Data + features            | done        |
| 2     | Supervised classifier      | done        |
| 3     | Anomaly detector           | done        |
| 4     | Fusion + LOAO evaluation   | **done**    |
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

Phases 2 and 3 produce `supervised_model.pkl` and `autoencoder.pt` from real
training runs on the 2.83M-row CICIDS2017 release, and Phase 4 refits Stage 1
once per held-out family on top of them. The artifacts are gitignored — they
are reproducible output, not source — so a clean clone still reports
`model_version: "unloaded"` until
`make data && make train && make train-lgbm && make train-anomaly && make loao`
has been run. The measured results are below, in
[`reports/phase2_supervised.md`](reports/phase2_supervised.md),
[`reports/phase3_anomaly.md`](reports/phase3_anomaly.md) and
[`reports/loao.md`](reports/loao.md).

[Roadmap](docs/Roadmap.md) covers all nine phases, including the ones not yet
started.

---

## Documentation

The full documentation is a website, published from [`docs/`](docs/) to
**<https://team-arachnid.github.io/recluse/>**.

| If you want to…                              | Read                                                             |
| -------------------------------------------- | ---------------------------------------------------------------- |
| Get the stack running                        | [Getting Started](docs/Getting-Started.md)                       |
| Understand the two-stage design              | [Architecture](docs/Architecture.md)                             |
| Know what every source file does             | [Repository Layout](docs/Repository-Layout.md) → the `Code-*` pages |
| Work on the data phase                       | [Data Pipeline](docs/Data-Pipeline.md)                           |
| Understand the models and how they are judged | [Models and Evaluation](docs/ML-Models.md)                      |
| Call the API                                 | [API Reference](docs/API-Reference.md)                           |
| Know what to build next                      | [Roadmap](docs/Roadmap.md)                                       |

Pages are authored in this repository so a documentation change is reviewed in
the same pull request as the code change that caused it. Pushing to `main`
rebuilds and republishes the site via `.github/workflows/jekyll-gh-pages.yml`.
`make docs-serve` previews it locally at <http://localhost:4000/recluse/> if you
have Ruby; nothing but a text editor is needed to write a page. See
[Docs Publishing](docs/Docs-Publishing.md).

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
├── docker-compose.yml         backend + frontend (+ optional postgres profile)
├── Makefile / make.ps1        task runner, and its Windows equivalent
├── .env.example               every port, path and threshold input
├── data/                      raw / interim / processed — gitignored
├── reports/                   loao.md and friends (Phase 4)
├── docs/                      the documentation site, published to GitHub Pages
├── scripts/dev.py             runs both processes for `make dev`
├── .github/workflows/         jekyll-gh-pages.yml — builds docs/ and deploys it
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
at startup. The shipped model's threshold is **`tau_sup = 0.3879`**, measured:
it is the smallest validation-day threshold that keeps false alerts to 126 out
of 396,328 benign rows. A default of 0.5 would have been an arbitrary number
that happens to sit nearby; this one is derived, persisted inside the model
artifact, and re-derived from the same budget every time a model is retrained.

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

### Stage 1 (Phase 2, measured)

Champion `stage1-lgbm`: LightGBM, 92 features under the bucketed port encoding,
early-stopped at iteration 227 against the Thursday validation day. The
RandomForest baseline it replaced (300 trees, `max_depth=48`, validation PR-AUC
0.7010) is kept as a fallback artifact with its own matching preprocessing
bundle, written up in
[`reports/phase2_supervised_rf.md`](reports/phase2_supervised_rf.md).

| | Validation (Thu) | Test (Fri) |
| --- | --- | --- |
| Benign share of the split | 99.5% | 63.0% |
| **PR-AUC** (headline) | **0.8816** | **0.8468** |
| ROC-AUC | 0.9965 | 0.8820 |
| FPR at `tau_sup` | 3.18 × 10⁻⁴ | 1.63 × 10⁻⁴ |
| Projected false alerts/day at V = 1,000,000 | 318 | 163 |
| **Alerts per analyst per hour** | **39.7** | **20.3** |
| Accuracy *(table cell only, never a headline)* | — | 0.630 |

`tau_sup = 0.3879`, chosen as the smallest threshold whose validation-day FPR
fits the 320-alerts/day budget: 126 false alerts out of 396,328 benign rows.

**Look at the validation row, then the test row.** ROC-AUC reads 0.9965 where
attacks are 0.55% of the traffic and 0.8820 where they are 37% of it — the
same model. PR-AUC barely moves (0.8816 → 0.8468). That gap is why ROC-AUC is
not the headline, and it is the single most useful thing this table shows.

### What Stage 1 can and cannot do

The temporal split gives Stage 1 a vocabulary of `benign`, `dos` and
`brute_force` — those are the only families Tuesday and Wednesday carry.
CICIDS2017 runs each family on one day, so **every attack on the Friday test day
is a family Stage 1 has never seen**:

| Family on the test day | Rows | Flagged by Stage 1 | Recall |
| --- | --- | --- | --- |
| `ddos` | 128,014 | 48,600 | **38.0%** |
| `port_scan` | 90,694 | 501 | **0.6%** |
| `botnet` | 1,948 | 0 | **0.0%** |

DDoS partially generalises from Wednesday's DoS traffic. Port scan and botnet
do not resemble anything in the training days and are missed almost entirely.
Across the whole day Stage 1 surfaces 22.3% of the attack traffic, so
**77.7% of it produces no Stage 1 alert — that is the measured size of the gap
Stage 2 exists to close**, and Phase 4's leave-one-attack-out table is where it
gets closed or does not.

A `web_attack` class exists in the label map but is held out of training: it
collapses to 11 Heartbleed rows on Wednesday, and under `class_weight="balanced"`
11 rows against 821,166 earn a weight in the thousands. The support floor and
what it excluded are reported rather than quietly applied.

### Destination-port ablation

Trained twice, everything but the encoding identical
([`reports/port_ablation.md`](reports/port_ablation.md)):

| Encoding | Features | Validation PR-AUC |
| --- | --- | --- |
| raw port | 70 | 0.8724 |
| bucketed (IANA service group + top-20 one-hot) | 92 | **0.8816** |

The raw port gives no gain, so nothing here rests on memorising the lab's port
assignments. Bucketed ships — not for the 1%, which is noise, but because it
asks what kind of service a flow hit rather than which port this particular lab
used, and Phase 9 points the same model at a network whose assignments are
nothing like CICIDS2017's.

### Stage 2 (Phase 3, measured)

A PyTorch autoencoder, `input(92) → 64 → 32 → 16 → 32 → 64 → output(92)`, 17,612
parameters, fitted on **1,191,239 benign flows and nothing else** — Monday in
full plus the benign rows of Tuesday and Wednesday. Early stopping on held-out
benign loss chose epoch 54 of a possible 60. Written up in
[`reports/phase3_anomaly.md`](reports/phase3_anomaly.md).

`tau_anom = 0.1098`, the 99.5th percentile of reconstruction error on the
Thursday validation day's benign rows. Benign-only, on a day the network never
trained on.

| Measured on the Friday test day | Stage 2 | Stage 1, for comparison |
| --- | --- | --- |
| **PR-AUC** | 0.7728 | **0.8468** |
| ROC-AUC | **0.9045** | 0.8820 |
| Attack recall at its own threshold | 31.0% | 22.3% |
| False-positive rate at that threshold | 5.96 × 10⁻² | 1.63 × 10⁻⁴ |
| Median benign / attack reconstruction error | 5.18 × 10⁻³ / 5.22 × 10⁻² | — |

Read that table carefully, because two of its rows are not a fair fight. **The
recall figures are not comparable**: Stage 2's 31% is bought with 366 times
Stage 1's false-positive rate, because the two thresholds are cut by different
rules — a benign percentile against an analyst budget. Any detector can buy
recall with false positives, and a comparison that quotes one without the other
is the thing this project exists not to do.

What *is* comparable is the ranking. Stage 2 has the higher ROC-AUC (0.9045
against 0.8820) and the lower PR-AUC (0.7728 against 0.8468) — it orders Friday's
traffic slightly better on the prevalence-invariant measure and slightly worse on
the precision-sensitive one, **having never been shown an attack label of any
kind**. A model that was given no labels ranking within a few points of one that
was trained on three classes is the two-stage thesis appearing as a measurement
rather than an argument. It is not yet the claim itself: that needs the two
stages fused and measured per held-out family, which is [below](#fusion-and-leave-one-attack-out-phase-4-measured).

Per family, and this is where the average comes apart:

| Family on the test day | Rows | Stage 1 recall | Stage 2 recall |
| --- | --- | --- | --- |
| `ddos` | 128,014 | 38.0% | **53.3%** |
| `botnet` | 1,948 | 0.0% | **2.2%** |
| `port_scan` | 90,694 | 0.6% | **0.2%** |
| benign *(false positives)* | 375,238 | 0.02% | 5.96% |

DDoS is what carries the 31%, and again at 366 times the false-positive cost.
**Port scan is missed by both stages**, and that is the number to carry forward
rather than the average: a family neither stage surfaces is a gap in the system,
not in one model, and fusing two detectors that both look past the same traffic
does not produce a third that does not.

The mechanism is worth naming, because Stage 2's own explanation of port-scan
traffic is its *sharpest* — `init_win_bytes_forward` (31% of the error),
`psh_flag_count` (14%), `ack_flag_count` (13%), which is a recognisable SYN-scan
signature. The score is a *mean* over 92 features, and port-scan flows are short
and sparse: they reconstruct easily on most columns, so a large error on five of
them is divided by ninety-two. Stage 2 is responding to the right features and
still ranking the row below the line. That is a limitation of the aggregate, not
of the representation. Phase 4 confirmed it rather than fixing it: port scan is 0.7% fused, the weakest row in the hold-out table bar brute force.

**The threshold costs more than the queue can absorb.** `tau_anom` alerts on
0.50% of Thursday's benign flows by construction, and on 5.96% of Friday's — 11.9
times more often, for 59,594 false alerts a day against a 320/day budget. Nothing
about the model changed between those two numbers; the benign traffic did. That
is domain shift measured across two days of one lab network, and it is the
argument for Phase 9's shadow-mode burn-in stated as evidence rather than as a
worry. The percentile is what the brief specifies and what ships; the
budget-equivalent threshold (0.4244, the 99.968th percentile) is recorded beside
it.

### The autoencoder earns its complexity

All four detectors fitted on benign rows only, scored on one shared
42,179-row arena from the validation day — because choosing between detectors is
a choice, and choices are not made on the test day:

| Detector | PR-AUC | ROC-AUC |
| --- | --- | --- |
| **Autoencoder** | **0.6232** | **0.9670** |
| LOF | 0.3545 | 0.9323 |
| IsolationForest | 0.0954 | 0.7342 |
| ECOD (PyOD) | 0.0803 | 0.7136 |

Every one of them sees the same input the autoencoder does, including the
Stage 2 input transform. Handing the baselines the raw scaled matrix would
flatter the autoencoder for free.

### The bug worth reporting

The first Phase 3 run produced a detector that ranked attack traffic *below*
benign traffic — ROC-AUC 0.2337 on the shared validation-day arena, where the
three classical baselines scored 0.71 to 0.86 on the same rows, and 0.4676 on
the test day. The cause was not the network.

`RobustScaler` divides by the interquartile range, and when a column's IQR is
zero scikit-learn leaves the divisor at 1.0, so the column passes through
unscaled. Over three quarters of benign flows report `idle_std` of exactly zero;
the ones that do idle report values up to 7.6 × 10⁷. Squared, that **one column
owned 93.9% of the magnitude the MSE loss could see**, and the top three owned
98.7%. The gradient belonged to one feature, eighty-nine were invisible, and the
resulting score was a proxy for *does this flow have a large idle gap* — which
benign traffic has more of than attack traffic does.

Stage 2 now reads the shared matrix through `sign(x) · log1p(|x|)` clipped to ±6,
which compresses the magnitudes while preserving the ordering. The bound is
chosen on the validation day over three seeds in
[`reports/input_ablation.md`](reports/input_ablation.md) — three rather than one
because the spread *within* a single bound reaches 0.20 ROC-AUC, wider than the
gaps between the bounds' means. What makes ±6 a result rather than a draw is that
its worst of three runs still beats every other candidate's average. This is a
Stage 2 decision rather than a change to the bundle — a tree ensemble does not
care what a column's units are, so changing the scaler would retrain Stage 1 for
nothing.

### Fusion and leave-one-attack-out (Phase 4, measured)

The two stages are now one decision. `training/fusion.py` holds the rule —
Stage 1 names what clears `tau_sup`, everything else falls through to Stage 2,
and a row Stage 2 flags becomes an `UNCLASSIFIED_ANOMALY` with no family — and
both `app/inference.py` and the hold-out evaluation import it, so the headline
number below describes the rule the dashboard will run rather than a second
copy written for the measurement.

This is the table the project's claim rests on. Each family is removed from
Stage 1's training set, Stage 1 is refitted, Stage 2 is left untouched because
it never saw an attack label of any kind, and the fused pipeline is then run
over every row of that family in the capture. Full write-up, per-fold
thresholds and method caveats in [`reports/loao.md`](reports/loao.md).

| Held-out family | Rows | Caught by Stage 1 | Caught by Stage 2 | Total recall | Missed |
| --- | --- | --- | --- | --- | --- |
| `dos` | 193,745 | 0.0% | **75.5%** | 75.5% | 24.5% |
| `ddos` | 128,014 | 38.0% | **20.7%** | 58.6% | 41.4% |
| `brute_force` | 9,150 | 0.0% | **0.2%** | 0.2% | 99.8% |
| `port_scan` | 90,694 | 0.6% | **0.1%** | 0.7% | 99.3% |
| `web_attack` | 2,154 | 88.6% | **4.6%** | 93.2% | 6.8% |
| `botnet` | 1,948 | 0.0% | **2.2%** | 2.2% | 97.8% |
| `infiltration` | 36 | 0.0% | **44.4%** | 44.4% | 55.6% |

**The row that carries the claim is `dos`.** Stage 1 was refitted with all
193,745 DoS rows removed; its vocabulary became `benign, brute_force`, its
threshold dropped from 0.3879 to 0.0515 to keep the same false-positive
budget, and it then named **none** of them. The autoencoder — which has never
been shown an attack label in its life — surfaced 75.5%. A quarter of the
family still got through. That is a measured claim about catching what a
signature set would miss, and both halves of it are the result.

`brute_force` is the same experiment with the opposite answer, and it is the
more useful row for understanding the system. With brute force in training
Stage 1 catches 100% of it — *in-sample*, and the report labels it so: the
family lives only on the training days, so the control is scored on rows it
was itself fitted on, and no out-of-sample with-it-in-training figure exists
to quote instead. With it removed, Stage 1 catches 0% and Stage 2 catches
0.2%. Nine thousand failed-login flows, essentially invisible. The
pattern that makes brute force obvious to a human is the *repetition* — the
same short session, hundreds of times — and nothing in a per-flow feature
vector can see that.

**Three things the five columns hide, which `reports/loao.md` reports and
which matter more than the averages.**

*Caught is not named.* Stage 1's score is the largest single attack-class
probability, so a held-out family can clear `tau_sup` under a *different*
family's label. That is what the `web_attack` row is: Thursday's HTTP brute
force looks like Tuesday's FTP and SSH brute force, so Stage 1 flags 88.6% of
it — correctly, as an attack worth an analyst's time — and names 0% of it
`web_attack`, because the model has no such column. Read the Stage 1 column as
*an alert was raised*, never as classification. The same mechanism is why
`ddos` scores 38% on a classifier that has never seen DDoS: those flows alert
as `dos`, which is a correct alert about a flood with the family one level off.

*Stage 2's column is marginal, not standalone.* It reports what Stage 2 adds
on rows Stage 1 passed through. Stage 2's own recall on DDoS is 53.3%, but it
only adds 20.7% in the cascade, because both stages respond to the same
extreme flows and largely agree about which ones. `reports/loao.md` reports
both figures side by side for that reason.

*The alert volume at `tau_anom` does not fit a queue.* Stage 2's shipped
threshold is the brief's 99.5th percentile of benign reconstruction error,
which on the Friday test day means 5.96% of ordinary flows — roughly 7,450
alerts per analyst per hour against a budget of 40. That is not a defect
hiding in the table; it is the gap Phase 3 already measured between a
percentile (a statement about normal traffic) and a budget (a statement about
staffing), and the same benign distribution reaches the budget only at its
99.968th percentile. The hold-out report measures Stage 2's recall at both
thresholds so the trade-off is a decision somebody makes rather than a number
that looks like a bug — and the honest reading is that the cheaper threshold
is not a fix either. `budget_tau` was cut on the validation day, so it fits
the budget *there* by construction; on the test day it still costs 705 alerts
per analyst per hour, **17.6× over budget**, while DoS recall falls from 75.5%
to 4.6% and four families reach zero. What moves this is dedup (one queue row
per burst rather than per flow — Phase 5), risk ranking, and recalibration
against a local baseline (Phase 9). In the shipped system it is the Live Traffic screen's
threshold slider, and nothing is auto-blocked at either setting.

### Still pending

- the backend API: live SSE replay, dedup, and the seven endpoints the
  dashboard reads — Phase 5
- per-alert explanations: TreeSHAP for Stage 1, per-feature reconstruction
  error for Stage 2 — Phase 5

---

## Limitations

Stating these makes the work more credible, not less.

- Flow-level features cannot see encrypted payload content.
- CICIDS2017 is synthesised lab traffic. A real enterprise baseline is messier
  and drifts faster.
- The autoencoder flags *unusual*, which is not synonymous with *malicious*. A
  new backup job will fire alerts. This is now measured rather than predicted: at
  `tau_anom` Stage 2 flags 5.96% of the Friday test day's benign flows, which is
  22,362 rows of ordinary traffic that look unlike Thursday's ordinary traffic.
- An adaptive adversary can shape traffic to stay under the threshold.
- Leave-one-attack-out measures generalisation to held-out *known* attacks. It
  is a proxy for genuinely novel ones, not proof.
- A model trained on 2017 lab traffic pointed at today's mostly-TLS traffic
  will over-fire until it is recalibrated against a local baseline. That is
  domain shift, and Phase 9 handles it with a shadow-mode burn-in rather than
  treating it as a bug. Phase 3 put a number on how little distance it takes:
  moving `tau_anom` from the day it was calibrated on to the *next day of the
  same capture* raised its false-positive rate 11.9-fold.

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
make docs-serve   # preview the documentation site locally
make data-fetch   # download CICIDS2017 into data/raw (~885 MB)
make data         # clean, split and fit the preprocessing bundle
make train        # Phase 2: RandomForest baseline, then evaluate the test day
make train-lgbm   # Phase 2: LightGBM upgrade, promoted only if it wins
make ablation-port  # Phase 2: raw vs bucketed destination port
```

`make train` must follow `make data`, and `make train-lgbm` must follow
`make train` — the baseline is committed and evaluated before the upgrade is
attempted, and promotion compares the challenger against the incumbent's
validation PR-AUC.

`preprocessing.pkl` has two authors: `make data` writes it under Phase 1's own
port encoding, and training overwrites it with whichever bundle the champion
was fitted against. Running `make data` again after a model exists therefore
desynchronises it from `supervised_model.pkl`, and the API refuses to start on
the mismatch rather than scoring with it. Phase 1 warns at the moment it
happens, and the next training run puts the champion's own bundle back —
including when the run it just finished *lost*, which is the case that used to
leave the pair broken.

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
