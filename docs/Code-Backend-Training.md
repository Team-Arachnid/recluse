# Code Reference — Training Package

This page documents every module under `backend/training/`, the offline batch pipeline that turns raw CICIDS2017 CSVs into the artifacts the API loads at startup.pkl` is contractually required to contain, or if you are trying to understand why feature code lives in exactly one module and is imported by both the trainer and the request path.

Phases 1 and 2 are complete. `clean.py`, `split.py`, `preprocess.py`, `console.py` and the transforms in `features.py` run end to end through `make data`; `labels.py`, `metrics.py`, `estimators.py`, `train_supervised.py` and `evaluate.py` run end to end through `make train` and `make train-lgbm`. All of it has been run against the real 2.83M-record CICIDS2017 release — the measured results are in [Roadmap](Roadmap.md#measured-on-the-real-release) and [Roadmap](Roadmap.md#phase-2--supervised-classifier). Phases 3 and 4 remain docstring-only stubs that raise `NotImplementedError` naming the phase that implements them, which is deliberate: the stubs carry the design decisions so the specification cannot drift away from the code.

| File | Lines | Role |
| --- | --- | --- |
| `backend/training/__init__.py` | 5 | Package marker stating the training/serving separation |
| `backend/training/features.py` | 307 | The shared feature contract: normalisation, leakage lists, port encodings, schema hash, the feature matrix, bundle I/O |
| `backend/training/clean.py` | 393 | Phase 1 — CICIDS2017 defect handling |
| `backend/training/split.py` | 272 | Phase 1 — temporal train/validation/test splitting |
| `backend/training/preprocess.py` | 176 | Phase 1 — fits the scaler on train only and persists the bundle |
| `backend/training/console.py` | 37 | Phase 1 — report output that degrades rather than crashing on a cp1252 console |
| `backend/training/labels.py` | 200 | Phase 2 — the class collapse, the support floor, and the mapping log |
| `backend/training/metrics.py` | 367 | Phase 2 — threshold arithmetic and the quantities Stage 1 is judged on |
| `backend/training/estimators.py` | 55 | Phase 2 — the LightGBM wrapper, in a module that is never run as a script |
| `backend/training/train_supervised.py` | 824 | Phase 2 — Model A: baseline, upgrade, threshold, promotion, port ablation |
| `backend/training/evaluate.py` | 651 | Phase 2/3 — the held-out test day and the write-up |
| `backend/training/train_autoencoder.py` | 35 | Phase 3 — Model B, the benign-only autoencoder (stub) |
| `backend/training/loao.py` | 28 | Phase 4 — leave-one-attack-out evaluation (stub) |

---

## backend/training/\_\_init\_\_.py

Package marker whose docstring states the one architectural rule that governs the whole directory.

The file contains no code, only a docstring. Its job is to make `training` an importable package so `from training.features import ...` works from both the trainer scripts and `backend/app/inference.py`, and to record the boundary the rest of the package depends on: nothing in this package is imported by a request handler, training is batch, and the API only ever loads the artifacts the package produces.

That rule is what keeps `.fit()` out of an endpoint — listed in [Anti-Patterns](Anti-Patterns.md) as a serving-architecture failure. There is exactly one import that crosses the boundary, and it runs the other way: `app/inference.py` imports `compute_schema_hash` from `training.features`. Importing the feature contract is the point; importing a trainer would not be.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| module docstring | Documentation | `"""Offline training pipeline. ..."""` | States that nothing in the package is imported by a request handler and that the API only consumes produced artifacts |

- The package is declared to the build in `backend/pyproject.toml` under `[tool.hatch.build.targets.wheel]` as `packages = ["app", "training"]`, so `training` ships alongside `app` rather than being a loose script directory.
- Tests import from it directly (`from training.features import ...` in `backend/tests/test_features.py`), which works because `[tool.pytest.ini_options]` sets `pythonpath = ["."]`.
- Status: implemented. It is a docstring-only file and needs nothing further.

---

## backend/training/features.py

The single module in which feature transforms are allowed to live, imported by both the training scripts and the serving path.

This is the most important file in the repository, and the reason is stated in its own docstring: reimplementing any of it inside the API is the train/serve skew failure mode, *which is silent*. If the API hands the model the right columns in the wrong order, scikit-learn does not raise; it multiplies the wrong numbers by the wrong coefficients and returns a confident, meaningless probability. There is no exception, no log line and no crash — just wrong answers that look exactly like right answers. Every other defence in the codebase can be added later; this one has to exist before there is a model, because once there is a model the bug is undetectable by inspection.

The module answers that with two mechanisms. The first is structural: one module, imported by both sides. `backend/app/inference.py:21` reads `from training.features import compute_schema_hash` — one symbol, and today the only one that crosses the boundary. `build_feature_matrix` joins that same import in Phase 1, when the serving path has a matrix to build. There is no second copy of the transform logic to drift. The second is a checksum: `compute_schema_hash` hashes the exact feature order a model was trained against, that hash is persisted into `artifacts/preprocessing.pkl` alongside the scaler, and `ModelBundle._verify_schema_hash` recomputes it at startup. A mismatch raises `SchemaHashMismatch`, which is fatal in the FastAPI lifespan. The silent scoring bug becomes a refused startup.

Phase 0 implements everything that does not need the dataset in hand: column-name normalisation, the leakage deny-list, the schema hash, and bundle construction, save and load. The transforms themselves — dropping leakage columns, applying the port encoding, reindexing to `feature_order`, applying the fitted `RobustScaler` — land in Phase 1. `pandas` is imported only under `TYPE_CHECKING`, so the module stays cheap to import inside the API process.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `SCHEMA_HASH_PREFIX` | Constant | `SCHEMA_HASH_PREFIX = "sha256"` | Algorithm prefix on every emitted hash; produces strings of the form `sha256:<hexdigest>` |
| `LEAKAGE_COLUMNS` | Constant | `LEAKAGE_COLUMNS: tuple[str, ...] = ("flow_id", "source_ip", "src_ip", "destination_ip", "dst_ip", "source_port", "src_port")` | Identity columns dropped before training; they memorise the lab's addressing scheme instead of attack behaviour. Both the CICIDS2017 spellings and the abbreviated forms are listed so either naming is caught |
| `SPLIT_ONLY_COLUMNS` | Constant | `SPLIT_ONLY_COLUMNS: tuple[str, ...] = (TIMESTAMP_COLUMN, DAY_COLUMN)` | Columns kept only long enough to produce the temporal split, then dropped; never a model input. `capture_day` joined it once the MachineLearningCSV release turned out to ship no timestamp |
| `LABEL_COLUMN`, `TIMESTAMP_COLUMN`, `DAY_COLUMN` | Constants | `"label"`, `"timestamp"`, `"capture_day"` | The three non-feature columns, named once so nothing spells them inline |
| `PORT_COLUMN` | Constant | `PORT_COLUMN = "destination_port"` | Deliberately **absent** from `LEAKAGE_COLUMNS`. It is genuinely predictive and also a memorisation trap, so Phase 1 trains twice (raw port vs. bucketed service groups) and reports both |
| `_NON_ALNUM` | Constant (private) | `_NON_ALNUM = re.compile(r"[^0-9a-z]+")` | Compiled pattern matching any run of characters that is not a lowercase alphanumeric; the substitution engine behind `normalise_column_name` |
| `normalise_column_name` | Function | `normalise_column_name(name: str) -> str` | Normalises one raw CICIDS2017 header to snake_case: strip, lowercase, replace non-alphanumeric runs with `_`, strip leading and trailing underscores |
| `normalise_columns` | Function | `normalise_columns(columns: list[str]) -> list[str]` | Applies `normalise_column_name` across a header list, preserving order |
| `compute_schema_hash` | Function | `compute_schema_hash(feature_order: list[str] \| tuple[str, ...]) -> str` | Joins the feature order with `\n`, UTF-8 encodes it, SHA-256 digests it and returns `f"{SCHEMA_HASH_PREFIX}:{digest}"`. Order-sensitive by construction |
| `PreprocessingBundle` | TypedDict | `class PreprocessingBundle(TypedDict)` | The required contents of `artifacts/preprocessing.pkl`. Keys: `scaler: Any`, `feature_order: list[str]`, `dropped_columns: list[str]`, `port_encoding: dict[str, Any]`, `schema_hash: str` |
| `build_preprocessing_bundle` | Function | `build_preprocessing_bundle(scaler: Any, feature_order: list[str], dropped_columns: list[str], port_encoding: dict[str, Any]) -> PreprocessingBundle` | Assembles a bundle, copying the mutable inputs and deriving `schema_hash` from `feature_order` so the two can never be set independently |
| `save_preprocessing_bundle` | Function | `save_preprocessing_bundle(bundle: PreprocessingBundle, path: Path) -> Path` | Creates the parent directory if needed and pickles the bundle with `protocol=pickle.HIGHEST_PROTOCOL`; returns the written path |
| `load_preprocessing_bundle` | Function | `load_preprocessing_bundle(path: Path) -> PreprocessingBundle` | Unpickles a locally produced bundle and returns it unchecked — no key validation on the way out. Carries `# noqa: S301 - first-party artifact`, which records a trust boundary rather than silencing an enabled rule (see the Notes): there is no code path that unpickles an uploaded or downloaded file |
| `build_feature_matrix` | Function | `build_feature_matrix(frame, bundle=None) -> pd.DataFrame` | Turns cleaned flow records into the model's input matrix. Called by training to fit the scaler and by serving to apply it — that shared call is what keeps the two paths identical. Without a bundle it returns the unscaled feature frame, which is what fitting needs before a scaler exists; with one it reindexes to `feature_order` and applies the fitted scaler |
| `drop_leakage_columns` | Function | `drop_leakage_columns(frame) -> tuple[pd.DataFrame, list[str]]` | Removes identity and splitting columns, returning what went. `destination_port` is deliberately not among them |
| `fit_port_encoding` | Function | `fit_port_encoding(frame, strategy="raw", top_n=20) -> dict` | Decides the destination-port representation, **on the training split only**. Rejects an unknown strategy |
| `apply_port_encoding` | Function | `apply_port_encoding(frame, encoding) -> pd.DataFrame` | Applies a fitted encoding. The column set depends only on the encoding, never on the frame being transformed |
| `service_group` | Function | `service_group(port: int) -> str` | Buckets a port into its IANA range: `well_known` (≤1023), `registered` (≤49151), `ephemeral` |
| `fit_preprocessing` | Function | `fit_preprocessing(frame, port_encoding="raw", top_n=20) -> PreprocessingBundle` | Fits the `RobustScaler` and freezes the feature contract |
| `PORT_ENCODING_RAW`, `PORT_ENCODING_BUCKETED` | Constants | `"raw"`, `"bucketed"` | The two arms of the Phase 2 port ablation |
| `SERVICE_GROUPS` | Constant | `("well_known", "registered", "ephemeral")` | The bucketed encoding's group columns |
| `DEFAULT_TOP_PORTS` | Constant | `20` | How many ports the bucketed encoding one-hots |

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

- `RobustScaler`, not `StandardScaler`. Flow features are heavy-tailed enough that a handful of enormous flows would flatten everything else under standard scaling. The choice is recorded in the `build_feature_matrix` docstring.
- `destination_port` is the one column with a documented open decision. Leaving it out of `LEAKAGE_COLUMNS` is asserted in `test_destination_port_is_not_silently_dropped`, because dropping it by default would quietly skip the ablation the specification requires.
- `LEAKAGE_COLUMNS` lists both `source_ip`/`src_ip` and `destination_ip`/`dst_ip` because normalised CICIDS2017 headers and the abbreviated field names used elsewhere in the project are both plausible inputs; listing both is cheaper than a lookup table.
- Mutable inputs are copied on the way into the bundle (`list(feature_order)`, `dict(port_encoding)`), so a caller mutating its own list after the call cannot desynchronise the persisted feature order from the persisted hash.
- `pandas` is now a runtime import, not a `TYPE_CHECKING` one: the transforms need it. `sklearn` is still imported lazily inside `fit_preprocessing`, so importing this module stays cheap for the serving path, which only ever *applies* a scaler it unpickled.
- `PreprocessingBundle` is a `TypedDict`, which means it is a plain `dict` at runtime — there is no validation of the five keys on write or on read, and `load_preprocessing_bundle` returns whatever was pickled. The one invariant that is actually enforced is that `build_preprocessing_bundle` *derives* `schema_hash` from `feature_order` rather than accepting it as an argument, so a bundle built through the supported path cannot be internally inconsistent from the start. A bundle assembled by hand can be, and the startup recompute in `ModelBundle._verify_schema_hash` is what catches it.
- The `# noqa: S301` on the unpickle is documentary, not functional: `[tool.ruff.lint]` in `backend/pyproject.toml` selects `E`, `F`, `I`, `UP`, `B` and `W`, so the flake8-bandit `S` rules are not enabled and nothing is being suppressed. It records the trust boundary — artifacts are first-party output of `backend/training/` into a gitignored directory, and no code path unpickles an uploaded or downloaded file — in the place the boundary is crossed. The same comment appears on the Stage 1 unpickle in `backend/app/inference.py:183` for the same reason.
- Wiring: everything in `backend/training/` now imports it. `clean.py` takes the column constants and `normalise_columns`, `split.py` the column constants, `preprocess.py` the fit and save helpers, and `app/inference.py` takes `compute_schema_hash` for the startup check. That fan-in is the point — one implementation of the feature contract, imported by both the trainer and the request path.
- **The port ablation, both arms.** `raw` keeps `destination_port` as one numeric column. `bucketed` replaces it with three IANA service-group indicators plus a one-hot for the top 20 ports *as counted on the training split* — a port first seen at serve time must not add a column, because that would change the matrix width under a trained model. On the real release the two give 70 and 92 features and different schema hashes.
- Status: **implemented**. `tests/test_features.py` and `tests/test_feature_matrix.py` pin the whole module — 35 tests between them, including the element-for-element train/serve parity check that `Code-Backend-Tests` had listed as the most important missing test in the repository.

---

## backend/training/clean.py

Repairs the documented defects in the published CICIDS2017 CSVs before anything else touches them.

CICIDS2017 is not clean data with a few rough edges; it has specific, catalogued defects that have produced a body of published work with inflated scores. Each is handled explicitly rather than papered over, because the most damaging of them — exact duplicate rows — is invisible in every downstream metric and makes a broken pipeline look excellent.

Output goes to `data/interim` as Parquet, not CSV. Parquet is faster to reload and preserves dtypes, which matters because the Inf-to-NaN repair depends on the float columns still being floats. `pyarrow>=17.0` is declared in `backend/pyproject.toml` specifically as the "Parquet writer for data/interim (Phase 1)".

### The six documented defects

| # | Defect | Handling, as implemented |
| --- | --- | --- |
| 1 | Column names carry leading and trailing whitespace — `" Flow Duration"` is not `"Flow Duration"` | Stripped and snake_cased first, via `features.normalise_column_name` |
| 2 | `flow_bytes_s` and `flow_packets_s` contain `Inf` and `NaN` from zero-duration flows | `Inf` becomes `NaN`, then the rows are **dropped**, not imputed — see the decision below |
| 3 | Massive exact-duplicate rows | `drop_duplicates()` before any split. **255,236 removed** on the real release |
| 4 | Zero-variance columns | Dropped, and *deferred to a global pass* — see below |
| 5 | Negative values in some duration and IAT columns | Clipped at zero and counted. **3,253** on the real release |
| 6 | CSV is the wrong interchange format for the interim stage | Cleaned output is written as Parquet |

**The drop-vs-impute decision, which the brief asks to be documented.** Rows whose rate columns are non-finite are dropped. Those values come from flows with a duration of zero, so bytes-per-second is not a missing measurement to estimate — it is undefined. Imputing a median would invent a throughput the flow never had. On the real release this cost **2,867 rows** out of 2.83M, and the count is reported on every run.

**Why zero-variance dropping is deferred.** A column can be constant on Monday and vary on Wednesday — in the published release `fwd_urg_flags` and `cwe_flag_count` do exactly that. Deciding per file gives the files *different column sets*, and concatenating those injects `NaN` into every row that came from a file which kept the column. So `clean_frame(..., drop_zero_variance=False)` is what the per-file path uses, and the assessment happens once, globally, in `split.load_interim`. Globally, **8 columns** go.

### Label normalisation

Three separate defects put stray characters inside the web-attack labels, and each one splits a single attack family into several classes:

| Source | Character | Where it comes from |
| --- | --- | --- |
| The published files | U+0080–U+009F | The cp1252 en dash, seen through the `latin-1` decode those files require |
| The published files | U+00A0 | Non-breaking spaces, plus inconsistent runs of ordinary ones |
| The common UTF-8 mirror | U+FFFD | The replacement character, substituted where the en dash could not be decoded |

All three are collapsed to a single space and the result is stripped, so `Web Attack — Brute Force`, `Web Attack  Brute Force` and `Web Attack � Brute Force` all arrive as one class. This matters well beyond cosmetics: in the Phase 4 leave-one-attack-out loop, holding out one spelling of a family would leave the other in training and quietly invalidate the headline result. U+FFFD additionally cannot be encoded to cp1252 at all, which is how printing the per-class table used to end a run on Windows *after* every file had been written — see `console.py`.

Normalisation runs over the distinct values rather than row-wise, because labels are low-cardinality and the real files run to millions of rows.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `CleaningReport` | Dataclass | `rows_in, rows_out, infinite_values, nan_rows_dropped, duplicate_rows, negative_values_clipped, zero_variance_columns` | What cleaning actually did. `.render()` formats the checkpoint block |
| `clean_frame` | Function | `clean_frame(frame, drop_zero_variance=True) -> tuple[DataFrame, CleaningReport]` | Every documented fix, in order. Idempotent |
| `zero_variance_columns` | Function | `zero_variance_columns(frame) -> list[str]` | Numeric columns carrying a single value. Never the label |
| `read_flow_csv` | Function | `read_flow_csv(path) -> DataFrame` | Reads one CSV, trying UTF-8 before falling back to latin-1 |
| `day_from_filename` | Function | `day_from_filename(path) -> str \| None` | Recovers the capture day from a published file name |
| `load_raw` | Function | `load_raw(raw_dir) -> DataFrame` | Concatenates every CSV, stamping each with its capture day |
| `clean_file` | Function | `clean_file(path, drop_zero_variance=True) -> tuple[DataFrame, CleaningReport]` | Read-then-clean for one file |
| `clean_directory` | Function | `clean_directory(raw_dir, interim_dir) -> CleaningReport` | The memory-safe path: one Parquet out per CSV in |
| `NEGATIVE_CLIP_COLUMN_HINTS` | Constant | `("duration", "iat")` | Name fragments marking columns where a negative is impossible |
| `main` | Function | `main(argv=None) -> int` | CLI: `python -m training.clean` |

### Encoding, and why the reader tries UTF-8 first

The original files are cp1252 and fail to decode as UTF-8. The corrected re-releases the brief mentions are UTF-8, and reading *those* as latin-1 silently turns every multi-byte character into two — a label gains a stray letter and becomes its own class. UTF-8 is strict enough to fail loudly on a latin-1 file, so trying it first and falling back is safe in a way the reverse order is not.

- Cleaning runs before splitting, never after. Deduplicating after a split cannot remove a duplicate that has already been separated across the boundary.
- `clean_directory` is the memory-safe path and the one `make data-clean` uses: the real release is 2.83M rows by 79 columns, and holding all of it plus copies is several gigabytes.
- Both the per-file path and the one-shot `--all` path produce identical splits and the same schema hash. They attribute duplicate removals to different stages — per-file cleaning cannot see a duplicate that spans two files, so those surface later as cross-split duplicates instead — and the totals reconcile.
- Status: **implemented**, run against the real release.

---

## backend/training/split.py

The temporal train, validation and test splits, and the benign-only set Stage 2 trains on.

CICIDS2017 was captured over five consecutive weekdays, each with a different attack profile, and that structure is the split. Days are assigned to roles; rows are never shuffled between them. The alternative — `train_test_split(shuffle=True)` — leaks near-identical duplicated flows across train and test and manufactures fake 99.9% scores. That failure mode is listed first in [Anti-Patterns](Anti-Patterns.md), and this module exists to make the correct behaviour the only available one.

| Day | Content | Role |
| --- | --- | --- |
| Monday | Benign only | `benign_train` (Stage 2) |
| Tuesday | FTP-Patator, SSH-Patator | `train` |
| Wednesday | DoS Hulk/GoldenEye/Slowloris/Slowhttptest, Heartbleed | `train` |
| Thursday | Web attacks (AM), infiltration (PM) | `val` |
| Friday | Botnet, port scan, DDoS | `test` |

Monday being benign-only is what makes Stage 2 possible at all: a label-contamination-free training set obtained without any filtering that could go wrong. The benign rows of Tuesday and Wednesday join it, and an attack row reaching that set raises `AttackInBenignTrainingSet` rather than warning — the whole Stage 2 claim rests on that file containing no attacks.

Friday holding botnet, port scan and DDoS means the test day contains families the validation day does not, so a model tuned on Thursday is genuinely being asked about traffic it was not tuned against. Verified on the real run: **no attack family appears in both train and test**.

### Splitting when there is no timestamp

The widely-mirrored **MachineLearningCSV** release ships with `Flow ID`, both IP columns, `Source Port` *and* `Timestamp` already removed by CIC. With no timestamp there is nothing to split on — but the capture day survives in the file names, so `clean.day_from_filename` recovers it and carries it in a `capture_day` column.

`split_frames` prefers a real `timestamp` where a file has one and falls back to `capture_day`, raising only when neither is present. Both are splitting keys and neither is ever a model input, so both are dropped once the split is made — `SPLIT_ONLY_COLUMNS` holds the pair.

### Cross-split duplicates

Dropping the splitting key can make rows from different days byte-identical. Those are exactly the duplicates that span a split boundary, so they are detected, removed and counted rather than assumed away. **41,984** were removed on the real release, and the written splits share zero rows.

This is done as one vectorised `duplicated(keep="first")` pass over the concatenated splits, ordered train, val, test — which is the "earliest split wins" rule, and also catches duplicates created *within* a split when the key was dropped. The obvious alternative, building a string key per row and testing membership against a growing Python set, takes about 24 seconds per 200k rows and never finishes on 2.5M. A test pins the cost so it cannot come back.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `split_frames` | Function | `split_frames(frame) -> SplitResult` | Splits temporally and builds the benign-only set |
| `SplitResult` | Dataclass | `train, val, test, benign_train, report` | The four frames and the checkpoint report |
| `SplitReport` | Dataclass | `counts, benign_train_rows, cross_split_duplicates, unknown_days` | Row counts per split per class. `.render()` formats the table |
| `AttackInBenignTrainingSet` | Exception | — | Raised when a labelled attack reaches the Stage 2 set |
| `DAY_ROLES` | Constant | `dict[str, str]` | Day to role. Weekend days are absent from the capture |
| `BENIGN_LABEL` | Constant | `"BENIGN"` | Upper-case, as the source spells it |
| `load_interim` | Function | `load_interim(interim_dir) -> DataFrame` | Concatenates the cleaned Parquet and applies the global zero-variance drop |
| `write_splits` | Function | `write_splits(result, processed_dir) -> None` | Writes the four Parquet files |
| `main` | Function | `main(argv=None) -> int` | CLI: `python -m training.split` |

- The phase checkpoint is three assertions: row counts per split per class, zero duplicate rows shared across splits, and no `NaN` or `Inf` surviving. All three are verified against the written files, not the in-memory frames.
- The zero-shared-duplicates check catches a regression in `clean.py` defect 3. Running it here checks the property that actually matters — duplicates *across the split boundary* — rather than the operation that was supposed to produce it.
- `load_interim` is where deferred zero-variance dropping lands, because that is the first point at which the whole dataset is in one frame.
- Status: **implemented**, run against the real release.

---

## backend/training/preprocess.py

Fits the preprocessing bundle and drives the phase end to end.

Cleaning and splitting each own a file. This module owns the last step, and the one the serving path depends on: fitting the `RobustScaler` **on the training split alone** and persisting it together with the feature order, the dropped columns, the port encoding and the schema hash.

All five travel in one pickle because no subset of them reproduces the training-time feature matrix. `app/inference.py` recomputes the hash at startup and refuses to serve on a mismatch, which turns train/serve skew from a silent scoring bug into a refused boot.

Fitting on train only is not a detail. Fitting on everything leaks the test distribution into the scaler, and a test asserts the two produce different centres so the shortcut cannot be taken quietly.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `fit_and_save` | Function | `fit_and_save(train, artifacts_dir, port_encoding="raw", top_n=20) -> tuple[PreprocessingBundle, Path]` | Fits on the training split and writes the bundle |
| `run_pipeline` | Function | `run_pipeline(frame, processed_dir, artifacts_dir, ...) -> PipelineResult` | Clean, split, fit and write from one raw frame |
| `PipelineResult` | Dataclass | `processed_dir, artifacts_dir, cleaning_report, split_report, bundle` | `.render()` prints the whole phase checkpoint |
| `BUNDLE_FILENAME` | Constant | `"preprocessing.pkl"` | — |
| `main` | Function | `main(argv=None) -> int` | CLI: `python -m training.preprocess [--all] [--port-encoding raw\|bucketed]` |

- `--all` runs cleaning, splitting and fitting in one go from `data/raw`, which is what `make data` invokes. Without it the module reads `data/processed/train.parquet` and only fits, which is `make data-fit`.
- `--port-encoding bucketed` fits the other arm of the Phase 2 ablation. On the real release `raw` gives 70 features and `bucketed` 92, with different schema hashes, so one bundle cannot be mistaken for the other.
- Fitting on an empty training split raises rather than writing a degenerate scaler.
- Status: **implemented**, run against the real release.

---

## backend/training/console.py

Report output that degrades a character rather than losing the report.

The reports these CLIs print — cleaning counts, row counts per split per class — are deliverables of Phase 1, not decoration. They have to survive the terminal they land in.

On Windows that terminal defaults to cp1252, which cannot encode most of what a dataset can put in a class name. The published labels already carry a cp1252 en dash, and the common UTF-8 mirror carries U+FFFD in its place. Printing either through a cp1252 stdout raises `UnicodeEncodeError` and kills the run *after* the real work is finished, which is the worst possible moment — it did exactly that on the first full run of this pipeline.

Labels are normalised on the way in, so in practice nothing unencodable should reach here. This is the belt to that braces.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `echo` | Function | `echo(text, stream=None) -> None` | Writes a line to stdout, replacing characters the stream cannot encode |

- `clean.py`, `split.py`, `preprocess.py`, `train_supervised.py` and `evaluate.py` route every report line through `echo` rather than `print`.
- A report is worth degrading a character for, never worth crashing over. The failure it prevents is silent in the worst way: the data is already written, so a rerun does the whole job again to produce output nobody sees.
- Status: **implemented**.

---

## backend/training/labels.py

Phase 2 — the single place the fifteen published CICIDS2017 label strings collapse into the eight classes Stage 1 is trained against.

The same mapping has to hold in three places that must never disagree: training, the Phase 4 hold-out loop, and the family an alert carries into the dashboard. Two rules govern it.

**Nothing maps by accident.** Matching is on an exact canonical form of the published string — lowercased, runs of non-alphanumerics collapsed to single spaces — and never on a substring. The trap that rule exists for is `Web Attack Brute Force`, which contains "brute force" and is emphatically *not* `brute_force`. A substring match would file Thursday's web attacks under Tuesday's class, which would also put one family on both sides of the leave-one-attack-out loop and quietly invalidate the project's headline result. Canonicalising rather than matching literals is what lets the original release, its corrected re-releases (which separate those words with a hyphen or an en dash) and the whitespace `clean.py` normalises all land on the same entry.

**A label with no entry raises.** `UnmappedLabel` names every unknown value at once. The alternative — a default bucket — turns a dataset the maintainer has not looked at into a silently mislabelled training set, with an attack family deleted and nothing said.

`Heartbleed` maps to `web_attack`: the eight-class vocabulary has no slot of its own for it, and MITRE T1190, malformed input aimed at a public-facing application, describes a malformed TLS heartbeat as well as it describes SQL injection.

### The support floor

Collapsing is not the last word on what gets trained. On the real training split `web_attack` collapses to **11 Heartbleed rows against 821,166 benign**, and under `class_weight="balanced"` that earns it a weight above 20,000 — enough pull to bend the forest's whole decision surface chasing eleven examples. `MIN_CLASS_SUPPORT` holds any family under 100 rows out of the vocabulary, and `mapping_report` prints which ones went and why.

The rows are not deleted from the data and not hidden; they are scored like any other traffic. Being unnameable by Stage 1 is the condition Stage 2 exists to cover.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `BENIGN_FAMILY` | Constant | `BENIGN_FAMILY = "benign"` | The one non-attack class, named once |
| `ATTACK_FAMILIES` | Constant | `tuple[str, ...]` of seven | Mirrors `app.models.ALERT_FAMILIES`; duplicated rather than imported so the training package stays free of SQLAlchemy, with a test asserting the two cannot drift |
| `FAMILIES` | Constant | `(BENIGN_FAMILY, *ATTACK_FAMILIES)` | The full vocabulary, in the fixed order `predict_proba` columns are emitted in |
| `MIN_CLASS_SUPPORT` | Constant | `MIN_CLASS_SUPPORT = 100` | Rows below which a family is held out of the vocabulary |
| `LABEL_TO_FAMILY` | Constant | `dict[str, str]` of fifteen | Canonical published label → class. Every string the dataset ships appears here |
| `UnmappedLabel` | Exception | `class UnmappedLabel(KeyError)` | Raised for any label with no entry, rather than defaulting |
| `canonical` | Function | `canonical(label: str) -> str` | Reduces a published label to the map's key form |
| `to_family` | Function | `to_family(label: str) -> str` | Collapses one label, raising `UnmappedLabel` on anything unrecognised |
| `map_labels` | Function | `map_labels(labels: pd.Series) -> pd.Series` | Collapses a column, reporting every unknown value at once. Mapped over the distinct values, because labels are low-cardinality and the splits run to a million rows |
| `held_out_families` | Function | `held_out_families(families, min_support=100) -> list[str]` | Attack classes too small to train on, smallest first. Benign is never a candidate |
| `vocabulary` | Function | `vocabulary(families, min_support=100) -> list[str]` | The classes Stage 1 will actually be fitted on, in `FAMILIES` order |
| `mapping_report` | Function | `mapping_report(labels, families, min_support=100) -> str` | Which published labels collapsed into which class, with counts and the support-floor note |

- The counts are what make the log worth reading. They are how a reader sees that `web_attack` on the training days is eleven Heartbleed rows rather than a web-attack class.
- `vocabulary` returns a fixed order on purpose: that order *is* the column order of `predict_proba`, and it is persisted in the artifact so serving can map a column back to a family name without guessing.
- Tested by `backend/tests/test_labels.py`.
- Status: **implemented**.

---

## backend/training/metrics.py

Phase 2 — the threshold arithmetic and the quantities Stage 1 is judged on, shared by the trainer and the evaluator.

Both `train_supervised.py` (which picks `tau_sup` on the validation day) and `evaluate.py` (which reports the test day) import from here, so the two cannot compute the same number two different ways. Accuracy is computed here too, and appears in exactly one table cell of the report: on traffic that is 99% benign, a model that always answers benign scores 99%, and the number describes the class balance rather than the model.

### Two attack scores, and why the smaller one is used

`attack_confidence` is the largest single attack-class probability — the brief's fusion rule, `p[attack_classes].max()`. `attack_probability` is `1 - P(benign)`. They differ when evidence is split across families: a flow at 0.4 benign / 0.3 dos / 0.3 ddos scores 0.3 under the first and 0.6 under the second.

`tau_sup` cuts the first, deliberately. A row Stage 1 cannot confidently *name* should fall through to Stage 2, which is exactly what the lower score produces. The evaluation reports the PR-AUC of both so the choice is auditable rather than assumed: on the test day they land within 0.001 of each other (0.8468 against 0.8476), and on the validation day within 0.006 (0.8816 against 0.8871). The fusion rule's choice costs a little ranking quality and buys the cascade its reason to exist.

### Selecting the threshold

`select_threshold` takes the distinct benign scores as its candidates, so the FPR at the chosen threshold is a measured value rather than an interpolation, and appends one sentinel above all of them for the case where no observed score fits the budget. Because the count of benign rows at or above a candidate falls as the candidate rises, the first candidate that fits the budget is also the smallest one — which is the one wanted, since every step higher discards recall the analysts had the capacity to absorb.

The sentinel case is reported rather than hidden: `ThresholdChoice.above_every_benign_score` says the budget could only be met by a threshold above every benign score on the split, which is a real outcome and not a number that should be allowed to look ordinary.

### Projecting onto a day

`alert_volume` multiplies the **false-positive rate** by the daily flow volume, not the alert rate measured on the split. A CICIDS2017 attack day is over a third attack traffic; projecting that density onto a million flows would describe a queue no real network produces and would make the budget comparison meaningless. The measured alert rate is still reported, as a measurement of the split rather than a projection. True positives sit on top of the false-alert floor, and how many there are depends on how much attack traffic the network actually carries — which a lab capture cannot say.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `MAX_CURVE_POINTS` | Constant | `MAX_CURVE_POINTS = 512` | Curves are downsampled for transport; a quarter of a million points is a 20 MB payload nobody can render |
| `attack_columns` | Function | `attack_columns(classes) -> list[int]` | Indices of the attack classes in a `predict_proba` matrix |
| `attack_confidence` | Function | `attack_confidence(proba, classes) -> np.ndarray` | The quantity `tau_sup` cuts: the largest single attack-class probability |
| `attack_probability` | Function | `attack_probability(proba, classes) -> np.ndarray` | `1 - P(benign)`, reported alongside so the choice above is auditable |
| `predicted_attack_family` | Function | `predicted_attack_family(proba, classes) -> np.ndarray` | The family an alert would carry: the highest-scoring attack class |
| `false_positive_rate` | Function | `false_positive_rate(scores, is_benign, tau) -> float` | Benign rows the threshold would alert on, over all benign rows |
| `ThresholdChoice` | Dataclass | `tau, fpr, target_fpr, benign_rows, false_alerts, above_every_benign_score` | `tau_sup` and the evidence for it, with a `render()` for the checkpoint |
| `select_threshold` | Function | `select_threshold(scores, is_benign, target_fpr) -> ThresholdChoice` | The smallest threshold whose FPR fits the budget |
| `threshold_sweep` | Function | `threshold_sweep(scores, is_benign, is_attack, candidates, daily_flow_volume) -> list[dict]` | One row per candidate: FPR, attack recall, implied alert volume — the table that makes the choice mechanical |
| `AlertVolume` | Dataclass | `tau, alert_rate, fpr, false_alerts_per_day, alerts_per_analyst_hour, budget_per_day, attack_share_of_split` | What a threshold costs the queue, in the unit a SOC budgets in |
| `alert_volume` | Function | `alert_volume(scores, is_benign, tau, daily_flow_volume, capacity, shift_hours) -> AlertVolume` | Projects a threshold onto a day of traffic |
| `DetectionCurves` | Dataclass | `pr_auc, roc_auc, pr_curve, roc_curve, positives, negatives` | Both curves for the binary "is this an attack at all" question |
| `detection_curves` | Function | `detection_curves(is_attack, scores) -> DetectionCurves` | PR-AUC, ROC-AUC and both curves, downsampled. Returns `nan` rather than inventing a number for a one-class split |
| `per_class_report` | Function | `per_class_report(truth, predicted, classes) -> dict` | Per-class precision, recall, F1 and support, spanning families the model has no column for |
| `confusion` | Function | `confusion(truth, predicted, classes) -> list[list[int]]` | Rows true, columns predicted. Aggregate metrics say a class is weak; only the matrix says what it is mistaken for |
| `recall_by_family` | Function | `recall_by_family(truth, flagged, families) -> dict` | How much of each family clears the threshold regardless of the name given — a DDoS flow flagged as `dos` is caught |
| `accuracy` | Function | `accuracy(truth, predicted) -> float` | Reported in one table cell for comparability, never as a headline |

- `per_class_report` spans every family present in the truth *as well as* every family the model can emit, so a family with no column shows a row of zeros against its real support rather than vanishing from the table. That row is the point: on a temporal split, the test day's families are mostly ones Stage 1 was never shown.
- Tested by `backend/tests/test_metrics.py`.
- Status: **implemented**.

---

## backend/training/estimators.py

Phase 2 — estimator wrappers that have to survive a pickle round trip.

This module exists for one reason, and it is worth stating in full because the failure is confusing when met cold. `pickle` does not store a class; it stores the class's `__module__` and `__qualname__` and looks the pair up again at load time. A class defined in a module started with `python -m training.train_supervised` has `__module__ == "__main__"`, because that is genuinely what the module was called while it ran. The API process, and `evaluate.py`, and anything else that later loads the artifact, have a different `__main__` entirely — so the lookup lands in the wrong module and raises `AttributeError: Can't get attribute ... on <module '...'>`. Training succeeds, the artifact is written, and nothing that reads it can open it.

This was not hypothetical: the first LightGBM champion was written that way and could not be evaluated or served. Anything pickled into an artifact therefore lives here, in a module that is imported by name and never run as a script.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `LightGBMClassifier` | Dataclass | `LightGBMClassifier(booster, classes)` | A scikit-learn estimator face — `classes_` and `predict_proba` — over a raw LightGBM `Booster` |
| `LightGBMClassifier.classes_` | Property | `-> np.ndarray` | The class order, which *is* the column order of `predict_proba` |
| `LightGBMClassifier.predict_proba` | Method | `(x) -> np.ndarray` | Predicts at `booster.best_iteration`, so the early-stopping decision is honoured at serving time too |
| `LightGBMClassifier.predict` | Method | `(x) -> np.ndarray` | Argmax over the probabilities, returning family names |

- Wrapping the Booster means nothing downstream branches on which algorithm produced the champion; serving, TreeSHAP and the evaluation all speak the estimator protocol.
- The raw Booster is used rather than `LGBMClassifier` because training needs a custom evaluation function over a validation set whose labels are outside the training vocabulary — see `train_supervised.fit_lightgbm`.
- `backend/tests/test_supervised.py::test_a_lightgbm_champion_survives_leaving_the_process_that_trained_it` unpickles a trained artifact in a **subprocess**, because a test running inside pytest has its own `__main__` and would not otherwise notice the bug.
- Status: **implemented**.

---

## backend/training/train_supervised.py

Phase 2 entry point training Model A, the supervised multi-class classifier that names known attack families.

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

This reframes the threshold from an arbitrary constant into a statement about how many alerts a shift can actually triage. On the measured run it landed at **`tau_sup = 0.387908`**: 126 false alerts out of 396,328 benign validation rows, an FPR of `3.18e-4` against the `3.20e-4` target.

`tau_sup` is persisted **inside `supervised_model.pkl`**, not beside it, so the threshold and the model it was cut from cannot be separated. `model_card.json` carries a copy under `thresholds.tau_sup` for display, but `ModelBundle._load_models` takes the value from the artifact — one source per threshold, and no cross-check to get wrong.

### Champion and fallback

Every run writes a self-contained pair: `supervised_<algorithm>.pkl` and the `preprocessing_<algorithm>.pkl` it was fitted against. `promote()` compares the run's validation PR-AUC against the incumbent recorded in `model_card.json` and, only if it wins, copies both files over the canonical `supervised_model.pkl` / `preprocessing.pkl` that the API loads.

Copying the pair together is what makes "keep the RandomForest as a fallback" mean something in practice: the two halves always match, so swapping back after a regression is a file copy rather than a retrain. A challenger that loses stays on disk under its own name and changes nothing.

### The port ablation

`--port-ablation` trains once with the raw destination port and once with it bucketed, into a scratch directory, and writes `reports/port_ablation.md`. The question it answers is not which scores higher but whether raw scores *much* higher — destination port is genuinely predictive and also a memorisation trap, and a large gain from the raw value is evidence the model learned the lab's port assignments rather than attack behaviour.

Scored on the validation day, not the test day: a feature-encoding decision made on the test day is a decision that has already spent the test day. On the measured run raw came in 1.0% *lower* than bucketed, so nothing here rests on memorising ports, and `DEFAULT_PORT_ENCODING` is bucketed.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `ALGORITHMS` | Constant | `("rf", "lgbm")` | The two algorithms, in the order they are built |
| `RF_ESTIMATORS`, `RF_DEPTH_GRID` | Constants | `300`, `(8, 16, 24, 32, 48, 64)` | The brief's tree count; a depth grid that deliberately runs past the winner, because an optimum at the top of a grid is a grid that was too short |
| `RF_SWEEP_ROWS`, `RF_SWEEP_MIN_PER_CLASS` | Constants | `250_000`, `2_000` | The depth search runs on a stratified subsample of the **training split only**, and the winner is refitted on all of it |
| `LGBM_MAX_ROUNDS`, `LGBM_EARLY_STOPPING_ROUNDS` | Constants | `1_000`, `50` | Boosting budget and patience; the measured run stopped at iteration 227 |
| `DEFAULT_PORT_ENCODING` | Constant | `PORT_ENCODING_BUCKETED` | The shipped encoding, chosen from the ablation |
| `NoTrainableClasses` | Exception | `class NoTrainableClasses(RuntimeError)` | Raised when no attack family clears the support floor — Stage 1 has nothing to learn |
| `TrainingRun` | Dataclass | 17 fields + `val_pr_auc`, `render()` | Everything the evaluation and the write-up need from one fit: provenance, class support, the sweeps, the threshold, the validation metrics |
| `Prepared` | Dataclass | `bundle, x_train, y_train, x_val, y_val, classes, ...` | Feature matrices and collapsed labels for one port encoding |
| `load_split` | Function | `load_split(processed_dir, name) -> pd.DataFrame` | Reads a Phase 1 Parquet split, with a message naming `make data` when it is absent |
| `prepare` | Function | `prepare(train, val, port_encoding, top_n, min_class_support) -> Prepared` | Collapses labels, freezes the feature contract on the training split alone, builds both matrices as `float32` |
| `_class_weights` | Function (private) | `(y, classes) -> dict[str, float]` | `balanced` weights, computed once so both fitters use the same numbers |
| `_stratified_subsample` | Function (private) | `(x, y, rows, seed)` | A class-proportional slice of the training split for the depth search. No row crosses a split boundary; this is not the shuffled-split anti-pattern |
| `fit_random_forest` | Function | `(data, depth_grid, seed, sweep_rows) -> (model, hyperparameters, sweep)` | Sweeps `max_depth` against the validation day, then refits the winner on the full split |
| `fit_lightgbm` | Function | `(data, seed) -> (model, hyperparameters, [])` | Boosted trees with early stopping on a custom validation metric |
| `measure_validation` | Function | `(model, data, target_fpr, ...) -> (ThresholdChoice, dict, list)` | Chooses `tau_sup` on the validation day and records what it costs |
| `write_artifacts` | Function | `(model, run, bundle, artifacts_dir) -> (Path, Path)` | Persists the model and its preprocessing as a pair, plus the run record as JSON |
| `read_model_card`, `promote` | Functions | `(artifacts_dir)`, `(run, artifacts_dir) -> bool` | Read the incumbent; publish the challenger only if it wins, logging the comparison either way |
| `train` | Function | `(train_frame, val_frame, artifacts_dir, algorithm, port_encoding, ...) -> TrainingRun` | Fits one Stage 1 model end to end and writes its artifacts |
| `port_ablation`, `render_port_ablation` | Functions | `(...) -> dict[str, TrainingRun]`, `(runs, algorithm) -> str` | Trains both encodings into a scratch directory and writes the comparison |
| `main` | Function | `main(argv=None) -> int` | CLI: `--algorithm`, `--port-encoding`, `--port-ablation`, `--depth-grid`, `--sweep-rows`, `--min-class-support`, `--seed`, `--no-promote`, and the three directory overrides |

### Why the validation day needs a custom metric

The validation day carries families the training days do not, which breaks the obvious choice of early-stopping metric. A multi-class loss has no valid label for a row whose family is outside the training vocabulary, and computing it over the remainder would be a loss over benign traffic almost exclusively — which rewards a model that answers benign to everything.

So LightGBM's validation `Dataset` is handed a **binary** attack/benign label with `metric: "None"`, and the only metric is a custom one: the PR-AUC of the attack confidence. It is defined for every row, and it is the same quantity `tau_sup` is later cut from. The RandomForest uses the same metric to select `max_depth` — bagging has no stopping point to find, so depth is what the validation day decides for it.

- Target classes collapse through `training.labels`; the mapping is logged rather than left implicit, and the measured vocabulary is `benign`, `dos`, `brute_force`.
- No SMOTE. Synthetic interpolation between flow records invents packets that could not exist on a real network, and applied before a split it puts synthetic neighbours of test rows into training.
- The test day is never read by this module. `evaluate.py` opens it once, after every choice has been made.
- `scikit-learn>=1.5` and `lightgbm>=4.5` are both declared in `backend/pyproject.toml`, annotated there as "Model A baseline: RandomForestClassifier" and "Model A upgrade" respectively.
- Tested by `backend/tests/test_supervised.py`.
- Status: **implemented**.

---

## backend/training/train_autoencoder.py

Phase 3 entry point training Model B, the benign-only anomaly detector that gives the project its novel-attack claim.

Model B answers "how unlike normal traffic is this?" and produces `artifacts/autoencoder.pt` as a state dict. It trains on benign rows only: Monday in full, plus the benign rows from Tuesday and Wednesday. The docstring is explicit that attack rows must never enter this training set and that the exclusion must be *asserted in code rather than merely intended* — that assertion is what makes the novel-attack claim real instead of a relabelled supervised model. If attack rows leak into the benign training set, the model learns to reconstruct those attacks, stops flagging them, and the leave-one-attack-out numbers in Phase 4 become meaningless in a way no downstream metric reveals.

The architecture is a symmetric bottleneck:

```text
input(d) -> 64 -> 32 -> 16 -> 32 -> 64 -> output(d)
ReLU, MSE loss, Adam, early stopping on benign validation loss
Dropout 0.1 in the encoder; batch norm helps convergence here
```

The 16-unit bottleneck is the mechanism: a network that can only carry sixteen numbers through the middle has to learn the structure of normal traffic, and traffic that does not share that structure reconstructs badly. The score for a row is its mean squared reconstruction error.

`tau_anom` is the 99.5th percentile of reconstruction error on held-out benign validation data — set from the benign distribution alone, never from attack data. The full benign error distribution is persisted as histogram bins, not raw rows, because both the dashboard's interactive threshold slider and drift detection read it and neither needs per-row data. `ModelBundle` holds it as `benign_error_histogram`.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 3 training entry point. **Stub** — raises `NotImplementedError("train_autoencoder.py is implemented in Phase 3 (anomaly detector).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

- Baselines to run on the same split: `IsolationForest`, `LOF` and `ECOD` from PyOD. They establish that the autoencoder earns its complexity, and if one of them wins, that is a finding to report rather than hide.
- Stage 2 explanation does not use SHAP. The per-feature reconstruction error is already computed, so the features the model failed hardest to reconstruct are, directly, why the row looks anomalous. TreeSHAP is for Stage 1 only.
- The artifact is a state dict, not a pickled module. `ModelBundle` holds it as `autoencoder_state` and its comment notes that Phase 3 owns the `nn.Module` definition and reconstructs the model from it; torch weights are read with `weights_only=True` so the file is data rather than code.
- `torch>=2.4` is declared in `backend/pyproject.toml` as "Model B: autoencoder". `requires-python = ">=3.11,<3.13"` is pinned partly because 3.13/3.14 lack settled wheels for this stack.
- The phase checkpoint is a histogram of benign vs. attack reconstruction error with the threshold line drawn. If the distributions do not visibly separate, the model is not working and no dashboard hides that.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 3.

---

## backend/training/evaluate.py

Phase 2/3 entry point emitting the metric set the project is willing to be judged on.

This module exists to make one class of dishonesty impossible by default. On traffic that is 99% benign, a model that always answers "benign" scores 99% accuracy, so accuracy may appear in a table but never as a headline number. PR-AUC is the headline instead.

The emitted set is: per-class precision, recall, F1 and support; the confusion matrix; PR and ROC curves rendered side by side; PR-AUC as the headline; the FPR at the chosen threshold; and projected alerts per analyst per hour. That last figure closes the loop with the false-positive budget in `train_supervised.py` — it is the budget's prediction checked against the model's actual behaviour.

Rendering PR and ROC side by side is deliberate rather than completionist. The gap between the two curves *is* the explanation for why ROC-AUC flatters an imbalanced classifier, and showing both makes the argument visible instead of asserted.

### Reported at the operating point, not at argmax

Predictions in the per-class table are taken the way the served system takes them: a family is emitted only when the attack confidence clears `tau_sup`, and everything below it stays benign. A plain `argmax` report would describe an operating point nobody runs.

That choice is what makes the table legible on this dataset. A class in the vocabulary with zero support is not a failure — the test day simply carries none of it — but its precision column still means something: it is the share of rows given that name which really were that family, and a zero says every such prediction was a family the model has no name for. The confusion matrix says which one.

### The written interpretation is generated, not written once

The Phase 2 checkpoint requires a paragraph naming which classes the model handles poorly and why. `_interpretation` templates it from the measured numbers rather than leaving prose to be edited by hand, so a rerun that moves the numbers cannot leave a stale claim behind. The same applies to `_pr_versus_roc`, which builds the PR-versus-ROC argument out of the two splits' actual benign shares.

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `REPORT_FILENAME`, `METRICS_FILENAME` | Constants | `"phase2_supervised.md"`, `"metrics_supervised.json"` | What the champion's outputs are called |
| `output_names` | Function | `output_names(algorithm) -> tuple[str, str]` | The champion owns the unsuffixed names; evaluating a fallback writes beside them rather than over them |
| `ArtifactMismatch` | Exception | `class ArtifactMismatch(RuntimeError)` | The model and its preprocessing disagree — fatal, for the same reason the API refuses to start on it |
| `LoadedModel` | Dataclass | `model, bundle, payload` + `classes`, `tau_sup` | One loaded champion or fallback pair |
| `load_model` | Function | `load_model(artifacts_dir, algorithm=None) -> LoadedModel` | Loads the champion pair, or a named algorithm's fallback pair, and checks the two schema hashes against each other |
| `load_run_record` | Function | `(artifacts_dir, version, algorithm) -> dict \| None` | The training record belonging to the model being evaluated. The champion's lives in its model card; looking a record up by algorithm name alone would pair one run's model with another run's sweep tables |
| `operating_point_prediction` | Function | `(proba, classes, tau) -> np.ndarray` | What the system actually emits: a family only when the threshold is cleared |
| `Evaluation` | Dataclass | 18 fields | Measured numbers for one split under one model, including both curves |
| `evaluate_split` | Function | `(loaded, frame, split, settings) -> Evaluation` | Scores one split at the persisted threshold and measures everything |
| `render_report` | Function | `(evaluation, run, settings) -> str` | The Markdown write-up |
| `write_outputs` | Function | `(evaluation, run, artifacts_dir, reports_dir, settings, algorithm=None) -> (Path, Path)` | Writes the human report and the machine-readable metrics, and adds the `test` block to the champion's model card |
| `main` | Function | `main(argv=None) -> int` | CLI: `--algorithm`, `--split`, and the three directory overrides |

- `model_card.json` is written by `train_supervised.py` at promotion time, carrying `version`, `schema_hash`, the `thresholds` block and the full training record. `evaluate.py` adds a `test` block to it, and only when the card's version matches the model it just scored.
- `metrics_supervised.json` carries the budget inputs, the training record and the test evaluation including 512-point PR and ROC curves — the payload `GET /api/v1/metrics/model` will serve in Phase 5 and the Model Performance screen will draw in Phase 6.
- Tested by `backend/tests/test_supervised.py`, including that the report carries every section the checkpoint asks for and that accuracy never appears as a headline.
- Status: **implemented** for Phase 2. Phase 3 extends it with the Stage 2 numbers.

---

## backend/training/loao.py

Phase 4 entry point running the leave-one-attack-out evaluation, the project's headline result.

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

| Symbol | Kind | Signature | Description |
| --- | --- | --- | --- |
| `main` | Function | `main() -> None` | Phase 4 LOAO entry point. **Stub** — raises `NotImplementedError("loao.py is implemented in Phase 4 (fusion and LOAO).")` |
| `__main__` guard | Module entry | `if __name__ == "__main__": main()` | Makes the module runnable as a script |

- The `Missed` column is not optional and is not to be quietly dropped. The docstring states the reasoning directly: a table with a real Missed column reads as credible engineering, a table of 99s reads as a bug.
- The claim this table supports is bounded. LOAO measures generalisation to held-out *known* attacks, which is a proxy for genuinely novel ones, not proof — and that limitation belongs in the README rather than in a footnote.
- Retraining once per family makes this the most expensive job in the pipeline; it is batch, offline, and has no interaction with the API.
- The fusion logic it exercises is Phase 4's, in `backend/app/inference.py`, not a separate copy — running LOAO against a reimplementation of fusion would measure the reimplementation.
- Status: **stub** — raises `NotImplementedError`, lands in Phase 4.
