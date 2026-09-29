# Data and Feature Pipeline

This page describes how raw CICIDS2017 capture files become the matrices the two models train on: which day plays which role, every known defect in the published dataset and the prescribed handling for it, the leakage deny-list, why the split is temporal rather than random, how scaling is fitted, and the preprocessing bundle contract that keeps training and serving from drifting apart. It is written for whoever implements Phase 1, and for anyone reviewing the numbers that come out of it later.

> **Status: done.** The pipeline has been run end to end against the real release: 2,830,743 raw flow records in, 2,572,640 after cleaning, split by capture day into train (1,024,072), val (398,507), test (595,894) and an attack-free benign_train (1,331,862), with a 70-feature preprocessing bundle persisted. The full per-class table is in [Roadmap](Roadmap.md#measured-on-the-real-release). Numbers on this page that describe *this repository's run* are measured; numbers describing CICIDS2017 in general are quoted from its documentation.

---

## Dataset

The project uses **CICIDS2017** from the Canadian Institute for Cybersecurity (UNB), taking the pre-extracted flow-feature CSVs rather than the raw pcaps. One row is one flow: a single network conversation summarised as roughly 78 numeric columns (duration, packet counts, byte rates, inter-arrival times, flag counts). Roughly 8 CSV files, on the order of 2.8 million rows in total.

The dataset was captured over five working days, each day carrying a different attack programme. That structure is what makes an honest temporal split possible without inventing one.

| Day | Attack content | Role | Destination |
| --- | --- | --- | --- |
| Monday | None — benign only | Autoencoder training | `data/processed/benign_train.parquet` (with benign rows from Tue/Wed) |
| Tuesday | FTP-Patator, SSH-Patator | Train | `data/processed/train.parquet` |
| Wednesday | DoS Hulk, DoS GoldenEye, DoS Slowloris, DoS Slowhttptest, Heartbleed | Train | `data/processed/train.parquet` |
| Thursday | Web attacks (morning), Infiltration (afternoon) | Validation | `data/processed/val.parquet` |
| Friday | Botnet, Port Scan, DDoS | Test | `data/processed/test.parquet` |

Monday being benign-only is the single most useful property of this dataset for this project. Stage 2 must train on traffic containing no attack rows at all, and Monday supplies that without any filtering step that could be got wrong.

### Filenames

The distributed files are named approximately as below. Two details cost time if not known in advance.

```
Monday-WorkingHours.pcap_ISCX.csv
Tuesday-WorkingHours.pcap_ISCX.csv
Wednesday-workingHours.pcap_ISCX.csv
Thursday-WorkingHours-Morning-WebAttacks.pcap_ISCX.csv
Thursday-WorkingHours-Afternoon-Infilteration.pcap_ISCX.csv
Friday-WorkingHours-Morning.pcap_ISCX.csv
Friday-WorkingHours-Afternoon-PortScan.pcap_ISCX.csv
Friday-WorkingHours-Afternoon-DDos.pcap_ISCX.csv
```

- `Infilteration` is misspelled in the dataset itself. Match the name on disk; do not correct it in code and then debug a missing file.
- Capitalisation is inconsistent (`WorkingHours` versus `workingHours`), which matters on case-sensitive filesystems. Discover files with a glob, log what matched, and fail loudly when a day is absent. Half a dataset produces half a result and no error.

### Label quality, stated rather than hidden

The original CICIDS2017 label set contains documented errors, and corrected re-releases exist. Phase 1 is not expected to repair them. It **is** expected to say so — in the README, in the Phase 1 write-up, and in the model card. Reviewers already know this about the dataset; acknowledging it signals the work went past the abstract. If a corrected re-release is used instead of the original distribution, record which one and its version, because the class counts will not match published figures from the original.

### Why not NSL-KDD

NSL-KDD derives from the 1998/1999 DARPA captures. The traffic mix, protocol distribution and attack techniques are a quarter of a century old, and results on it are routinely discounted for exactly that reason. It is not used here as a primary dataset. If it appears at all, it is as a secondary sanity check, clearly labelled as such.

---

## Known defects in CICIDS2017 and how clean.py handles each

`backend/training/clean.py` names six defects in its module docstring. Each is handled explicitly and each handling is logged with a count, because those counts are the content of the Phase 1 write-up.

The prescribed order matters. Headers are normalised first so that every later step can refer to columns by name; duplicates are dropped before splitting so that no duplicate can straddle two splits.

```
raw CSV
   |
   v  normalise_headers      strip + snake_case, via features.normalise_columns
   v  fix_infinities         Inf -> NaN, then drop or impute (documented)
   v  drop_duplicate_rows    exact duplicates, BEFORE any split
   v  drop_zero_variance     computed on the training split, applied everywhere
   v  fix_negative_values    clip at zero or drop
   v  normalise_labels       raw sub-family -> one of eight classes
   |
   v  data/interim/<day>.parquet
```

### 1. Header whitespace

**Symptom.** Column names in the published CSVs carry leading and often trailing whitespace. `" Flow Duration"` is not `"Flow Duration"`. The label column is `" Label"`. There is also a genuinely duplicated header, `Fwd Header Length`, usually disambiguated on load as `Fwd Header Length.1`.

**Consequence if unhandled.** `frame["Flow Duration"]` raises `KeyError` while `frame[" Flow Duration"]` works, so code written from the documentation fails against the file. Worse, partially-normalised code silently selects the wrong subset of columns. This single detail breaks more attempts at this dataset than anything else.

**Handling.** Normalise once, first, using the function that already exists — do not write a second one:

```python
from training.features import normalise_columns

frame.columns = normalise_columns(list(frame.columns))
```

`normalise_column_name` strips, lowercases, collapses every run of non-alphanumeric characters to a single underscore, then trims leading and trailing underscores. The behaviour is pinned by `backend/tests/test_features.py`:

| Raw header | Normalised |
| --- | --- |
| `" Flow Duration"` | `flow_duration` |
| `"Flow Duration "` | `flow_duration` |
| `"Flow Bytes/s"` | `flow_bytes_s` |
| `"Flow Packets/s"` | `flow_packets_s` |
| `"Fwd Header Length.1"` | `fwd_header_length_1` |
| `"Bwd PSH Flags"` | `bwd_psh_flags` |
| `"Destination Port"` | `destination_port` |
| `"  Label  "` | `label` |

The shared function is mandatory rather than merely convenient: serving code must normalise identically to training code. A second implementation in the API is the train/serve skew bug being born.

### 2. Inf and NaN in `flow_bytes_s` and `flow_packets_s`

**Symptom.** Both columns contain `inf`, `-inf` and `NaN`. They are rates — bytes or packets divided by duration — and flows recorded with zero duration divide by zero.

**Consequence if unhandled.** `RobustScaler` and every downstream model reject or propagate non-finite values. Where they do not reject them, a single `inf` poisons a whole quantile computation.

**Handling.** Replace the infinities with NaN first, then make an explicit, recorded decision on what happens to those rows:

```python
frame = frame.replace([np.inf, -np.inf], np.nan)
```

Dropping and imputing with zero are both defensible. Dropping is simpler and the affected count is small. Imputing zero is arguably more truthful, in that a zero-duration flow did not transfer data at infinite speed. Whichever is chosen, the write-up must contain a sentence of the shape "dropped N rows with non-finite rate values, X% of the data, because ...". An unexplained choice here is one of the first things a reviewer asks about.

### 3. Massive exact-duplicate rows

**Symptom.** The dataset contains a very large number of byte-identical rows.

**Consequence if unhandled.** This is the most damaging defect in the set. If duplicates survive into the split, the same row lands in both train and test, the model is scored on rows it memorised, and the result is a 99.9% figure that measures nothing. It is the single largest source of inflated scores in published work on this dataset, which means the high number actively harms the project: a reviewer seeing 99.9% assumes this bug before reading anything else.

**Handling.** Drop exact duplicates before splitting, and log the count:

```python
before = len(frame)
frame = frame.drop_duplicates()
print(f"dropped {before - len(frame)} exact duplicate rows")
```

A substantial fraction of the data is expected to disappear. That is the correct outcome, not a problem to work around.

### 4. Zero-variance columns

**Symptom.** Several columns hold the same value in every row. `bwd_psh_flags` and `fwd_urg_flags` are all-zero throughout, and they are not the only ones.

**Consequence if unhandled.** A column with one distinct value carries no information. It costs training time, adds a meaningless entry to `feature_order`, and pads SHAP output with features that cannot explain anything.

**Handling.** Find them programmatically; never hardcode a list:

```python
constant = [c for c in numeric_cols if frame[c].nunique(dropna=False) <= 1]
```

Compute the list on the **training split**, not per file. A column can be constant on Monday and informative on Friday, and dropping it per-day would give the three splits different schemas. The safe order is: clean each day, concatenate, split, compute the constant list on train only, then apply that same list to train, val, test and benign_train. The resulting list is exactly what goes into the `dropped_columns` entry of the preprocessing bundle.

### 5. Negative durations and IAT values

**Symptom.** Some duration and inter-arrival-time columns contain negative values. (IAT is the gap between consecutive packets.)

**Consequence if unhandled.** A negative elapsed time is a capture artefact, not a measurement. Left in, it widens the interquartile range used by `RobustScaler` and gives the models a physically impossible region of feature space to fit.

**Handling.** Clip at zero or drop the affected rows, and log the count either way. Clipping preserves row counts and is usually preferred; dropping is acceptable if the count is small and is recorded.

### 6. CSV versus Parquet

**Symptom.** The dataset ships as CSV. CSV carries no type information.

**Consequence if unhandled.** Every reload re-guesses dtypes, which is both slow and non-deterministic — a column that parses as `int64` on one day's file can parse as `object` on another because of a single malformed value. Reloading the full set from CSV on every experiment turns a two-minute iteration into a twenty-minute one.

**Handling.** Cleaned output is written to Parquet under `data/interim/`, one file per source day, and the splits under `data/processed/` are Parquet as well:

```python
frame.to_parquet(out_path, index=False)
```

Parquet reloads roughly ten times faster and preserves dtypes exactly. `pyarrow` is already a dependency.

### Label collapsing

Raw labels are specific (`DoS Hulk`, `DoS GoldenEye`, `FTP-Patator`, and so on). The project uses eight classes: `benign` plus the seven families in `ALERT_FAMILIES` in `backend/app/models.py`.

| Raw label(s) | Class |
| --- | --- |
| `BENIGN` | `benign` |
| `DoS Hulk`, `DoS GoldenEye`, `DoS slowloris`, `DoS Slowhttptest` | `dos` |
| `DDoS` | `ddos` |
| `FTP-Patator`, `SSH-Patator` | `brute_force` |
| `PortScan` | `port_scan` |
| `Web Attack - Brute Force`, `Web Attack - XSS`, `Web Attack - Sql Injection` | `web_attack` |
| `Bot` | `botnet` |
| `Infiltration` | `infiltration` |
| `Heartbleed` | decide and document (a Wednesday label with very few rows) |

Write the mapping as an explicit dictionary in code and log it. Log any raw label that did not match, and assert that every row received a class — an unmapped label must not become `NaN` and quietly vanish. The raw web-attack labels contain inconsistent dash characters in some distributions, so check the actual distinct values rather than assuming the strings above.

---

## Leakage control

Leakage here means a column that lets the model reach the answer by a shortcut instead of learning attack behaviour. The deny-list is defined once, in `backend/training/features.py`, and imported everywhere rather than retyped.

```python
LEAKAGE_COLUMNS: tuple[str, ...] = (
    "flow_id",
    "source_ip",
    "src_ip",
    "destination_ip",
    "dst_ip",
    "source_port",
    "src_port",
)

# Dropped after splitting, never used as a model input.
SPLIT_ONLY_COLUMNS: tuple[str, ...] = ("timestamp",)

PORT_COLUMN = "destination_port"
```

| Column | Why it is dropped |
| --- | --- |
| `flow_id` | A per-flow identifier. Carries no behaviour, and in a sorted capture it correlates with position in the file, which correlates with the attack schedule. |
| `source_ip` / `src_ip` | In this lab capture the attacker ran from a fixed machine. Left in, the model learns "traffic from 172.16.0.1 is an attack" — a perfect score on this dataset and a useless model on any real network. |
| `destination_ip` / `dst_ip` | The same failure from the other end: the victim hosts are a small fixed set, so the destination address encodes the attack schedule. |
| `source_port` / `src_port` | Ephemeral and attacker-tool-specific. It identifies the tool, not the behaviour, and will not transfer. |
| `timestamp` (`SPLIT_ONLY_COLUMNS`) | Required to build the temporal split, so it survives `split.py`. It must be dropped before training. A model that keeps it learns "attacks happen on Wednesday afternoon", which is the same failure in a different costume. |

`LEAKAGE_COLUMNS` holds seven entries rather than four because it covers both the long spellings used by the original CICIDS2017 CSVs and the short spellings (`src_ip`, `dst_ip`, `src_port`) that appear in corrected re-releases and in raw CICFlowMeter output. One deny-list then works whichever source the data came from, which matters because Phase 9 feeds live CICFlowMeter output through the same `features.py`.

### The destination_port decision

`destination_port` is deliberately **not** in the deny-list, and it is deliberately not given a default either. The comment in `features.py` states the tension directly: it is genuinely predictive and also a memorisation trap, so Phase 1 prepares both encodings and Phase 2 trains twice and reports both.

The port is real information — 80 is web, 22 is SSH, and attacks genuinely target specific services. It is also a memorisation trap, because in a lab capture the attacks were aimed at a small fixed set of ports, and a model can score well by memorising that set without learning anything about attack behaviour.

| Encoding | Definition | Recorded as |
| --- | --- | --- |
| A — raw | `destination_port` kept as an integer feature | `port_encoding = {"strategy": "raw"}` |
| B — bucketed | Service group (well-known 0–1023, registered 1024–49151, ephemeral 49152–65535) plus a one-hot column for each of the top 20 most frequent ports | `port_encoding = {"strategy": "bucketed", "top_ports": [80, 443, ...]}` |

The top-20 port list is computed from the **training split only**. Deriving it from the full dataset leaks test-set information into the feature definition, which is a subtler version of the same error the deny-list exists to prevent.

The decision rule for Phase 2 was stated in advance so the result could not be rationalised after the fact: **if raw port produces a large gain over bucketed, treat that gain as suspect and say so in writing.** A large gain means the model is memorising the lab's port assignments. Reporting that honestly is worth more than the higher number, and the `port_encoding` field in the bundle means the choice behind any given artifact is never ambiguous later.

### The result

Trained twice with LightGBM on Tuesday+Wednesday and scored on the Thursday validation day, everything but the encoding identical (`reports/port_ablation.md`):

| Encoding | Features | Validation PR-AUC | tau_sup | Attack recall at tau |
| --- | --- | --- | --- | --- |
| `raw` | 70 | 0.8724 | 0.2849 | 88.2% |
| `bucketed` | 92 | **0.8816** | 0.3879 | 87.6% |

Raw produces **no gain at all** — it is 1.0% worse — so the rule above never fires and nothing in this model rests on memorising which ports the lab happened to use.

Bucketed is what ships, and deliberately not because of that 1%, which is well inside noise. It ships because it asks what *kind* of service a flow hit rather than which port this particular capture assigned to it, and Phase 9 points the same model at a network whose assignments are nothing like CICIDS2017's. When two encodings measure the same, the one that generalises by construction is the one to pick.

The ablation is scored on the validation day rather than the test day on purpose: a feature-encoding decision made on the test day is a decision that has already spent the test day.

---

## Temporal splitting

The rule is absolute, and is listed on [Anti-Patterns](Anti-Patterns.md):

```python
train_test_split(X, y, shuffle=True)   # never, on this data
```

Flow records in this dataset are heavily duplicated and strongly correlated in time. A random split scatters near-identical rows across train and test, so the model is evaluated on what it effectively memorised. The scores it produces are fabricated, not merely optimistic.

Splitting by day avoids that, and it is better for a positive reason rather than only a defensive one: the model trains on earlier traffic and is tested on later traffic, which is how it would actually be deployed. Concept drift between Tuesday and Friday is part of the evaluation, not a nuisance.

```
Mon        Tue        Wed        Thu        Fri
 |          |          |          |          |
 |<-- benign_train: Mon + benign rows of Tue/Wed -->|
            |<---- train ---->|
                                  |  val  |
                                             | test |
```

`data/processed/benign_train.parquet` is written separately for Phase 3, and its contents must be asserted rather than assumed:

```python
assert (benign_train["label"] == "benign").all(), "attack rows leaked in"
```

If one attack row enters that file, Stage 2 quietly stops being a novel-attack detector and becomes a weak supervised model, and the project's central claim collapses without any error being raised. That assert is the cheapest insurance in the codebase.

### Verifying zero overlap

Duplicate removal happens before the split, but the property that matters is the one after it, so it is checked directly rather than inferred:

```python
overlap = pd.merge(train, test, how="inner")
assert len(overlap) == 0, f"{len(overlap)} rows shared between splits"
```

Run the same check for train against val, and val against test. Merging on all columns is a full-row equality join, which is exactly the property being asserted. On large frames, hashing each row's tuple is a faster equivalent. Do not substitute the reasoning "duplicates were dropped earlier, so this is fine" — the check is cheap and the failure it catches is invisible.

---

## Scaling

`RobustScaler`, not `StandardScaler`, fitted on train only:

```python
from sklearn.preprocessing import RobustScaler

scaler = RobustScaler()
scaler.fit(X_train)          # train only — never val, never test
X_val = scaler.transform(X_val)
X_test = scaler.transform(X_test)
```

**Why robust.** Network flow features are extremely heavy-tailed. A handful of enormous flows — one large file transfer — dominate the mean and the standard deviation, so `StandardScaler` compresses every ordinary value into a narrow band near zero and the model loses resolution in the region where almost all the data lives. `RobustScaler` centres on the median and scales by the interquartile range, so a few giant flows cannot flatten everything else. The same reasoning is recorded in the `build_feature_matrix` docstring in `features.py`.

**Why train-only.** Fitting on all the data lets test-set statistics influence the transform applied to training data. That is leakage — subtler than an IP column, but the same category of error, and it produces an optimistic test score that will not reproduce on new traffic. Fit once on train, then transform val, test and benign_train with that already-fitted scaler.

**What robust scaling does not fix, and what Phase 3 found.** An interquartile range can be *zero*, and when it is, scikit-learn leaves the divisor at 1.0 rather than dividing by nothing — so the column passes through essentially unscaled. CICIDS2017 has such columns. Over three quarters of benign flows report `idle_std` of exactly zero, which puts its 25th and 75th percentiles both at zero, while the flows that do idle report values up to 7.6 × 10⁷ microseconds.

That is harmless for a tree ensemble, which does not care what a column's units are, and it is why Stage 1 was unaffected. It is not harmless for anything that squares a distance. In the scaled matrix `idle_std` alone accounts for **93.9%** of the total squared magnitude, `active_std` for another 4.0%, and the top three columns for 98.7% between them — so an MSE objective over that matrix is a one-column objective, and the first Stage 2 run scored a ROC-AUC of 0.2337 on the validation-day arena — worse than a coin, and against 0.71 to 0.86 for three classical detectors on the same rows.

The fix belongs to Stage 2 rather than here. Changing the scaler would change the schema hash and force Stage 1 to be retrained for the benefit of a model that does not need it, so `training/autoencoder.py` compresses its own input with `sign(x) * log1p(|x|)` before the network sees it. The full argument, and the bound it clips at, are in [Models and Evaluation](ML-Models.md#the-input-transform-and-the-pathology-that-forced-it) and `reports/input_ablation.md`.

The general lesson is worth keeping: a scaler chosen for heavy tails still assumes the tail has a middle to be measured against. A column that is constant for most rows and enormous for the rest has no middle, and no per-column linear rescaling will give it one.

---

## The preprocessing bundle contract

Everything needed to reproduce the training-time feature matrix travels in one file, `backend/artifacts/preprocessing.pkl`. The structure is declared as a `TypedDict`, `PreprocessingBundle`, in `features.py`:

| Key | Type | Contents | Why it is in the bundle |
| --- | --- | --- | --- |
| `scaler` | fitted estimator | The `RobustScaler` fitted on train only | The transform cannot be recreated from data that no longer exists in the same form |
| `feature_order` | `list[str]` | The exact column order of the training matrix | Column order, not just membership, defines the matrix |
| `dropped_columns` | `list[str]` | Zero-variance columns plus the leakage deny-list actually applied | Lets a reader reconstruct what was removed, without rerunning the pipeline |
| `port_encoding` | `dict[str, Any]` | `{"strategy": "raw"}` or `{"strategy": "bucketed", "top_ports": [...]}` | Records which of the two encodings this artifact was built with |
| `schema_hash` | `str` | `sha256:<hex>` over `feature_order` | The startup check that turns silent skew into a refused boot |

Built and persisted with the helpers that already exist:

```python
from training.features import build_preprocessing_bundle, save_preprocessing_bundle

bundle = build_preprocessing_bundle(
    scaler=scaler,
    feature_order=list(X_train.columns),   # exact order
    dropped_columns=dropped,
    port_encoding=port_encoding,
)
save_preprocessing_bundle(bundle, settings.artifacts_path / "preprocessing.pkl")
```

`build_preprocessing_bundle` computes `schema_hash` itself, so the hash cannot be forgotten or hand-written out of step with the order it is supposed to describe. `load_preprocessing_bundle` is the matching reader; its docstring records that it is a first-party artifact only, written by `backend/training/` into a gitignored directory, with no code path that unpickles an uploaded or downloaded file.

### The schema hash, and the failure it exists to catch

```python
def compute_schema_hash(feature_order):
    payload = "\n".join(feature_order).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"sha256:{digest}"
```

Consider a model trained on a matrix whose first three columns are `flow_duration`, `total_fwd_packets`, `total_backward_packets`. Later the API assembles a request frame and — through a dictionary iteration order, a `reindex` against a stale list, or a `sort_index()` added for tidiness — hands the model `total_backward_packets`, `flow_duration`, `total_fwd_packets` instead.

Nothing fails. The array has the right shape and the right dtype. The scaler applies the median and IQR of `flow_duration` to packet counts. The forest walks its splits, comparing a packet count against a threshold learned for a microsecond duration, and returns a probability. The probability is confident and it is meaningless. Every downstream consumer — the fusion rule, the severity ranking, the dashboard — treats it as real. This is train/serve skew, and the reason it is called silent is precisely that there is no exception, no warning and no visible symptom until someone compares scores against ground truth by hand.

The hash makes it loud. Because it is computed over the joined column names in order, any reordering changes it, which is pinned by a test in `backend/tests/test_features.py`:

```python
assert compute_schema_hash(["a", "b"]) != compute_schema_hash(["b", "a"])
```

At startup, `ModelBundle._verify_schema_hash` in `backend/app/inference.py` recomputes the hash from the bundle's own `feature_order` and compares it to the persisted `schema_hash`. A mismatch — or a bundle with no hash at all — raises `SchemaHashMismatch` and the service refuses to serve. `model_card.json` carries the same hash and is checked against the preprocessing bundle too, so a model paired with the wrong preprocessing is caught as well. On success the log line reads:

```
schema hash verified: sha256:... (N features)
```

That log line is a Phase 1 acceptance criterion. A `SchemaHashMismatch` at startup means the bundle is internally inconsistent: rebuild it, do not work around the check.

### One implementation, two callers

`build_feature_matrix(frame, bundle=None)` is called by training to fit and by the API to apply, and that is the entire point — one implementation means the two paths cannot diverge. When `bundle` is `None` the caller is training and fitting; when a bundle is supplied the caller is applying a saved transform. The Phase 1 shape is:

```python
def build_feature_matrix(frame, bundle=None):
    # drop leakage columns
    # apply the port encoding
    # reindex to bundle["feature_order"]     <- order matters
    # apply bundle["scaler"].transform(...)
    # return the matrix
```

The constraint that goes with it: **do not reimplement any of this inside `backend/app/`.** A transform written in a route handler is the skew bug being born, and no amount of care keeps two copies in step across a refactor.

---

## Phase 1 checkpoint

Phase 1 is finished when all of the following are true and produced. It deliberately does **not** include training anything — no `.fit()` on a classifier happens in this phase.

### Deliverables

| Path | Contents |
| --- | --- |
| `data/interim/*.parquet` | Cleaned data, one file per source day |
| `data/processed/train.parquet` | Tuesday + Wednesday |
| `data/processed/val.parquet` | Thursday |
| `data/processed/test.parquet` | Friday |
| `data/processed/benign_train.parquet` | Monday in full plus benign rows from Tuesday and Wednesday |
| `backend/artifacts/preprocessing.pkl` | Scaler, feature order, dropped columns, port encoding, schema hash |

### Acceptance criteria — all met

Verified against the written Parquet files, not the in-memory frames, by re-reading them from disk after the run.

| # | Criterion | Result |
| --- | --- | --- |
| 1 | Row counts per split per class printed | The table below |
| 2 | Zero duplicate rows shared across splits | 0 rows shared by train/val, train/test or val/test |
| 3 | No NaN and no Inf survives | 0 of each, across all four files |
| 4 | `benign_train.parquet` contains zero attack rows | Only `BENIGN` present; asserted in code, which raises rather than warns |
| 5 | `preprocessing.pkl` exists and the backend still starts | Loads with `schema hash verified: sha256:ae1b67b1… (70 features)`, or `sha256:76724838… (92 features)` once Phase 2 has rewritten it with the champion's bucketed bundle |
| 6 | Cleaning decisions written down | On this page and in each module's docstring |
| 7 | Existing tests still pass | 159 pass; 85 of them are new in this phase |

### What cleaning removed

Across all eight published files:

| | Rows |
| --- | --- |
| Raw | 2,830,743 |
| After cleaning | **2,572,640** (258,103 dropped, 9.12%) |
| Exact duplicates removed | **255,236** |
| Dropped for non-finite rates | 2,867 (from 4,376 `Inf` values) |
| Negative durations and IATs clipped | 3,253 |
| Globally zero-variance columns dropped | 8 |

The duplicate count is the number to look at. A quarter of a million exact duplicates, 9.9% of the file, is what defect 3 above means in practice — and it is why shuffling instead of splitting temporally produces scores in the high nineties that mean nothing.

The eight zero-variance columns are `bwd_psh_flags`, `bwd_urg_flags`, `fwd_avg_bytes_bulk`, `fwd_avg_packets_bulk`, `fwd_avg_bulk_rate`, `bwd_avg_bytes_bulk`, `bwd_avg_packets_bulk` and `bwd_avg_bulk_rate`. Note that `fwd_urg_flags` and `cwe_flag_count` are **not** among them: they are constant on six of the eight days and vary on the other two, which is exactly why the assessment is made once across the whole dataset rather than per file.

### The table the phase must print

| class | train | val | test | benign_train |
| --- | --- | --- | --- | --- |
| BENIGN | 821,166 | 396,328 | 375,238 | 1,331,862 |
| DoS Hulk | 172,846 | 0 | 0 | 0 |
| DoS GoldenEye | 10,286 | 0 | 0 | 0 |
| FTP-Patator | 5,931 | 0 | 0 | 0 |
| DoS slowloris | 5,385 | 0 | 0 | 0 |
| DoS Slowhttptest | 5,228 | 0 | 0 | 0 |
| SSH-Patator | 3,219 | 0 | 0 | 0 |
| Heartbleed | 11 | 0 | 0 | 0 |
| Web Attack Brute Force | 0 | 1,470 | 0 | 0 |
| Web Attack XSS | 0 | 652 | 0 | 0 |
| Web Attack Sql Injection | 0 | 21 | 0 | 0 |
| Infiltration | 0 | 36 | 0 | 0 |
| DDoS | 0 | 0 | 128,014 | 0 |
| PortScan | 0 | 0 | 90,694 | 0 |
| Bot | 0 | 0 | 1,948 | 0 |
| **Total** | **1,024,072** | **398,507** | **595,894** | **1,331,862** |

The zeros are expected and correct: a temporal split means each attack family appears only on the day it was executed. That is the point — Phase 4 then tests whether the system catches families it never trained on. No family appears in both train and test.

A further **41,984** rows were removed as cross-split duplicates: rows that became byte-identical to a row in an earlier split once the splitting key was dropped. Those are precisely the duplicates that would have spanned a split boundary, and removing them is what makes criterion 2 hold.

### Three classes are too small to evaluate

| Class | Rows | Split |
| --- | --- | --- |
| Heartbleed | 11 | train |
| Web Attack Sql Injection | 21 | val |
| Infiltration | 36 | val |

This is the clearest thing the real run surfaced that the specification did not anticipate. No trustworthy per-class recall can come from eleven examples. Phase 2 should report these classes as under-powered rather than printing a precision and recall that a single misclassification would swing by ten points, and Phase 4's leave-one-attack-out table has the same problem for Heartbleed — holding out eleven rows and retraining measures almost nothing.

It is worth being clear that this is a property of the dataset, not a defect in the pipeline. CICIDS2017 executed Heartbleed once.

### The dataset actually used

The run above used the Kaggle mirror of the **MachineLearningCSV** release, fetched with `make data-fetch`, because the CIC distribution point sits behind a licence form that cannot be scripted. Two properties of that release shaped the code:

- CIC published it with `Flow ID`, both IP columns, `Source Port` **and `Timestamp`** already removed. Most of the leakage deny-list is therefore moot on this data — those columns never arrive, and `dropped_columns` in the bundle is empty. The deny-list still runs and still drops them when present. What the run verifies is the outcome: none of those columns appears in any split or in `feature_order`.
- With no timestamp, the temporal split had nothing to split on. The capture day is recovered from the file names instead, carried in a `capture_day` column, and dropped alongside the timestamp once the split is made.

Its web-attack labels also carry `U+FFFD` where the original cp1252 en dash was, which is why label collapsing above handles three separate stray characters rather than two.

Phase 1 stops at this table. Phase 2 begins as its own piece of work, after the table and the written decisions have been reviewed, because an unreviewed Phase 1 poisons everything built on top of it.
