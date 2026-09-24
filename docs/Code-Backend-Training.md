# Code Reference — Training Package

This page documents every module under `backend/training/`, the offline batch pipeline that turns raw CICIDS2017 CSVs into the artifacts the API loads at startup. Read it if you are implementing Phase 1 through Phase 4, if you need to know what `artifacts/preprocessing.pkl` is contractually required to contain, or if you are trying to understand why feature code lives in exactly one module and is imported by both the trainer and the request path.

Most of this package is stub today. Phase 0 of 9 is complete: the only executable logic here is in `features.py`, and everything else raises `NotImplementedError` naming the phase that implements it. That is deliberate — the stubs carry the design decisions in their docstrings so the specification cannot drift away from the code.

| File | Lines | Role |
| --- | --- | --- |
| `backend/training/__init__.py` | 5 | Package marker stating the training/serving separation |
| `backend/training/features.py` | 155 | The shared feature contract: normalisation, leakage lists, schema hash, bundle I/O |
| `backend/training/clean.py` | 32 | Phase 1 — CICIDS2017 defect handling (stub) |
| `backend/training/split.py` | 27 | Phase 1 — temporal train/validation/test splitting (stub) |
| `backend/training/train_supervised.py` | 42 | Phase 2 — Model A, the supervised classifier (stub) |
| `backend/training/train_autoencoder.py` | 35 | Phase 3 — Model B, the benign-only autoencoder (stub) |
| `backend/training/evaluate.py` | 22 | Phase 2/3 — metric emission (stub) |
| `backend/training/loao.py` | 28 | Phase 4 — leave-one-attack-out evaluation (stub) |

---

## backend/training/\_\_init\_\_.py

**Path:** `backend/training/__init__.py` — package marker whose docstring states the one architectural rule that governs the whole directory.

### What it does

The file contains no code, only a docstring. Its job is to make `training` an importable package so `from training.features import ...` works from both the trainer scripts and `backend/app/inference.py`, and to record the boundary the rest of the package depends on: nothing in this package is imported by a request handler, training is batch, and the API only ever loads the artifacts the package produces.

That rule is what keeps `.fit()` out of an endpoint — listed in [Anti-Patterns](Anti-Patterns.md) as a serving-architecture failure. There is exactly one import that crosses the boundary, and it runs the other way: `app/inference.py` imports `compute_schema_hash` from `training.features`. Importing the feature contract is the point; importing a trainer would not be.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| module docstring | Documentation | `"""Offline training pipeline. ..."""` | States that nothing in the package is imported by a request handler and that the API only consumes produced artifacts |

### Notes

- The package is declared to the build in `backend/pyproject.toml` under `[tool.hatch.build.targets.wheel]` as `packages = ["app", "training"]`, so `training` ships alongside `app` rather than being a loose script directory.
- Tests import from it directly (`from training.features import ...` in `backend/tests/test_features.py`), which works because `[tool.pytest.ini_options]` sets `pythonpath = ["."]`.
- Status: implemented. It is a docstring-only file and needs nothing further.

---

## backend/training/features.py

**Path:** `backend/training/features.py` — the single module in which feature transforms are allowed to live, imported by both the training scripts and the serving path.

### What it does

This is the most important file in the repository, and the reason is stated in its own docstring: reimplementing any of it inside the API is the train/serve skew failure mode, *which is silent*. If the API hands the model the right columns in the wrong order, scikit-learn does not raise; it multiplies the wrong numbers by the wrong coefficients and returns a confident, meaningless probability. There is no exception, no log line and no crash — just wrong answers that look exactly like right answers. Every other defence in the codebase can be added later; this one has to exist before there is a model, because once there is a model the bug is undetectable by inspection.

The module answers that with two mechanisms. The first is structural: one module, imported by both sides. `backend/app/inference.py:21` reads `from training.features import compute_schema_hash` — one symbol, and today the only one that crosses the boundary. `build_feature_matrix` joins that same import in Phase 1, when the serving path has a matrix to build. There is no second copy of the transform logic to drift. The second is a checksum: `compute_schema_hash` hashes the exact feature order a model was trained against, that hash is persisted into `artifacts/preprocessing.pkl` alongside the scaler, and `ModelBundle._verify_schema_hash` recomputes it at startup. A mismatch raises `SchemaHashMismatch`, which is fatal in the FastAPI lifespan. The silent scoring bug becomes a refused startup.

Phase 0 implements everything that does not need the dataset in hand: column-name normalisation, the leakage deny-list, the schema hash, and bundle construction, save and load. The transforms themselves — dropping leakage columns, applying the port encoding, reindexing to `feature_order`, applying the fitted `RobustScaler` — land in Phase 1. `pandas` is imported only under `TYPE_CHECKING`, so the module stays cheap to import inside the API process.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `SCHEMA_HASH_PREFIX` | Constant | `SCHEMA_HASH_PREFIX = "sha256"` | Algorithm prefix on every emitted hash; produces strings of the form `sha256:<hexdigest>` |
| `LEAKAGE_COLUMNS` | Constant | `LEAKAGE_COLUMNS: tuple[str, ...] = ("flow_id", "source_ip", "src_ip", "destination_ip", "dst_ip", "source_port", "src_port")` | Identity columns dropped before training; they memorise the lab's addressing scheme instead of attack behaviour. Both the CICIDS2017 spellings and the abbreviated forms are listed so either naming is caught |
| `SPLIT_ONLY_COLUMNS` | Constant | `SPLIT_ONLY_COLUMNS: tuple[str, ...] = ("timestamp",)` | Columns kept only long enough to produce the temporal split, then dropped; never a model input |
| `PORT_COLUMN` | Constant | `PORT_COLUMN = "destination_port"` | Deliberately **absent** from `LEAKAGE_COLUMNS`. It is genuinely predictive and also a memorisation trap, so Phase 1 trains twice (raw port vs. bucketed service groups) and reports both |
| `_NON_ALNUM` | Constant (private) | `_NON_ALNUM = re.compile(r"[^0-9a-z]+")` | Compiled pattern matching any run of characters that is not a lowercase alphanumeric; the substitution engine behind `normalise_column_name` |
| `normalise_column_name` | Function | `normalise_column_name(name: str) -> str` | Normalises one raw CICIDS2017 header to snake_case: strip, lowercase, replace non-alphanumeric runs with `_`, strip leading and trailing underscores |
| `normalise_columns` | Function | `normalise_columns(columns: list[str]) -> list[str]` | Applies `normalise_column_name` across a header list, preserving order |
| `compute_schema_hash` | Function | `compute_schema_hash(feature_order: list[str] \| tuple[str, ...]) -> str` | Joins the feature order with `\n`, UTF-8 encodes it, SHA-256 digests it and returns `f"{SCHEMA_HASH_PREFIX}:{digest}"`. Order-sensitive by construction |
| `PreprocessingBundle` | TypedDict | `class PreprocessingBundle(TypedDict)` | The required contents of `artifacts/preprocessing.pkl`. Keys: `scaler: Any`, `feature_order: list[str]`, `dropped_columns: list[str]`, `port_encoding: dict[str, Any]`, `schema_hash: str` |
| `build_preprocessing_bundle` | Function | `build_preprocessing_bundle(scaler: Any, feature_order: list[str], dropped_columns: list[str], port_encoding: dict[str, Any]) -> PreprocessingBundle` | Assembles a bundle, copying the mutable inputs and deriving `schema_hash` from `feature_order` so the two can never be set independently |
| `save_preprocessing_bundle` | Function | `save_preprocessing_bundle(bundle: PreprocessingBundle, path: Path) -> Path` | Creates the parent directory if needed and pickles the bundle with `protocol=pickle.HIGHEST_PROTOCOL`; returns the written path |
| `load_preprocessing_bundle` | Function | `load_preprocessing_bundle(path: Path) -> PreprocessingBundle` | Unpickles a locally produced bundle and returns it unchecked — no key validation on the way out. Carries `# noqa: S301 - first-party artifact`, which records a trust boundary rather than silencing an enabled rule (see the Notes): there is no code path that unpickles an uploaded or downloaded file |
| `build_feature_matrix` | Function | `build_feature_matrix(frame: pd.DataFrame, bundle: PreprocessingBundle \| None = None)` | Turns cleaned flow records into the model's input matrix. Called by training to fit the scaler and by serving to apply it — that shared call is what keeps the two paths identical. **Stub** |

### The normalisation rule, by example

The published CSVs carry leading and trailing whitespace in their headers, along with slashes and dots. The docstring examples are executable and are mirrored by parametrised cases in `backend/tests/test_features.py`:

```python
>>> normalise_column_name(" Flow Duration")
'flow_duration'
>>> normalise_column_name("Flow Bytes/s")
'flow_bytes_s'
>>> normalise_column_name("Fwd Header Length.1")
'fwd_header_length_1'
```

The third case matters more than it looks: CICIDS2017 ships a genuine duplicate header disambiguated only by a `.1` suffix. Normalising first is what makes every later column reference reliable, because `" Flow Duration"` is not `"Flow Duration"` and the difference is invisible when reading the file.

### The schema hash, and why it is order-sensitive

```python
>>> compute_schema_hash(["a", "b"]) == compute_schema_hash(["b", "a"])
False
```

That single assertion is the whole mechanism. Hashing a *set* of column names would catch a missing or extra feature but would pass a reordered matrix — and a reordered matrix is exactly the failure that produces garbage scores without raising. Joining with a newline before hashing makes position part of the digest.

The `sha256:` prefix is not decoration. It makes the stored value self-describing, so a future move to a different digest can be detected rather than guessed at, and it is asserted directly in `test_schema_hash_is_stable_and_prefixed`.

### The `preprocessing.pkl` contract

`artifacts/preprocessing.pkl` must contain exactly these five keys:

```python
{
    "scaler": fitted_scaler,       # RobustScaler, fit on train only
    "feature_order": [...],        # exact column order
    "dropped_columns": [...],
    "port_encoding": {...},
    "schema_hash": "sha256:...",   # checked at startup
}
```

The `PreprocessingBundle` docstring states why they travel together: any one of them on its own is not enough to reproduce the training-time feature matrix. A scaler without the column order cannot be applied. A column order without the scaler gives unscaled inputs. A hash without either proves nothing. `build_preprocessing_bundle` is the only supported way to construct one, and it derives the hash rather than accepting it, which removes the possibility of writing a bundle whose hash and feature order disagree from the start.

The consuming side is `ModelBundle.load` in `backend/app/inference.py`, which reads the five keys, calls `_verify_schema_hash`, and raises `SchemaHashMismatch` both when the hash is absent (`"carries no schema_hash; refusing to serve"`) and when the recomputed value differs (`"The bundle is inconsistent -- retrain rather than serve it."`). `_load_model_card` performs a second cross-check: if `model_card.json` carries its own `schema_hash` and it disagrees with the one in the pickle, that also raises.

Note the asymmetry: `ModelBundle.load` does not call `load_preprocessing_bundle`. It opens the pickle itself (`backend/app/inference.py:113-114`) and reads each key defensively with `.get()`, so a bundle missing `feature_order` degrades to an empty list rather than raising there — the schema hash is what makes the inconsistency loud. `save_preprocessing_bundle` and `load_preprocessing_bundle` have no caller in `backend/app/` at all today; their only call site is the round-trip assertion in `backend/tests/test_features.py`, and Phase 1's `split.py` becomes the first writer.

None of this exists on disk yet. `backend/artifacts/` contains only its `README.md` and a `.gitkeep`; no `preprocessing.pkl` has ever been written, because `build_feature_matrix` is a stub and `split.py` — the module that will fit the scaler and persist the bundle — raises. `ModelBundle.load` treats the missing file as the expected Phase 0 state: it logs `no artifact bundle in <dir> -- serving with model_version=unloaded (expected until Phase 2 trains a model)` and returns, which is why `GET /api/v1/health` reports `model_version: "unloaded"` today. The contract above is specified and unwritten.

### Notes

- `RobustScaler`, not `StandardScaler`. Flow features are heavy-tailed enough that a handful of enormous flows would flatten everything else under standard scaling. The choice is recorded in the `build_feature_matrix` docstring.
- `destination_port` is the one column with a documented open decision. Leaving it out of `LEAKAGE_COLUMNS` is asserted in `test_destination_port_is_not_silently_dropped`, because dropping it by default would quietly skip the ablation the specification requires.
- `LEAKAGE_COLUMNS` lists both `source_ip`/`src_ip` and `destination_ip`/`dst_ip` because normalised CICIDS2017 headers and the abbreviated field names used elsewhere in the project are both plausible inputs; listing both is cheaper than a lookup table.
- Mutable inputs are copied on the way into the bundle (`list(feature_order)`, `dict(port_encoding)`), so a caller mutating its own list after the call cannot desynchronise the persisted feature order from the persisted hash.
- `pandas` is a `TYPE_CHECKING`-only import guarded with `# pragma: no cover - typing only`, and `from __future__ import annotations` makes the `pd.DataFrame` annotation on `build_feature_matrix` a string at runtime.
- `PreprocessingBundle` is a `TypedDict`, which means it is a plain `dict` at runtime — there is no validation of the five keys on write or on read, and `load_preprocessing_bundle` returns whatever was pickled. The one invariant that is actually enforced is that `build_preprocessing_bundle` *derives* `schema_hash` from `feature_order` rather than accepting it as an argument, so a bundle built through the supported path cannot be internally inconsistent from the start. A bundle assembled by hand can be, and the startup recompute in `ModelBundle._verify_schema_hash` is what catches it.
- The `# noqa: S301` on the unpickle is documentary, not functional: `[tool.ruff.lint]` in `backend/pyproject.toml` selects `E`, `F`, `I`, `UP`, `B` and `W`, so the flake8-bandit `S` rules are not enabled and nothing is being suppressed. It records the trust boundary — artifacts are first-party output of `backend/training/` into a gitignored directory, and no code path unpickles an uploaded or downloaded file — in the place the boundary is crossed. The same comment appears on the Stage 1 unpickle in `backend/app/inference.py:183` for the same reason.
- Wiring: this is the only module in `backend/training/` that anything else imports. `app/inference.py:21` takes `compute_schema_hash` from it and `backend/tests/test_features.py` takes eight names from it; no module in `backend/training/` imports it yet, because none of them has a transform to call.
- Status: **partially implemented**. `LEAKAGE_COLUMNS`, `PORT_COLUMN`, `normalise_column_name`, `normalise_columns`, `compute_schema_hash`, `build_preprocessing_bundle`, `save_preprocessing_bundle` and `load_preprocessing_bundle` ship and are pinned by `backend/tests/test_features.py` — those eight names are exactly what that module imports. `SCHEMA_HASH_PREFIX` is covered only indirectly: `test_schema_hash_is_stable_and_prefixed` asserts the literal `"sha256:"` rather than importing the constant. `SPLIT_ONLY_COLUMNS` and the private `_NON_ALNUM` pattern ship but have no test naming them — `_NON_ALNUM` is exercised through `normalise_column_name`, while `SPLIT_ONLY_COLUMNS` is referenced nowhere else in the repository yet and gets its first consumer in Phase 1's `split.py`. `PreprocessingBundle` is exercised only as the return type of `build_preprocessing_bundle`. `build_feature_matrix` is a stub — it raises `NotImplementedError("build_feature_matrix is implemented in Phase 1 (data and features).")`.

---

## backend/training/clean.py

**Path:** `backend/training/clean.py` — Phase 1 entry point that repairs the documented defects in the published CICIDS2017 CSVs before anything else touches them.

### What it does

CICIDS2017 is not clean data with a few rough edges; it has specific, catalogued defects that have produced a body of published work with inflated scores. This module's docstring enumerates them and commits to handling each one explicitly rather than papering over it, because the most damaging defect — exact duplicate rows — is invisible in every downstream metric and makes a broken pipeline look excellent.

The module currently holds only that catalogue and a `main` that raises. That is intentional: the cleaning rules are decisions, and recording them in the module that will implement them keeps the decision next to the code rather than in a document that rots.

Output goes to `data/interim` as Parquet, not CSV. Parquet is faster to reload and preserves dtypes, which matters because the Inf-to-NaN repair in defect 2 depends on the float columns still being floats. `pyarrow>=17.0` is declared in `backend/pyproject.toml` specifically as the "Parquet writer for data/interim (Phase 1)".

### The six documented defects

These are the defects listed in `BUILD_PROMPT.md` Part 4 that `clean.py` must handle. The module docstring numbers the first five and states the sixth as its output rule.

| # | Defect | Required handling |
| --- | --- | --- |
| 1 | Column names carry leading and trailing whitespace — `" Flow Duration"` is not `"Flow Duration"` | Strip and snake_case everything first, via `features.normalise_column_name` |
| 2 | `flow_bytes_s` and `flow_packets_s` contain `Inf` and `NaN` from zero-duration flows | Replace `Inf` with `NaN`, then decide drop vs. impute and document the choice |
| 3 | Massive exact-duplicate rows | Drop before splitting. Failing to do this is the single largest source of inflated scores published on this dataset |
| 4 | Zero-variance columns — `bwd_psh_flags`, `fwd_urg_flags` and others are all-zero | Drop programmatically and log which ones went |
| 5 | Negative values in some duration and IAT columns | Clip at zero or drop, and log the count |
| 6 | CSV is the wrong interchange format for the interim stage | Write cleaned output to Parquet, not CSV — faster to reload and it preserves dtypes |

Defect 3 is the one that decides whether the project's numbers mean anything. Duplicate flows that survive into both train and test turn memorisation into apparent generalisation, which is why the specification pairs this rule with the ban on `train_test_split(shuffle=True)` in [Anti-Patterns](Anti-Patterns.md).

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 1 cleaning entry point. **Stub** — raises `NotImplementedError("clean.py is implemented in Phase 1 (data and features).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

### Notes

- Defects 4 and 5 both require *logging what was removed*, not just removing it. A dropped-column list that is not recorded cannot be reconciled against `dropped_columns` in the preprocessing bundle.
- The docstring also carries a documentation obligation: note in the README that the original CICIDS2017 labels contain documented errors and that corrected re-releases exist.
- Cleaning runs before splitting, never after. Deduplicating after a split cannot remove a duplicate that has already been separated across the boundary.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 1.

---

## backend/training/split.py

**Path:** `backend/training/split.py` — Phase 1 entry point producing the temporal train, validation and test splits.

### What it does

CICIDS2017 was captured over five consecutive weekdays, each with a different attack profile, and that structure is the split. Days are assigned to roles; rows are never shuffled between them. The alternative — `train_test_split(shuffle=True)` — leaks near-identical duplicated flows across train and test and manufactures fake 99.9% scores. That failure mode is listed first in [Anti-Patterns](Anti-Patterns.md), and this module exists to make the correct behaviour the only available one.

The day-to-role mapping is recorded in the module docstring:

| Day | Content | Role |
| --- | --- | --- |
| Monday | Benign only | Autoencoder train |
| Tuesday | FTP-Patator, SSH-Patator | Train |
| Wednesday | DoS Hulk/GoldenEye/Slowloris/Slowhttptest, Heartbleed | Train |
| Thursday | Web attacks (AM), infiltration (PM) | Validation |
| Friday | Botnet, port scan, DDoS | Test |

Monday being benign-only is what makes Stage 2 possible at all: it is a label-contamination-free training set for the autoencoder, obtained without any filtering that could go wrong. Friday holding botnet, port scan and DDoS means the test day contains families the validation day does not, so a model tuned on Thursday is genuinely being asked about traffic it was not tuned against.

Temporal splitting is also what makes `SPLIT_ONLY_COLUMNS` coherent. `timestamp` is needed to assign a row to a day and is worthless — actively harmful — as a model input, so it survives exactly until this module has used it.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 1 splitting entry point. **Stub** — raises `NotImplementedError("split.py is implemented in Phase 1 (data and features).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

### Notes

- The phase checkpoint is stated in the docstring as three assertions: row counts per split per class, zero duplicate rows shared across splits, and no `NaN` or `Inf` surviving.
- The zero-shared-duplicates check is the one that catches a regression in `clean.py` defect 3. Running it here rather than there means it checks the property that actually matters — duplicates *across the split boundary* — rather than the operation that was supposed to produce it.
- `backend/artifacts/README.md` lists `preprocessing.pkl` as written by `split.py` / `features.py` in Phase 1, so this module is also where the scaler is fit on train only and the bundle is persisted.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 1.

---

## backend/training/train_supervised.py

**Path:** `backend/training/train_supervised.py` — Phase 2 entry point training Model A, the supervised multi-class classifier that names known attack families.

### What it does

Model A answers "which named attack is this?" and produces `artifacts/supervised_model.pkl`. The module docstring fixes the order of work, and the order is the point: a `RandomForestClassifier` baseline with `n_estimators=300` and `max_depth` tuned against the validation day is committed as a running baseline *before* anything else is touched. Only once that baseline runs end to end and has been evaluated does LightGBM arrive, as a swap-in upgrade with the RandomForest artifact kept as a fallback.

Doing it in that order buys a working, honestly evaluated model on day one, on CPU, with no GPU setup — and it means that if the LightGBM upgrade regresses, there is something to compare against and fall back to. Starting with LightGBM would leave no baseline and no way to tell whether the gradient booster earned anything.

Class imbalance is handled with `class_weight` balanced or explicit per-class weights, and explicitly **not** with SMOTE. Synthetic interpolation between flow records invents packets that could not exist on a real network; applying it before the split puts synthetic rows in the test set and makes the scores meaningless outright. If SMOTE is demonstrated at all it is as an ablation that underperforms.

### The false-positive-budget threshold

`tau_sup` comes from an alert budget, never from `argmax` or a default of 0.5:

```text
max_alerts_per_day = analyst_capacity_per_hour * analyst_shift_hours
target_fpr         = max_alerts_per_day / expected_daily_flow_volume
tau_sup            = smallest threshold where FPR(tau) <= target_fpr
```

All three inputs are configured through the environment and exposed on `Settings` in `backend/app/config.py`: `expected_daily_flow_volume` (default `1_000_000`), `analyst_capacity_per_hour` (default `40`) and `analyst_shift_hours` (default `8`), each declared with `gt=0`. `Settings.max_alerts_per_day` and `Settings.target_fpr` are computed properties, and `backend/tests/test_config.py::test_false_positive_budget_arithmetic` pins the arithmetic at 320 alerts per day and a target FPR of `3.2e-4` for those defaults. See [Configuration](Configuration.md).

This reframes the threshold from an arbitrary constant into a statement about how many alerts a shift can actually triage. `tau_sup` is persisted into the artifact bundle, and `ModelBundle._load_model_card` reads it back from `model_card.json` under `thresholds.tau_sup`.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 2 training entry point. **Stub** — raises `NotImplementedError("train_supervised.py is implemented in Phase 2 (supervised classifier).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

### Notes

- Target classes: `benign`, `dos`, `ddos`, `brute_force`, `port_scan`, `web_attack`, `botnet`, `infiltration`. Rare sub-families collapse into these, and the collapse mapping is logged rather than left implicit.
- Early stopping is against the validation day (Thursday), which is also the day `max_depth` is tuned on — the test day (Friday) is touched once, at the end.
- `scikit-learn>=1.5` and `lightgbm>=4.5` are both declared in `backend/pyproject.toml`, annotated there as "Model A baseline: RandomForestClassifier" and "Model A upgrade" respectively.
- The artifact is `artifacts/supervised_model.pkl`, loaded by `ModelBundle._load_models` from the filename constant `SUPERVISED_FILE`.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 2.

---

## backend/training/train_autoencoder.py

**Path:** `backend/training/train_autoencoder.py` — Phase 3 entry point training Model B, the benign-only anomaly detector that gives the project its novel-attack claim.

### What it does

Model B answers "how unlike normal traffic is this?" and produces `artifacts/autoencoder.pt` as a state dict. It trains on benign rows only: Monday in full, plus the benign rows from Tuesday and Wednesday. The docstring is explicit that attack rows must never enter this training set and that the exclusion must be *asserted in code rather than merely intended* — that assertion is what makes the novel-attack claim real instead of a relabelled supervised model. If attack rows leak into the benign training set, the model learns to reconstruct those attacks, stops flagging them, and the leave-one-attack-out numbers in Phase 4 become meaningless in a way no downstream metric reveals.

The architecture is a symmetric bottleneck:

```text
input(d) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(d)
ReLU, MSE loss, Adam, early stopping on benign validation loss
Dropout 0.1 in the encoder; batch norm helps convergence here
```

The 16-unit bottleneck is the mechanism: a network that can only carry sixteen numbers through the middle has to learn the structure of normal traffic, and traffic that does not share that structure reconstructs badly. The score for a row is its mean squared reconstruction error.

`tau_anom` is the 99.5th percentile of reconstruction error on held-out benign validation data — set from the benign distribution alone, never from attack data. The full benign error distribution is persisted as histogram bins, not raw rows, because both the dashboard's interactive threshold slider and drift detection read it and neither needs per-row data. `ModelBundle` holds it as `benign_error_histogram`.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 3 training entry point. **Stub** — raises `NotImplementedError("train_autoencoder.py is implemented in Phase 3 (anomaly detector).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

### Notes

- Baselines to run on the same split: `IsolationForest`, `LOF` and `ECOD` from PyOD. They establish that the autoencoder earns its complexity, and if one of them wins, that is a finding to report rather than hide.
- Stage 2 explanation does not use SHAP. The per-feature reconstruction error is already computed, so the features the model failed hardest to reconstruct are, directly, why the row looks anomalous. TreeSHAP is for Stage 1 only.
- The artifact is a state dict, not a pickled module. `ModelBundle` holds it as `autoencoder_state` and its comment notes that Phase 3 owns the `nn.Module` definition and reconstructs the model from it; torch weights are read with `weights_only=True` so the file is data rather than code.
- `torch>=2.4` is declared in `backend/pyproject.toml` as "Model B: autoencoder". `requires-python = ">=3.11,<3.13"` is pinned partly because 3.13/3.14 lack settled wheels for this stack.
- The phase checkpoint is a histogram of benign vs. attack reconstruction error with the threshold line drawn. If the distributions do not visibly separate, the model is not working and no dashboard hides that.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 3.

---

## backend/training/evaluate.py

**Path:** `backend/training/evaluate.py` — Phase 2/3 entry point emitting the metric set the project is willing to be judged on.

### What it does

This module exists to make one class of dishonesty impossible by default. On traffic that is 99% benign, a model that always answers "benign" scores 99% accuracy, so accuracy may appear in a table but never as a headline number. PR-AUC is the headline instead.

The emitted set is: per-class precision, recall, F1 and support; the confusion matrix; PR and ROC curves rendered side by side; PR-AUC as the headline; the FPR at the chosen threshold; and projected alerts per analyst per hour. That last figure closes the loop with the false-positive budget in `train_supervised.py` — it is the budget's prediction checked against the model's actual behaviour.

Rendering PR and ROC side by side is deliberate rather than completionist. The gap between the two curves *is* the explanation for why ROC-AUC flatters an imbalanced classifier, and showing both makes the argument visible instead of asserted.

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 2/3 evaluation entry point. **Stub** — raises `NotImplementedError("evaluate.py is implemented in Phase 2 (supervised classifier).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

### Notes

- `backend/artifacts/README.md` lists `model_card.json` as written by `evaluate.py` in Phase 2/3. That file carries `version`, `schema_hash` and the `thresholds` block into the serving path — `ModelBundle._load_model_card` reads `thresholds.tau_sup` and `thresholds.tau_anom` from it and raises `SchemaHashMismatch` if its `schema_hash` disagrees with the one in `preprocessing.pkl`.
- The Phase 2 checkpoint attached to this module is a classification report on the held-out test day *plus a written paragraph* naming which classes the model handles poorly and why. The prose is part of the deliverable.
- The `NotImplementedError` message names Phase 2 even though the module also serves Phase 3, because Phase 2 is when it first has to run.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 2.

---

## backend/training/loao.py

**Path:** `backend/training/loao.py` — Phase 4 entry point running the leave-one-attack-out evaluation, the project's headline result.

### What it does

Every claim the project makes about detecting attacks it was never trained on reduces to this procedure. For each attack family F:

1. Remove all F rows from supervised training.
2. Retrain the supervised model.
3. Leave the autoencoder untouched — it never saw any attack rows anyway.
4. Run the full fusion pipeline over a test set containing F.
5. Record what fraction of F was flagged, and by which stage.

Step 3 is why the experiment is valid. The autoencoder is trained on benign traffic only, so there is nothing to remove from it; holding F out of Stage 1 alone produces a system that has genuinely never seen F in any supervised form, while Stage 2 remains exactly the model that ships. Step 5's "by which stage" split is what separates a measurement from an anecdote: Stage 1 recall on a held-out family is expected to be near zero, and anything Stage 2 catches is caught without ever having been told what it is.

The output table is committed as `reports/loao.md`:

| Column | Meaning |
| --- | --- |
| Held-out family | The attack family removed from supervised training for this run |
| Caught by Stage 1 | Fraction of F flagged by the supervised classifier despite the hold-out |
| Caught by Stage 2 | Fraction of F flagged by the autoencoder — the headline number |
| Total recall | Fraction of F flagged by either stage |
| Missed | Fraction of F that produced no alert at all |

### Key symbols

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 4 LOAO entry point. **Stub** — raises `NotImplementedError("loao.py is implemented in Phase 4 (fusion and LOAO).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

### Notes

- The `Missed` column is not optional and is not to be quietly dropped. The docstring states the reasoning directly: a table with a real Missed column reads as credible engineering, a table of 99s reads as a bug.
- The claim this table supports is bounded. LOAO measures generalisation to held-out *known* attacks, which is a proxy for genuinely novel ones, not proof — and that limitation belongs in the README rather than in a footnote.
- Retraining once per family makes this the most expensive job in the pipeline; it is batch, offline, and has no interaction with the API.
- The fusion logic it exercises is Phase 4's, in `backend/app/inference.py`, not a separate copy — running LOAO against a reimplementation of fusion would measure the reimplementation.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 4.
