# Glossary

Every term a new contributor will hit in this repository, in the code, in `BUILD_PROMPT.md` or in a
review conversation, defined in one to three sentences with a link to the page that goes deeper. It
is for anyone reading the codebase for the first time, from either the machine-learning side or the
web side, who needs the other half's vocabulary.

**Status:** Definitions describe what a term means in this project. Where the thing itself does not
exist yet, the entry says which phase produces it. No entry contains a measured number, because Phase
0 of 9 is complete and no model has been trained. See [Roadmap](Roadmap.md).

[A](#a) · [B](#b) · [C](#c) · [D](#d) · [E](#e) · [F](#f) · [I](#i) · [L](#l) · [M](#m) · [N](#n) ·
[P](#p) · [R](#r) · [S](#s) · [T](#t) · [U](#u) · [V](#v) · [Z](#z)

---

## A

**Alembic** — The migration tool for SQLAlchemy. Schema changes are versioned Python files under
`backend/alembic/versions/`, applied with `alembic upgrade head`, which `make migrate`, `make dev`
and the backend container all run. See [Database Schema](Database-Schema.md) and
[Code: Backend Migrations](Code-Backend-Migrations.md).

**Alerts per analyst hour** — How many alerts a human analyst has to work through per hour of shift.
This project treats it as the honest headline number in place of accuracy: a detector that is
statistically excellent but emits 10,000 alerts a day is unusable. It is the unit the
false-positive budget is expressed in. See [Configuration](Configuration.md).

**Anomaly score** — Stage 2's output for a flow: how unlike normal traffic it looks, measured as the
per-row mean squared reconstruction error of the autoencoder. It carries no attack name, only a
degree of strangeness, and becomes an alert when it exceeds `tau_anom`. See [ML Models](ML-Models.md).

**Artifact bundle** — The set of files `backend/training/` writes to `backend/artifacts/` and the API
loads once at startup: `preprocessing.pkl` (fitted scaler, feature order, dropped columns, port
encoding, schema hash), `supervised_model.pkl`, `autoencoder.pt` and `model_card.json` (version and
thresholds). Loaded by `ModelBundle.load()` in `app/inference.py`; the directory is gitignored
because artifacts are reproducible outputs, not source. Not produced yet — Phases 1 to 3 write it.

**Autoencoder** — A neural network trained to reconstruct its own input through a narrow bottleneck,
so it can only reproduce patterns it has seen. Recluse's Stage 2 model is a PyTorch autoencoder with
the shape `input(d) → 64 → 32 → 16 → 32 → 64 → output(d)`, trained on benign traffic exclusively;
traffic it reconstructs badly is traffic that does not look normal. Phase 3 trains it. See
[ML Models](ML-Models.md).

---

## B

**Benign** — Traffic labelled as not an attack. It is both a class Stage 1 can predict and the sole
training material for Stage 2 — Monday of CICIDS2017 is benign-only, which makes it a clean
autoencoder training set with zero label contamination.

**Botnet** — A network of compromised hosts under a common controller, visible at flow level as
regular beaconing to a command-and-control endpoint. One of the eight Stage 1 classes and one of the
families held out in the leave-one-attack-out evaluation; it appears on Friday in CICIDS2017.

**Brute force** — Repeated authentication attempts against a service until credentials are guessed.
In CICIDS2017 this is FTP-Patator and SSH-Patator on Tuesday, collapsed into the single
`brute_force` class.

---

## C

**CICFlowMeter** — A tool that turns a raw packet capture into the flow-level feature rows
CICIDS2017 is built from. Phase 9 uses it to score a pcap through the exact same `features.py` the
training pipeline used, so live traffic never gets its own feature code.

**CICIDS2017** — The labelled intrusion-detection dataset this project trains on: five days of
lab-generated traffic with a usable day structure (benign Monday, brute force Tuesday, DoS
Wednesday, web attacks and infiltration Thursday, botnet/port scan/DDoS Friday). Its original labels
contain documented errors and corrected re-releases exist, which the project states rather than
glosses over. See [Data Pipeline](Data-Pipeline.md).

**Class weight** — A per-class multiplier applied to the loss so rare attack classes are not drowned
out by the benign majority. Stage 1 uses `class_weight='balanced'` or explicit per-class weights;
notably it does **not** use SMOTE. Phase 2.

**Confusion matrix** — A table of predicted class against true class, showing exactly which attacks
get mistaken for which. More informative than a single score on imbalanced data because it exposes
where the errors actually land. Phase 2 emits one; no matrix exists yet.

---

## D

**DDoS** — Distributed denial of service: a flood from many sources at once, as opposed to DoS from
one. A Stage 1 class, present on Friday in CICIDS2017.

**DoS** — Denial of service from a single source, including the Hulk, GoldenEye, Slowloris and
Slowhttptest variants on Wednesday in CICIDS2017, collapsed into one `dos` class.

**Drift** — The monitored network's normal behaviour changing over time, so that a model trained on
an older baseline starts misfiring. Detected by tracking PSI per feature against the training
reference distribution and by overlaying the training benign score distribution on recent traffic.
Phase 7 implements it; `app/drift.py` currently raises `NotImplementedError`.

---

## E

**ECOD** — Empirical Cumulative distribution based Outlier Detection, a parameter-free anomaly
detector from PyOD. Run as one of three Stage 2 baselines so the autoencoder has to earn its
complexity; if a baseline wins, that is a finding to report rather than hide. Phase 3.

---

## F

**False-positive budget** — The arithmetic that turns a threshold from an arbitrary `0.5` into an
engineering decision: `max_alerts_per_day = analyst_capacity_per_hour * analyst_shift_hours`, then
`target_FPR = max_alerts_per_day / expected_daily_flow_volume`. With the shipped defaults that is
`40 * 8 = 320` alerts/day over 1,000,000 flows/day, giving a target FPR of `3.2e-4`. Computed today
by `Settings.target_fpr` and logged at startup; consumed by threshold selection in Phase 2. See
[Configuration](Configuration.md).

**False-positive rate (FPR)** — The fraction of benign flows the model flags as attacks. On traffic
that is roughly 99% benign, a rate that sounds negligible still floods the queue: 1% of a million
flows is 10,000 alerts a day against a capacity of 320. One of the four metrics the project is
required to report, alongside PR-AUC, per-class recall and alerts per analyst hour.

**Feature order** — The exact column order of the feature matrix, persisted in the artifact bundle.
It is load-bearing rather than cosmetic: a model fed the right columns in the wrong order produces
garbage scores and raises nothing at all, which is why the order is hashed and checked at startup.
See [Testing](Testing.md).

**Flow record** — One row of the dataset: a summary of a single network conversation (durations,
packet and byte counts, inter-arrival time statistics, flag counts) rather than raw packets. Flow
features cannot see encrypted payload content, which the project states as a limitation.

**Fusion** — The layer that combines both stages into one decision: if the supervised attack
confidence clears `tau_sup` it is a `KNOWN` alert with a family; otherwise, if the anomaly score
clears `tau_anom` it is an `UNCLASSIFIED_ANOMALY`; otherwise nothing is emitted. Collapsing this into
a single model would remove the entire point of the architecture. Phase 4. See
[Architecture](Architecture.md).

---

## I

**IAT** — Inter-arrival time: the gap between successive packets in a flow. CICIDS2017 provides
forward, backward and combined IAT statistics (min, max, mean, standard deviation); some columns
contain negative values that `clean.py` must clip or drop and log.

**Infiltration** — An attack where a compromised host is used as a foothold to move inside the
network, on Thursday afternoon in CICIDS2017. It is unusually hard for a supervised classifier
because it looks like ordinary internal traffic, which makes it the most interesting
leave-one-attack-out row.

**IsolationForest** — A tree-based anomaly detector that isolates outliers by random splitting. Run
as a Stage 2 baseline alongside LOF and ECOD. Phase 3.

---

## L

**Leakage** — Information reaching the model that would not exist at prediction time, or that lets it
memorise identities instead of learning behaviour. Controlled here by dropping `flow_id`,
`source_ip`, `destination_ip` and `source_port` before training — the `LEAKAGE_COLUMNS` denylist in
`training/features.py` — and by keeping `timestamp` only until splitting. `destination_port` is
deliberately *not* on the denylist so Phase 1 has to decide about it explicitly. See
[Data Pipeline](Data-Pipeline.md).

**Leave-one-attack-out (LOAO)** — The project's headline evaluation. For each attack family: remove
every row of that family from supervised training, retrain Stage 1, leave the autoencoder untouched
(it never saw attacks anyway), then run the full fusion pipeline on a test set containing the family
and record what fraction was caught and by which stage. It is what turns "catches attacks it was
never trained on" from an assertion into a measurement. Phase 4 produces the table as
`reports/loao.md`; **not measured yet**.

**LightGBM** — A gradient-boosting library, fast on wide tabular data and good with categoricals.
It is the Stage 1 upgrade, adopted only after the RandomForest baseline runs end to end and is
evaluated, with the baseline kept as a fallback artifact. Phase 2.

**LOF** — Local Outlier Factor, a density-based anomaly detector that scores a point by how isolated
it is relative to its neighbours. The third Stage 2 baseline, with IsolationForest and ECOD. Phase 3.

---

## M

**MITRE ATT&CK** — A public catalogue of adversary techniques with stable identifiers. Each alert
family maps to a technique ID plus a one-line plain-English description of what that technique means,
from the static reviewed table in `app/mitre.py`, so an analyst does not have to open a second tab.
`UNCLASSIFIED_ANOMALY` maps to no technique on purpose. Phase 5.

---

## N

**Novel attack** — Attack traffic of a kind the supervised model was never trained on. Catching it is
the core claim this project has to defend, it is what Stage 2 exists for, and LOAO is how the claim
is measured rather than asserted.

---

## P

**PR-AUC** — Area under the precision-recall curve, and the headline model metric here. On heavily
imbalanced data it tells you what fraction of the alerts you raise are real, which is the question a
SOC actually asks; the project renders PR and ROC curves side by side specifically to point at the
gap between them. Phase 2; **not measured yet**.

**PSI** — Population Stability Index, the drift statistic:
`sum over bins of (actual_pct - expected_pct) * ln(actual_pct / expected_pct)`. Warning bands are
0.1 for moderate and 0.25 for significant shift; crossing 0.25 raises the retrain-recommended banner.
Computed nightly per feature in Phase 7; `app/drift.py` currently raises `NotImplementedError`.

**PyOD** — A Python library of outlier-detection algorithms, the source of the IsolationForest, LOF
and ECOD baselines Stage 2 is compared against. Phase 3.

---

## R

**RandomForest** — An ensemble of decision trees, and the Stage 1 baseline: scikit-learn's
`RandomForestClassifier` with `n_estimators=300`, multi-class, `max_depth` tuned against the
validation day. It is deliberately first because it trains in minutes on CPU and gives a working,
honestly evaluated model before anything else is built. Phase 2.

**Reconstruction error** — The squared difference between an autoencoder's input and its output. The
per-row mean is the anomaly score; the per-feature breakdown is the explanation, since the features
the model failed hardest to reconstruct are precisely why the row looks strange. This is why Stage 2
needs no SHAP — the explanation is already computed.

**Replay mode** — The demo and evaluation traffic source: an asyncio background task in `app/replay.py`
that streams held-out test rows at accelerated time (1x, 10x or 100x), scoring in batches and pushing
alerts over SSE. It is honest — real flows with real ground-truth labels — so the dashboard can show
predictions against truth. Phase 5.

**RobustScaler** — A scaler that centres on the median and scales by the interquartile range, so it
is not dragged by extreme values. Chosen over `StandardScaler` because network flow features are
extremely heavy-tailed and a handful of enormous flows would otherwise flatten every other value.
Fitted on the training split only, then persisted in the artifact bundle. Phase 1.

**ROC-AUC** — Area under the receiver-operating-characteristic curve. Reported, but never as the
headline: on heavily imbalanced data it flatters a classifier because the false-positive rate is
divided by a huge benign denominator. PR-AUC is the honest view. See [Anti-Patterns](Anti-Patterns.md).

---

## S

**Schema hash** — A `sha256:`-prefixed digest of the exact feature order, computed by
`compute_schema_hash()` in `training/features.py`, stored in `preprocessing.pkl` and verified at
startup by `ModelBundle._verify_schema_hash()`. It is order-sensitive by design, and a mismatch
raises `SchemaHashMismatch`, which is fatal — train/serve skew is silent, so the check is loud. See
[Testing](Testing.md).

**Shadow mode** — Running the full pipeline in log-only mode against real traffic: score everything,
alert no one. Phase 9 uses it as a burn-in to recompute `tau_anom` from the local traffic's own
reconstruction-error distribution rather than trusting the CICIDS2017-derived threshold, and reports
the gap between the two as a result in its own right.

**SHAP** — SHapley Additive exPlanations, a method that attributes a prediction to its input
features. Used for Stage 1 only, via TreeSHAP; Stage 2 uses per-feature reconstruction error instead,
because KernelSHAP on a neural network is slow and less faithful than the error the model already
produced.

**Signature-based IDS** — A detector that matches traffic against rules for known attacks. It is
accurate on what it has rules for and blind to everything else, which is exactly the gap this project
exists to close.

**SMOTE** — Synthetic Minority Over-sampling Technique, which interpolates new minority-class rows.
Explicitly ruled out here: interpolating between flow records produces packets that could not exist
on a real network, and applying it before the split puts synthetic rows in the test set and inflates
scores outright. Permitted only as an ablation that demonstrates it underperforms. See
[Anti-Patterns](Anti-Patterns.md).

**SOC** — Security operations centre: the team of analysts who receive, triage and act on alerts.
The dashboard is built for them, which is why alert volume and explanation quality matter more here
than a headline score.

**SPAN port** — A switch or router port configured to mirror traffic from other ports, giving a
capture point without touching the traffic itself. One of the three Phase 9 capture options, and
usable only on equipment you own or are explicitly authorised to monitor.

**SSE** — Server-Sent Events, a one-way HTTP streaming protocol used by `GET /api/v1/stream` to push
alerts to the dashboard as they are raised. It needs proxies to stop buffering, which is why
`frontend/nginx.conf` sets `proxy_buffering off` and a 24-hour read timeout on `/api/`. The route is
registered and answers 501 today; Phase 5 implements it.

---

## T

**tau_anom** — The Stage 2 threshold: the 99.5th percentile of reconstruction error on held-out
benign validation data. An anomaly score at or above it becomes an `UNCLASSIFIED_ANOMALY`. Persisted
in the artifact bundle alongside the full benign error distribution as histogram bins, which the
dashboard's threshold slider and drift detection both need. Phase 3; **not measured yet**.

**tau_sup** — The Stage 1 threshold: the smallest attack confidence at which the measured false-
positive rate still stays within `target_fpr`. Chosen by false-positive budget rather than argmax or
a default of `0.5`, and persisted in the artifact bundle. Phase 2; **not measured yet**.

**Temporal split** — Splitting train, validation and test by time — here, by CICIDS2017 day — rather
than randomly. `train_test_split(shuffle=True)` on this data leaks near-identical duplicated flows
across the split and manufactures fake 99.9% scores, so it is a non-negotiable constraint. See
[Data Pipeline](Data-Pipeline.md).

**TreeSHAP** — The exact, fast SHAP implementation for tree ensembles. Used to produce the top-five
feature attributions that explain every Stage 1 alert. Phase 5, via `app/explain.py`.

**Triage queue** — The analyst-facing list of open alerts, ranked by risk. Its usability is the
constraint the whole detection design bends around: without dedupe, one compromised host emitting
5,000 flows fills it within thirty seconds of a replay starting. See
[Frontend Screens](Frontend-Screens.md).

---

## U

**UNCLASSIFIED_ANOMALY** — The alert kind emitted when Stage 2 fires and Stage 1 could not name the
traffic: the project's thesis rendered as an enum value. It carries no family — a database check
constraint enforces that, and a test asserts the constraint — and it must survive to the UI as a
visually distinct badge rather than being flattened into "other". See [Database Schema](Database-Schema.md).

---

## V

**Verdict** — An analyst's judgement on an alert, one of `TP`, `FP` or `UNSURE` plus an optional
note, submitted through `POST /api/v1/alerts/{id}/verdict` and stored in `analyst_verdicts`. It
closes the loop: verdicts feed the Phase 7 retraining pipeline, and an `FP` confirmation is what
qualifies a row for the benign refit pool. The vocabulary is enforced by a check constraint.

---

## Z

**Zeek** — A network security monitor that turns a live interface into flow-level logs directly, an
alternative to capturing raw with tcpdump and running CICFlowMeter over the pcap. Either path
produces the same shape, so `features.py` does not care which one was used. Phase 9.

---

## Related

- [Project Overview](Project-Overview.md) — what the system is and what it claims.
- [Architecture](Architecture.md) — how the two stages and the alert pipeline fit together.
- [ML Models](ML-Models.md) — Stage 1 and Stage 2 in detail.
- [Data Pipeline](Data-Pipeline.md) — dataset, cleaning, splits and features.
- [Anti-Patterns](Anti-Patterns.md) — the practices several of these definitions rule out.
- [FAQ](FAQ.md) — shorter answers to the questions these terms raise.
