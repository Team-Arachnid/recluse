# Models and Evaluation

This page specifies the two models Recluse trains, how their operating thresholds are chosen, how each one explains its own output, which metrics are reported and which are deliberately demoted, and the leave-one-attack-out procedure that produces the project's headline result. It is written for whoever implements Phases 2 through 4, and for a reviewer deciding whether the reported numbers can be trusted.

> **Status: specified, not trained.** Phase 0 of 9 is complete. Every training entry point in `backend/training/` is a docstring-only stub: `train_supervised.py`, `train_autoencoder.py`, `evaluate.py` and `loao.py` each raise `NotImplementedError` naming the phase that implements them. `ModelBundle.score_batch` in `backend/app/inference.py` raises `NotImplementedError("score_batch arrives in Phase 4 (fusion); Stage 1 lands in Phase 2 and Stage 2 in Phase 3.")`. No `.pkl` and no `.pt` exists; `/api/v1/health` reports `model_version: "unloaded"` because that is the truth. Every performance figure on this page is marked as not measured.

---

## The two models

| | Model A | Model B |
| --- | --- | --- |
| Role | Stage 1 — supervised classifier | Stage 2 — anomaly detector |
| Library | scikit-learn `RandomForestClassifier`, then LightGBM | PyTorch autoencoder |
| Trained on | Labelled flows: benign plus known attack families | Benign traffic only, no attack labels at all |
| Answers | "Which named attack is this?" | "How unlike normal traffic is this?" |
| Threshold | `tau_sup`, from the false-positive budget | `tau_anom`, 99.5th percentile of benign reconstruction error |
| Explanation | TreeSHAP, top 5 features | Per-feature reconstruction error, top 5 |
| Artifact | `backend/artifacts/supervised_model.pkl` | `backend/artifacts/autoencoder.pt` |
| Phase | 2 | 3 |

Stage 1 names what it has seen. Stage 2 catches what nobody named. The claim the project has to defend is that it detects attack traffic it was never trained on, and only Stage 2 can make that claim — which is why its training set must be provably free of attack rows, and why the leave-one-attack-out table is the evaluation that matters.

---

## Model A — supervised classifier

### Order of work

`RandomForestClassifier` first, LightGBM second. This ordering is deliberate and is not to be reversed.

1. **Baseline.** `RandomForestClassifier(n_estimators=300)`, multi-class, with `max_depth` tuned against the validation day (Thursday). It trains in minutes on CPU with no GPU setup and gives a working, honestly-evaluated model on day one. Commit it as a running baseline before touching anything else.
2. **Upgrade.** LightGBM as a swap-in replacement once the baseline runs end to end and has been evaluated. It is faster than XGBoost on wide tabular data and handles categoricals natively, and it will generally beat the forest here. It is an improvement on a working system, not a prerequisite for starting one.
3. **Keep the fallback.** The RandomForest artifact is retained as a fallback rather than overwritten. If the LightGBM model regresses on a family, there is something to compare against and something to serve.

Early stopping is evaluated against the validation day, never against the test day.

### The eight classes

Seven attack families plus benign. The attack families are defined once, as `ALERT_FAMILIES` in `backend/app/models.py`, and mirrored into the wire contract in `backend/app/schemas.py`.

| Class | CICIDS2017 sub-families that collapse into it | Capture day |
| --- | --- | --- |
| `benign` | `BENIGN` | All five days |
| `dos` | `DoS Hulk`, `DoS GoldenEye`, `DoS slowloris`, `DoS Slowhttptest` | Wednesday |
| `ddos` | `DDoS` | Friday |
| `brute_force` | `FTP-Patator`, `SSH-Patator` | Tuesday |
| `port_scan` | `PortScan` | Friday |
| `web_attack` | `Web Attack - Brute Force`, `Web Attack - XSS`, `Web Attack - Sql Injection` | Thursday (AM) |
| `botnet` | `Bot` | Friday |
| `infiltration` | `Infiltration` | Thursday (PM) |

`Heartbleed` (Wednesday) has very few rows; its destination class is a decision to be made and documented in Phase 1, not silently dropped. The collapsing map is written as an explicit dictionary and logged, so any label that failed to map is visible rather than turning into `NaN`.

### Class imbalance: weights, not SMOTE

The dataset is overwhelmingly benign. The handling is `class_weight="balanced"` or explicit per-class weights.

SMOTE is rejected, for two separate reasons:

- **It invents impossible traffic.** SMOTE interpolates between feature vectors. A flow record is a set of physically coupled counts — packets, bytes, durations, flag totals. The midpoint of two real flows is a row describing a conversation that could not occur on a network: fractional packets, byte totals inconsistent with the packet counts, durations inconsistent with the inter-arrival times. The model then learns a decision boundary partly defined by traffic that does not exist.
- **Applied before the split it corrupts the test set outright.** Synthetic rows interpolated from test-set neighbours end up in training, and the resulting scores are meaningless. This is listed as an anti-pattern in BUILD_PROMPT.md Part 12.

If SMOTE is demonstrated at all it is as an ablation, reported alongside the weighted model, showing that it underperforms.

**Artifact:** `backend/artifacts/supervised_model.pkl`, written by `backend/training/train_supervised.py`. It is loaded once at startup by `ModelBundle._load_models`, which reads it with `pickle` and documents the trust boundary in place: everything under the artifacts directory is produced locally by `backend/training/` and is gitignored, and the API has no artifact-upload path.

---

## Threshold selection by false-positive budget

This is the single calculation that reframes the operating threshold from an arbitrary 0.5 into an engineering decision, and it is the part of the project reviewers remember. `argmax` over class probabilities is not used, and neither is a default 0.5 cut.

```
Given expected daily flow volume V and analyst capacity C alerts/hour:

  max_alerts_per_day = C * analyst_shift_hours
  target_FPR         = max_alerts_per_day / V
  tau_sup            = smallest threshold where FPR(tau) <= target_FPR
```

| Symbol | Meaning | Where it comes from |
| --- | --- | --- |
| **V** | Expected daily flow volume on the network being defended — how many flows per day the system will actually score | `settings.expected_daily_flow_volume`, env `IDS_EXPECTED_DAILY_FLOW_VOLUME`, default `1_000_000` |
| **C** | Analyst capacity, in alerts triaged per hour | `settings.analyst_capacity_per_hour`, env `IDS_ANALYST_CAPACITY_PER_HOUR`, default `40` |
| shift | Hours in an analyst shift | `settings.analyst_shift_hours`, env `IDS_ANALYST_SHIFT_HOURS`, default `8` |
| `max_alerts_per_day` | The numerator of the budget | `Settings.max_alerts_per_day`, a `computed_field`: `analyst_capacity_per_hour * analyst_shift_hours` |
| `target_FPR` | The false-positive rate the threshold must respect | `Settings.target_fpr`, a `computed_field`: `max_alerts_per_day / expected_daily_flow_volume` |

All three inputs live in `backend/app/config.py` under the `IDS_` env prefix, are declared with `Field(..., gt=0)` so a zero or negative value is rejected at startup rather than producing a division by zero, and are stated in the README. The docstring on `Settings.target_fpr` records the intent directly: Phase 2 picks `tau_sup` as the smallest threshold whose measured FPR stays under this number, instead of defaulting to 0.5.

### Worked example

The arithmetic below uses the shipped defaults. The inputs are real configuration values; the FPR column is **illustrative arithmetic, not a measurement** — no model has been trained, so no FPR curve exists yet.

Step 1 — the budget.

```
C                  = 40 alerts/hour        (IDS_ANALYST_CAPACITY_PER_HOUR)
analyst_shift_hours = 8 hours              (IDS_ANALYST_SHIFT_HOURS)
max_alerts_per_day = 40 * 8   = 320 alerts/day

V                  = 1,000,000 flows/day   (IDS_EXPECTED_DAILY_FLOW_VOLUME)
target_FPR         = 320 / 1,000,000 = 0.00032  = 0.032%
```

One analyst, one shift, can absorb 320 alerts per day. Against a million flows a day, that is a false-positive rate of 32 in 100,000.

Step 2 — sweep the threshold over the validation day and measure FPR at each candidate. FPR is false positives divided by the number of true benign rows:

```
FPR(tau) = (benign rows scored >= tau) / (total benign rows)
```

Step 3 — read off the smallest `tau` whose FPR fits the budget. Laid out as a table, the choice is mechanical:

| candidate tau | FPR(tau) | alerts/day at V = 1,000,000 | within 320/day budget |
| --- | --- | --- | --- |
| 0.50 | 0.0040 | 4,000 | no |
| 0.70 | 0.0015 | 1,500 | no |
| 0.85 | 0.00060 | 600 | no |
| **0.91** | **0.00030** | **300** | **yes — first threshold that fits** |
| 0.95 | 0.00011 | 110 | yes, but strictly worse recall |

`tau_sup = 0.91` in this illustration: the *smallest* threshold that satisfies the budget, because anything higher throws away recall the analysts could have absorbed. The default 0.5 would have produced 4,000 alerts a day for a team that can handle 320 — a queue that is abandoned within a week, which makes the detector worthless regardless of its PR-AUC.

Step 4 — report it. The chosen `tau_sup`, the V and C it was derived from, and the resulting alerts/analyst/hour all go into the evaluation write-up, so the threshold can be recomputed for a different network by changing two numbers in `.env`.

### Where tau_sup lives

`tau_sup` is persisted into the artifact bundle by `train_supervised.py`. At startup `ModelBundle._load_model_card` reads `model_card.json` and lifts `thresholds.tau_sup` (and `thresholds.tau_anom`) onto the bundle, where `ModelBundle.stage1_ready` requires both a loaded supervised model and a non-`None` `tau_sup` before Stage 1 counts as usable. The budget inputs that produced it are mirrored in settings so the API can display and re-derive them, but the threshold the served model actually uses is the one in the artifact, not one recomputed at request time. An unpersisted threshold is listed as an anti-pattern for exactly this reason: the backend and the frontend would silently disagree about what "alert" means.

---

## Model B — autoencoder

### Training set

Benign rows only: Monday in full, plus the benign rows from Tuesday and Wednesday. Not one attack row.

This is not a preference; it is what makes the novel-attack claim real rather than a relabelled supervised model. It must be asserted in code, not merely intended:

```python
assert (benign_train["label"] == "benign").all(), "attack rows leaked in"
```

If an attack row enters the Stage 2 training set, the autoencoder learns to reconstruct that attack, stops flagging it, and the project's central claim collapses — with no error raised anywhere. The assert is produced by Phase 1 (see [Data and Feature Pipeline](Data-Pipeline)) and re-checked by Phase 3 before training starts.

### Architecture

```
input(d) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(d)
```

| Element | Choice | Note |
| --- | --- | --- |
| Activation | ReLU | |
| Loss | MSE | Reconstruction error is the score, so the loss and the score are the same quantity |
| Optimiser | Adam | |
| Stopping | Early stopping on benign validation loss | Validation loss is computed on held-out benign rows only |
| Regularisation | Dropout 0.1 in the encoder | Prevents the bottleneck memorising individual benign flows |
| Normalisation | Batch norm | Helps convergence at this depth and width |
| Bottleneck | 16 units | `d` is the feature count fixed by the Phase 1 `feature_order` |

The bottleneck is the mechanism: the network can only pass 16 numbers through the middle, so it must learn the structure that benign traffic actually has. A flow that does not share that structure cannot be squeezed through and comes back distorted.

### Score

Per-row mean squared reconstruction error:

```
score(x) = mean((x - x_hat) ** 2)
```

High score means the row is unlike anything in the benign training distribution. No attack label is involved at any point in producing it.

### Threshold

`tau_anom` is the **99.5th percentile of reconstruction error on held-out benign validation data**. Setting it from benign data alone keeps Stage 2 honest: the threshold is a statement about normal traffic, not a value tuned until the attacks happened to land above it.

The benign error distribution is persisted as **histogram bins, not raw rows**, and two consumers need it:

- The dashboard's interactive threshold slider, which shows an analyst how the alert count moves as the threshold moves.
- Drift detection, which compares today's benign error distribution against the one the model was calibrated on.

`ModelBundle` carries fields for both: `tau_anom` and `benign_error_histogram`, and `stage2_ready` requires the weights and `tau_anom` together.

**Artifact:** `backend/artifacts/autoencoder.pt`, a state dict. `ModelBundle._load_models` imports `torch` lazily — a heavy import that Phase 0 startup should not pay for when there is nothing to load — and reads the file with `torch.load(..., map_location="cpu", weights_only=True)`, so the Stage 2 file is data rather than code. Phase 3 owns the `nn.Module` definition and reconstructs the model from the state dict.

### Phase 3 checkpoint

A histogram of benign versus attack reconstruction error with the `tau_anom` line drawn on it. The two distributions should visibly separate. If they do not, the model is not working, and no dashboard will hide that.

---

## Baselines

Three classical anomaly detectors are run on the same split, with the same features and the same benign-only training data:

| Baseline | Source | What it tests |
| --- | --- | --- |
| `IsolationForest` | scikit-learn | Whether random axis-aligned partitioning isolates the anomalies as well as a learned representation does |
| Local Outlier Factor (LOF) | scikit-learn | Whether local density is enough, without a global model of normal |
| ECOD | PyOD | A parameter-free empirical-CDF detector — the strongest "no tuning at all" reference point |

They exist for two reasons. First, they establish whether the autoencoder earns its complexity: a neural network that ties an `IsolationForest` is a neural network that should not be in the system. Second, and more importantly, **if a baseline beats the autoencoder, that is a finding to report rather than hide.** A project that reports "ECOD outperformed our autoencoder on port scan recall, and here is why we think that is" is more credible than one that quietly drops the comparison, and the baseline result does not weaken the architecture — the two-stage design still holds, with a different Stage 2.

Results: not measured yet — Phase 3 produces these.

---

## Explanation strategy

Every alert carries an explanation. An alert with a score and no reason is an alert an analyst ignores, and `backend/app/explain.py` exists to guarantee one per stage.

| Stage | Explainer | Output | Entry point |
| --- | --- | --- | --- |
| Stage 1 (tree model) | TreeSHAP | Top 5 contributing features | `explain_supervised` |
| Stage 2 (autoencoder) | Per-feature reconstruction error | Top 5 worst-reconstructed features | `explain_anomaly` |

Both are wrapped by `narrate`, which templates the result into English through a per-feature phrase map. The target register, recorded in its docstring: *"2,400 distinct destination ports contacted in 8 seconds from a single source."* The narration is a static phrase map, not freeform prose — unconstrained per-alert text is unreviewable advice a SOC cannot trust, and is listed as an anti-pattern.

### Why not KernelSHAP on the autoencoder

KernelSHAP is model-agnostic and therefore expensive: it approximates attributions by repeatedly perturbing the input and re-running the model, which on a neural net costs thousands of forward passes per alert. It is also only an approximation.

The autoencoder gives the explanation away for free, exactly and immediately. The per-feature squared error is already computed as part of the score, and the features the model failed hardest to reconstruct are precisely why the row looks anomalous:

```python
per_feature_error = (x - x_hat) ** 2          # already computed
top_contributors = argsort(per_feature_error)[-5:]
```

This is faster, more faithful to the model, and more interpretable than an approximated attribution. TreeSHAP is used for the supervised stage because it is exact for tree ensembles and fast; reconstruction error is used for the anomaly stage because the model hands it over as a by-product of scoring.

---

## Metrics that count

| Metric | Why it is reported | Why the obvious alternative misleads |
| --- | --- | --- |
| **PR-AUC** (headline) | Precision and recall are both computed against the rare positive class, so the score cannot be inflated by the benign majority | ROC-AUC uses FPR, whose denominator is the enormous benign count. Thousands of false positives barely move it, so a model that floods the queue still scores well |
| **Per-class recall** | The system is judged on families, not on an average. Missing infiltration entirely is invisible in a pooled score | A macro F1 or a single recall figure lets strong performance on `dos` and `port_scan` mask a family that is never detected |
| **FPR at the chosen threshold** | The one number that determines whether the alert queue is workable; it is the quantity `tau_sup` is selected against | FPR at an unspecified or default threshold describes an operating point nobody will run |
| **Alerts per analyst per hour** | Translates FPR into the only unit a SOC actually budgets in; it is what makes the threshold an engineering decision | A rate with no volume attached hides that 0.4% FPR on a million flows is 4,000 alerts a day |
| **Per-class precision, F1, support** | Support alongside the rates shows which numbers rest on a handful of rows | A rate quoted without support invites reading 100% recall on 11 rows as a result |
| **Confusion matrix** | Shows *which* family a miss was confused with, which is what drives the next fix | Aggregate metrics say a class is weak; only the matrix says what it is being mistaken for |
| Accuracy (table cell only, never a headline) | Included for completeness and comparability with published work | On traffic that is roughly 99% benign, a model that always answers `benign` scores about 99%. The number describes the class balance, not the model |

### PR versus ROC, rendered side by side

The PR curve and the ROC curve are plotted next to each other deliberately. The gap between them **is** the explanation, and pointing at it is faster than arguing about it.

```
   ROC (flattering)                 PR (honest)
 1 |        _____                 1 |
   |      /                         |  \
TPR|    /                        Prec|   \____
   |  /                             |        \____
 0 |/________________             0 |_____________\___
   0        FPR        1            0    Recall      1

 FPR = FP / (FP + TN)             Precision = TP / (TP + FP)
 TN is enormous, so FPR           No TN term, so every false
 barely moves -> curve hugs       positive costs precision
 the top-left regardless          immediately
```

A detector producing 4,000 false positives a day against a million benign flows has an FPR of 0.4%, which leaves the ROC curve looking near-perfect. The same detector, if it surfaces 300 true attacks, has a precision of about 7% — and the PR curve says so. The PR curve is the one an analyst's experience of the queue corresponds to.

All figures: not measured yet — Phase 2 produces the Stage 1 numbers, Phase 3 the Stage 2 numbers, and Phase 4 the fused results. `backend/training/evaluate.py` is the entry point and currently raises `NotImplementedError`.

---

## Leave-one-attack-out

This is the evaluation that makes the project's claim measurable rather than asserted. It answers a question an ordinary held-out test set cannot: *what happens when the system meets an attack family it has never seen?*

### Procedure

For each attack family F in `dos`, `ddos`, `brute_force`, `port_scan`, `web_attack`, `botnet`, `infiltration`:

1. Remove **all** rows labelled F from the supervised training set. Not downsampled — removed.
2. Retrain the supervised model from scratch on the remaining families. Keep every other hyperparameter, the feature set, and the threshold procedure identical, so the only variable is the missing family.
3. Leave the autoencoder **untouched**. It is not retrained, and it does not need to be: it never saw any attack rows in the first place, so removing F changes nothing about its training set. This is what makes the comparison clean.
4. Run the full fusion pipeline over a test set that contains F. Fusion is the two-stage rule: if the supervised attack confidence clears `tau_sup` the alert is `KNOWN`; otherwise if the reconstruction error clears `tau_anom` the alert is `UNCLASSIFIED_ANOMALY`; otherwise nothing is emitted.
5. Record what fraction of F was flagged, **and by which stage**. Stage 1 catching a held-out family means it generalised from a related family; Stage 2 catching it means the anomaly detector did the job it exists for.
6. Repeat for every family, then write the table.

Re-deriving `tau_sup` for each retrained model, from the same budget, keeps the operating point comparable across rows rather than comparing models at arbitrarily different sensitivities.

### Result table

Committed as `reports/loao.md`. The skeleton below is the required shape. Nothing has been trained, so every cell is unmeasured.

| Held-out family | Caught by Stage 1 | Caught by Stage 2 | Total recall | Missed |
| --- | --- | --- | --- | --- |
| dos | not measured yet | not measured yet | not measured yet | not measured yet |
| ddos | not measured yet | not measured yet | not measured yet | not measured yet |
| brute_force | not measured yet | not measured yet | not measured yet | not measured yet |
| port_scan | not measured yet | not measured yet | not measured yet | not measured yet |
| web_attack | not measured yet | not measured yet | not measured yet | not measured yet |
| botnet | not measured yet | not measured yet | not measured yet | not measured yet |
| infiltration | not measured yet | not measured yet | not measured yet | not measured yet |

The **Stage 2 column is the headline number**. A sentence of the form "the system had never seen infiltration traffic and surfaced N% of it" is a measured claim about catching what signatures miss, and it is worth more than any accuracy figure the project could print.

The **Missed column is mandatory.** It is not an optional extra column and it is not to be omitted when it looks bad. A table with a real miss rate in it reads as credible engineering; a table of 99s reads as a bug, and a reviewer will assume duplicate leakage across the splits before believing the number. Reporting a family the system largely fails to catch is a stronger result than reporting seven families it allegedly catches perfectly.

`backend/training/loao.py` is the entry point and currently raises `NotImplementedError("loao.py is implemented in Phase 4 (fusion and LOAO).")`.

---

## Artifact inventory

Everything below is written by `backend/training/` on the machine that runs it, into `backend/artifacts/` (`IDS_ARTIFACTS_DIR`, resolved by `Settings.artifacts_path`). The directory is gitignored: it holds reproducible output, not source. None of it exists yet.

| Artifact | Produced by | Contains | Consumed by |
| --- | --- | --- | --- |
| `preprocessing.pkl` | `split.py` / `features.py` (Phase 1) | `scaler`, `feature_order`, `dropped_columns`, `port_encoding`, `schema_hash` | `ModelBundle.load` at startup; `build_feature_matrix` on every scoring path |
| `supervised_model.pkl` | `train_supervised.py` (Phase 2) | Fitted RandomForest or LightGBM multi-class classifier | `ModelBundle._load_models` -> `ModelBundle.supervised`; Stage 1 of fusion; TreeSHAP in `explain.py` |
| `autoencoder.pt` | `train_autoencoder.py` (Phase 3) | Torch state dict for the 64-32-16-32-64 network | `ModelBundle._load_models` -> `autoencoder_state`, loaded with `weights_only=True`; Stage 2 of fusion |
| `model_card.json` | `evaluate.py` (Phase 2/3) | `version`, `thresholds.tau_sup`, `thresholds.tau_anom`, `schema_hash`, metrics, dataset provenance | `ModelBundle._load_model_card`; `/api/v1/health` model version; the dashboard model card screen |
| `reports/loao.md` | `loao.py` (Phase 4) | The leave-one-attack-out table | Committed to the repository; quoted in the README and in this wiki |

Two consistency checks run at load and are fatal rather than advisory: the `schema_hash` in `preprocessing.pkl` must match a hash recomputed from its own `feature_order`, and the `schema_hash` in `model_card.json` must match the one in `preprocessing.pkl`. Either mismatch raises `SchemaHashMismatch` and the service refuses to start, because a model paired with the wrong preprocessing produces confident nonsense without raising anything on its own. See [Data and Feature Pipeline](Data-Pipeline) for the full argument.
