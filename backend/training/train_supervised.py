"""Phase 2 -- Model A, the supervised classifier.

Order of work, which is not to be reversed:

1. ``RandomForestClassifier`` baseline (``n_estimators=300``, ``max_depth``
   tuned against the validation day, multi-class), committed as a running
   baseline before anything else is touched.
2. LightGBM as a swap-in upgrade once the baseline runs end to end, with the
   RandomForest artifact kept as a fallback rather than overwritten.

Both write a self-contained pair -- ``supervised_<algorithm>.pkl`` and the
``preprocessing_<algorithm>.pkl`` it was fitted against -- and the better of
the two on the validation day is promoted to the canonical
``supervised_model.pkl`` / ``preprocessing.pkl`` the API loads. Promotion
compares validation PR-AUC and logs the comparison, so "LightGBM won" is a
recorded measurement rather than an assumption.

Rules this module holds to:

* ``class_weight="balanced"``. No SMOTE: interpolating between flow records
  invents packets that could not exist on a real network, and applied before a
  split it puts synthetic neighbours of test rows into training.
* Early stopping against the validation day, never the test day.
* Classes collapse through ``training.labels``, and the mapping is logged.
* ``tau_sup`` comes from the false-positive budget in ``Settings.target_fpr``,
  never from ``argmax`` and never from a default of 0.5.

The validation day is the only split this module reads besides the training
days. The test day is untouched here -- ``evaluate.py`` opens it once, after
every choice has already been made.
"""

from __future__ import annotations

import argparse
import json
import logging
import pickle
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from training.console import echo
from training.estimators import LightGBMClassifier
from training.features import (
    DEFAULT_TOP_PORTS,
    PORT_ENCODING_BUCKETED,
    PORT_ENCODING_RAW,
    PORT_ENCODING_STRATEGIES,
    PreprocessingBundle,
    build_feature_matrix,
    fit_preprocessing,
    save_preprocessing_bundle,
)
from training.labels import (
    BENIGN_FAMILY,
    MIN_CLASS_SUPPORT,
    held_out_families,
    map_labels,
    mapping_report,
    vocabulary,
)
from training.metrics import (
    ThresholdChoice,
    alert_volume,
    attack_confidence,
    attack_probability,
    detection_curves,
    select_threshold,
    threshold_sweep,
)

logger = logging.getLogger(__name__)

ALGORITHMS: tuple[str, ...] = ("rf", "lgbm")

FAMILY_COLUMN = "family"

# n_estimators is the brief's figure. Bagging does not overfit with more trees,
# so there is nothing here to early-stop -- max_depth is what the validation
# day selects, and it is the only knob swept.
RF_ESTIMATORS = 300
# The grid runs past the winner on purpose. An optimum at the top of a grid is
# a grid that was too short, not a result: on this data validation PR-AUC still
# climbs at 24 and 32, settles at 48, and is identical at 64 and 96 because no
# tree grows that deep. Ending on a measured plateau is what makes 48 a choice.
RF_DEPTH_GRID: tuple[int, ...] = (8, 16, 24, 32, 48, 64)

# The depth search refits the forest once per candidate. On the full training
# split that is four passes over a million rows to answer a question a
# representative subset answers just as well, so the sweep runs on a stratified
# subsample of the *training split only* and the winner is refitted on all of
# it. No row crosses a split boundary and no validation or test data is
# involved; this is not the shuffled-split anti-pattern.
RF_SWEEP_ROWS = 250_000
RF_SWEEP_MIN_PER_CLASS = 2_000

LGBM_MAX_ROUNDS = 1_000
LGBM_EARLY_STOPPING_ROUNDS = 50

# The shipped encoding, chosen from the ablation in reports/port_ablation.md.
# The two land within a percent of each other on the validation day, so the
# choice is not made on that percent -- it is made on the fact that bucketing
# asks "what kind of service is this?" instead of "which port did this lab
# happen to use?", and Phase 9 points the same model at a network whose port
# assignments are nothing like CICIDS2017's.
DEFAULT_PORT_ENCODING = PORT_ENCODING_BUCKETED

SUPERVISED_ARTIFACT = "supervised_model.pkl"
PREPROCESSING_ARTIFACT = "preprocessing.pkl"
MODEL_CARD = "model_card.json"


class NoTrainableClasses(RuntimeError):
    """Raised when the training split has no attack family above the support floor."""


@dataclass
class TrainingRun:
    """Everything the evaluation and the write-up need from one fit."""

    algorithm: str
    port_encoding: str
    version: str
    trained_at: str
    trained_on: str
    classes: list[str]
    schema_hash: str
    feature_count: int
    training_rows: int
    validation_rows: int
    class_support: dict[str, int]
    held_out_families: list[str]
    min_class_support: int
    label_mapping: str
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    depth_sweep: list[dict[str, float]] = field(default_factory=list)
    threshold: dict[str, Any] = field(default_factory=dict)
    threshold_sweep: list[dict[str, float]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def val_pr_auc(self) -> float:
        return float(self.validation.get("pr_auc", float("nan")))

    def render(self) -> str:
        lines = [
            f"algorithm        {self.algorithm}",
            f"version          {self.version}",
            f"port encoding    {self.port_encoding}",
            f"features         {self.feature_count}",
            f"classes          {', '.join(self.classes)}",
            f"training rows    {self.training_rows:,}",
            f"validation rows  {self.validation_rows:,}",
            "",
            "CLASS COLLAPSE",
            self.label_mapping,
            "",
            "VALIDATION DAY",
            f"PR-AUC           {self.val_pr_auc:.4f}   (headline)",
            f"ROC-AUC          {self.validation.get('roc_auc', float('nan')):.4f}",
            f"alerts/analyst/h {self.validation.get('alerts_per_analyst_hour', 0.0):,.1f}",
            "",
            "THRESHOLD",
            ThresholdChoice(**self.threshold).render(),
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class Prepared:
    """Feature matrices and collapsed labels for one port encoding."""

    bundle: PreprocessingBundle
    x_train: np.ndarray
    y_train: pd.Series
    x_val: np.ndarray
    y_val: pd.Series
    classes: list[str]
    train_families: pd.Series
    val_families: pd.Series
    label_mapping: str
    held_out: list[str]


def load_split(processed_dir: Path, name: str) -> pd.DataFrame:
    path = processed_dir / f"{name}.parquet"
    if not path.exists():
        raise SystemExit(f"{path} not found. Run the Phase 1 pipeline first (make data).")
    return pd.read_parquet(path)


def prepare(
    train: pd.DataFrame,
    val: pd.DataFrame,
    port_encoding: str = DEFAULT_PORT_ENCODING,
    top_n: int = DEFAULT_TOP_PORTS,
    min_class_support: int = MIN_CLASS_SUPPORT,
    bundle: PreprocessingBundle | None = None,
    exclude: Sequence[str] = (),
) -> Prepared:
    """Collapse labels, freeze the feature contract, and build both matrices.

    The scaler is fitted on the training split alone, which is what keeps the
    validation day an honest estimate of the operating point.

    ``bundle`` and ``exclude`` are what Phase 4's hold-out loop needs, and they
    go together. ``exclude`` drops whole families from the vocabulary and from
    the fit, which is the hold-out itself. ``bundle`` freezes the feature
    contract instead of refitting it, which is what keeps the hold-out to one
    variable: Stage 2 is the thing LOAO does not vary, an unchanged autoencoder
    means an unchanged input transform, and a scaler refitted per fold would
    also leave the folds unable to share a matrix. The cost is that the frozen
    scaler's medians and interquartile ranges were computed over the excluded
    family's rows as well -- column statistics, not labels -- and
    ``reports/loao.md`` states that rather than leaving it implicit.
    """
    train = train.copy()
    val = val.copy()
    train[FAMILY_COLUMN] = map_labels(train["label"])
    val[FAMILY_COLUMN] = map_labels(val["label"])

    if exclude:
        train = train[~train[FAMILY_COLUMN].isin(list(exclude))]

    label_mapping = mapping_report(train["label"], train[FAMILY_COLUMN], min_class_support)
    held_out = held_out_families(train[FAMILY_COLUMN], min_class_support)
    classes = vocabulary(train[FAMILY_COLUMN], min_class_support)
    if len(classes) < 2 or classes == [BENIGN_FAMILY]:
        raise NoTrainableClasses(
            f"the training split has no attack family with at least "
            f"{min_class_support} rows; Stage 1 has nothing to learn. "
            f"Classes found: {sorted(set(train[FAMILY_COLUMN]))}"
            + (f" (holding out {', '.join(exclude)})" if exclude else "")
        )

    fitted = train[train[FAMILY_COLUMN].isin(classes)]
    if bundle is None:
        bundle = fit_preprocessing(fitted, port_encoding=port_encoding, top_n=top_n)

    return Prepared(
        bundle=bundle,
        x_train=build_feature_matrix(fitted, bundle).to_numpy(dtype="float32"),
        y_train=fitted[FAMILY_COLUMN].astype(str).reset_index(drop=True),
        x_val=build_feature_matrix(val, bundle).to_numpy(dtype="float32"),
        y_val=val[FAMILY_COLUMN].astype(str).reset_index(drop=True),
        classes=classes,
        train_families=fitted[FAMILY_COLUMN].astype(str).reset_index(drop=True),
        val_families=val[FAMILY_COLUMN].astype(str).reset_index(drop=True),
        label_mapping=label_mapping,
        held_out=held_out,
    )


def _class_weights(y: pd.Series, classes: list[str]) -> dict[str, float]:
    """``balanced`` weights, computed once so both fitters use the same numbers."""
    from sklearn.utils.class_weight import compute_class_weight

    weights = compute_class_weight("balanced", classes=np.array(classes), y=y.to_numpy())
    return {name: float(weight) for name, weight in zip(classes, weights, strict=True)}


def _stratified_subsample(
    x: np.ndarray, y: pd.Series, rows: int, seed: int
) -> tuple[np.ndarray, pd.Series]:
    """A class-proportional slice of the training split, for the depth search only."""
    if len(y) <= rows:
        return x, y

    rng = np.random.default_rng(seed)
    fraction = rows / len(y)
    keep: list[np.ndarray] = []
    for family in sorted(set(y)):
        index = np.flatnonzero((y == family).to_numpy())
        take = min(len(index), max(int(round(len(index) * fraction)), RF_SWEEP_MIN_PER_CLASS))
        keep.append(rng.choice(index, size=take, replace=False))

    selected = np.sort(np.concatenate(keep))
    return x[selected], y.iloc[selected].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def _val_pr_auc(model_classes: list[str], proba: np.ndarray, is_attack: np.ndarray) -> float:
    from sklearn.metrics import average_precision_score

    return float(average_precision_score(is_attack, attack_confidence(proba, model_classes)))


def fit_random_forest(
    data: Prepared,
    depth_grid: tuple[int, ...],
    seed: int,
    sweep_rows: int,
) -> tuple[Any, dict[str, Any], list[dict[str, float]]]:
    """Sweep ``max_depth`` against the validation day, then refit the winner.

    The validation day carries attack families the training days do not, so the
    selection metric is the PR-AUC of the attack confidence rather than a
    multi-class loss: it is defined when the truth contains classes the model
    has no column for, and it is the quantity the operating threshold will be
    cut from.
    """
    from sklearn.ensemble import RandomForestClassifier

    is_attack = (data.val_families != BENIGN_FAMILY).to_numpy()
    x_sweep, y_sweep = _stratified_subsample(data.x_train, data.y_train, sweep_rows, seed)

    sweep: list[dict[str, float]] = []
    for depth in depth_grid:
        candidate = RandomForestClassifier(
            n_estimators=RF_ESTIMATORS,
            max_depth=depth,
            class_weight="balanced",
            n_jobs=-1,
            random_state=seed,
        ).fit(x_sweep, y_sweep)
        proba = candidate.predict_proba(data.x_val)
        score = _val_pr_auc(list(candidate.classes_), proba, is_attack)
        sweep.append({"max_depth": float(depth), "val_pr_auc": score, "sweep_rows": len(y_sweep)})
        logger.info("max_depth=%s -> validation PR-AUC %.4f", depth, score)

    best = max(sweep, key=lambda row: row["val_pr_auc"])
    depth = int(best["max_depth"])
    logger.info("selected max_depth=%d, refitting on all %d rows", depth, len(data.y_train))

    model = RandomForestClassifier(
        n_estimators=RF_ESTIMATORS,
        max_depth=depth,
        class_weight="balanced",
        n_jobs=-1,
        random_state=seed,
    ).fit(data.x_train, data.y_train)

    hyperparameters = {
        "n_estimators": RF_ESTIMATORS,
        "max_depth": depth,
        "class_weight": "balanced",
        "random_state": seed,
        "depth_grid": list(depth_grid),
        "sweep_rows": len(y_sweep),
    }
    return model, hyperparameters, sweep


def fit_lightgbm(data: Prepared, seed: int) -> tuple[Any, dict[str, Any], list[dict[str, float]]]:
    """Boosted trees with early stopping on the validation day.

    The validation set is handed to LightGBM with a *binary* attack/benign
    label and every built-in metric switched off. That is not a shortcut: the
    validation day contains families absent from the training vocabulary, so
    there is no valid multi-class label to give those rows, and a multi-class
    loss computed over the rest would be a loss over benign traffic almost
    exclusively -- which rewards a model that answers benign to everything.
    The custom metric below is the PR-AUC of the attack confidence, which is
    defined for every row and is the quantity ``tau_sup`` is cut from.
    """
    import lightgbm as lgb

    classes = data.classes
    class_index = {name: index for index, name in enumerate(classes)}
    weights = _class_weights(data.y_train, classes)

    y_train = data.y_train.map(class_index).to_numpy()
    sample_weight = data.y_train.map(weights).to_numpy()
    is_attack = (data.val_families != BENIGN_FAMILY).to_numpy()

    def as_proba(raw: np.ndarray) -> np.ndarray:
        raw = np.asarray(raw)
        if raw.ndim == 2:
            return raw
        return raw.reshape(len(classes), -1).T

    def validation_pr_auc(raw: np.ndarray, _dataset: Any) -> tuple[str, float, bool]:
        return "val_attack_pr_auc", _val_pr_auc(classes, as_proba(raw), is_attack), True

    train_set = lgb.Dataset(data.x_train, label=y_train, weight=sample_weight, free_raw_data=False)
    valid_set = lgb.Dataset(
        data.x_val, label=is_attack.astype(int), reference=train_set, free_raw_data=False
    )

    params = {
        "objective": "multiclass",
        "num_class": len(classes),
        "metric": "None",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "seed": seed,
        "verbosity": -1,
        "num_threads": 0,
    }

    booster = lgb.train(
        params,
        train_set,
        num_boost_round=LGBM_MAX_ROUNDS,
        valid_sets=[valid_set],
        valid_names=["val"],
        feval=validation_pr_auc,
        callbacks=[
            lgb.early_stopping(LGBM_EARLY_STOPPING_ROUNDS, first_metric_only=True, verbose=False),
            lgb.log_evaluation(period=50),
        ],
    )

    model = LightGBMClassifier(booster, classes)
    hyperparameters = {
        **{key: value for key, value in params.items() if key != "verbosity"},
        "num_boost_round": LGBM_MAX_ROUNDS,
        "early_stopping_rounds": LGBM_EARLY_STOPPING_ROUNDS,
        "best_iteration": int(booster.best_iteration or booster.current_iteration()),
        "class_weight": "balanced",
    }
    return model, hyperparameters, []


# The LightGBM knobs a fold inherits from the champion. Everything else in the
# recorded hyperparameters is either structural (`objective`, `num_class`) or a
# property of the search that a fold deliberately does not repeat
# (`early_stopping_rounds`, `best_iteration`).
LGBM_INHERITED_PARAMS: tuple[str, ...] = (
    "learning_rate",
    "num_leaves",
    "min_data_in_leaf",
    "feature_fraction",
    "bagging_fraction",
    "bagging_freq",
    "num_threads",
)


def fit_fixed(
    data: Prepared, algorithm: str, hyperparameters: dict[str, Any], seed: int = 7
) -> Any:
    """Fit one Stage 1 estimator with its hyperparameters already chosen.

    Phase 4's hold-out loop needs this, and the reason is not speed. LOAO has
    to change exactly one thing per fold -- whether family F was in the training
    labels -- and re-running the depth sweep or the early-stopping search would
    leave every fold differing in two ways at once. Worse, two of the held-out
    families live on the validation day, so a search measured there would let a
    fold's fit see the very rows the fold is supposed never to have met. There
    is no validation set in this function at all.

    ``num_class`` and the class weights come from ``data`` rather than from
    ``hyperparameters``: holding a family out removes a column, and a booster
    told to emit three columns for a two-class problem does not fail. It emits
    a column of noise, which ``attack_confidence`` then reads as a family's
    probability.
    """
    if algorithm == "rf":
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(
            n_estimators=int(hyperparameters.get("n_estimators", RF_ESTIMATORS)),
            max_depth=hyperparameters.get("max_depth"),
            class_weight="balanced",
            n_jobs=-1,
            random_state=seed,
        ).fit(data.x_train, data.y_train)

    if algorithm == "lgbm":
        import lightgbm as lgb

        classes = data.classes
        class_index = {name: index for index, name in enumerate(classes)}
        weights = _class_weights(data.y_train, classes)

        params: dict[str, Any] = {
            key: hyperparameters[key] for key in LGBM_INHERITED_PARAMS if key in hyperparameters
        }
        params.update(
            {
                "objective": "multiclass",
                "num_class": len(classes),
                "metric": "None",
                "seed": seed,
                "verbosity": -1,
            }
        )
        rounds = int(
            hyperparameters.get("best_iteration")
            or hyperparameters.get("num_boost_round")
            or LGBM_MAX_ROUNDS
        )
        booster = lgb.train(
            params,
            lgb.Dataset(
                data.x_train,
                label=data.y_train.map(class_index).to_numpy(),
                weight=data.y_train.map(weights).to_numpy(),
            ),
            num_boost_round=rounds,
        )
        return LightGBMClassifier(booster, classes)

    raise ValueError(f"unknown algorithm {algorithm!r}; expected one of {ALGORITHMS}")


# ---------------------------------------------------------------------------
# Threshold and validation measurement
# ---------------------------------------------------------------------------


def measure_validation(
    model: Any,
    data: Prepared,
    target_fpr: float,
    daily_flow_volume: int,
    analyst_capacity_per_hour: int,
    analyst_shift_hours: int,
) -> tuple[ThresholdChoice, dict[str, Any], list[dict[str, float]]]:
    """Choose ``tau_sup`` on the validation day and record what it costs."""
    classes = list(model.classes_)
    proba = model.predict_proba(data.x_val)
    confidence = attack_confidence(proba, classes)
    is_benign = (data.val_families == BENIGN_FAMILY).to_numpy()
    is_attack = ~is_benign

    choice = select_threshold(confidence, is_benign, target_fpr)
    volume = alert_volume(
        confidence,
        is_benign,
        choice.tau,
        daily_flow_volume,
        analyst_capacity_per_hour,
        analyst_shift_hours,
    )

    curves = detection_curves(is_attack, confidence)
    alternative = detection_curves(is_attack, attack_probability(proba, classes))

    sweep = threshold_sweep(
        confidence,
        is_benign,
        is_attack,
        [0.1, 0.25, 0.5, 0.75, 0.9, choice.tau],
        daily_flow_volume,
    )

    validation = {
        "pr_auc": curves.pr_auc,
        "roc_auc": curves.roc_auc,
        "pr_auc_one_minus_benign": alternative.pr_auc,
        "attack_rows": curves.positives,
        "benign_rows": curves.negatives,
        "attack_recall_at_tau": float((confidence[is_attack] >= choice.tau).mean())
        if is_attack.any()
        else 0.0,
        **asdict(volume),
    }
    return choice, validation, sweep


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _version(algorithm: str, now: datetime) -> str:
    return f"stage1-{algorithm}-{now:%Y%m%d%H%M}"


def write_artifacts(
    model: Any, run: TrainingRun, bundle: PreprocessingBundle, artifacts_dir: Path
) -> tuple[Path, Path]:
    """Persist the model and the preprocessing it was fitted against, as a pair.

    A model and a scaler that disagree produce confident nonsense and raise
    nothing, so the two are written together, both carrying the schema hash the
    API re-checks at startup.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    model_path = artifacts_dir / f"supervised_{run.algorithm}.pkl"
    bundle_path = artifacts_dir / f"preprocessing_{run.algorithm}.pkl"

    payload = {
        "model": model,
        "classes": run.classes,
        "algorithm": run.algorithm,
        "version": run.version,
        "schema_hash": run.schema_hash,
        "feature_order": bundle["feature_order"],
        "port_encoding": bundle["port_encoding"],
        "tau_sup": run.threshold["tau"],
        "trained_at": run.trained_at,
        "trained_on": run.trained_on,
        "training_rows": run.training_rows,
        "held_out_families": run.held_out_families,
        "hyperparameters": run.hyperparameters,
    }
    with model_path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    save_preprocessing_bundle(bundle, bundle_path)

    (artifacts_dir / f"training_{run.algorithm}.json").write_text(
        json.dumps(asdict(run), indent=2), encoding="utf-8"
    )
    return model_path, bundle_path


def read_model_card(artifacts_dir: Path) -> dict[str, Any] | None:
    path = artifacts_dir / MODEL_CARD
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _pair_schema_hash(path: Path) -> str | None:
    """The schema hash recorded inside an artifact, or None if it is unreadable."""
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            return pickle.load(handle).get("schema_hash")  # noqa: S301 - first-party
    except Exception:  # noqa: BLE001 - an unreadable artifact is a mismatch
        return None


def _publish_pair(algorithm: str, artifacts_dir: Path) -> None:
    """Copy one algorithm's model and preprocessing to the canonical names."""
    for source, destination in (
        (f"supervised_{algorithm}.pkl", SUPERVISED_ARTIFACT),
        (f"preprocessing_{algorithm}.pkl", PREPROCESSING_ARTIFACT),
    ):
        shutil.copyfile(artifacts_dir / source, artifacts_dir / destination)


def restore_champion(artifacts_dir: Path) -> bool:
    """Re-assert the recorded champion's pair as the canonical one.

    ``preprocessing.pkl`` has two authors. Phase 1 writes it from ``make data``
    under its own default port encoding, and Phase 2 overwrites it with
    whichever bundle the champion was fitted against. Running the Phase 1
    command after a model has been trained therefore leaves the canonical
    ``preprocessing.pkl`` disagreeing with the canonical
    ``supervised_model.pkl`` -- a genuinely broken pair, and one that nothing
    notices until the API refuses to start or the evaluation refuses to run.

    A training run whose challenger loses used to walk straight past that
    damage, which turned a recoverable state into a confusing crash two
    commands later. This is the repair: the champion is whatever
    ``model_card.json`` records, its own pair is still on disk under
    ``supervised_<algorithm>.pkl``, and copying it back costs a few megabytes.

    Returns True when it actually had to repair something.
    """
    card = read_model_card(artifacts_dir)
    if not card:
        return False

    algorithm = card.get("algorithm")
    expected = card.get("schema_hash")
    if not algorithm or not expected:
        return False

    model_hash = _pair_schema_hash(artifacts_dir / SUPERVISED_ARTIFACT)
    bundle_hash = _pair_schema_hash(artifacts_dir / PREPROCESSING_ARTIFACT)
    if model_hash == expected and bundle_hash == expected:
        return False

    source = artifacts_dir / f"supervised_{algorithm}.pkl"
    if not source.exists() or not (artifacts_dir / f"preprocessing_{algorithm}.pkl").exists():
        logger.warning(
            "the canonical pair does not match champion %s (model=%s bundle=%s, "
            "expected %s) and its own %s files are missing, so it cannot be "
            "restored. Retrain to rebuild it.",
            card.get("version"),
            model_hash,
            bundle_hash,
            expected,
            algorithm,
        )
        return False

    _publish_pair(algorithm, artifacts_dir)
    logger.warning(
        "restored champion %s over a mismatched canonical pair "
        "(model=%s bundle=%s, expected %s) -- something rewrote "
        "%s after the model was trained, most likely `make data`.",
        card.get("version"),
        model_hash,
        bundle_hash,
        expected,
        PREPROCESSING_ARTIFACT,
    )
    return True


def promote(run: TrainingRun, artifacts_dir: Path) -> bool:
    """Make this run the served champion if it beats the incumbent.

    The comparison is on validation PR-AUC, and the loser stays on disk under
    its own name. That is what "keep the RandomForest as a fallback" means in
    practice: a regression is a file swap, not a retrain.

    Either way this leaves the canonical pair consistent with the model card.
    Declining to promote is not a reason to leave a broken pair on disk -- see
    ``restore_champion``.
    """
    previous = read_model_card(artifacts_dir) or {}
    incumbent = previous.get("validation", {}).get("pr_auc")
    challenger = run.val_pr_auc

    if incumbent is not None and not np.isnan(incumbent) and challenger <= incumbent:
        logger.info(
            "keeping champion %s (validation PR-AUC %.4f) over %s (%.4f)",
            previous.get("version"),
            incumbent,
            run.version,
            challenger,
        )
        restore_champion(artifacts_dir)
        return False

    _publish_pair(run.algorithm, artifacts_dir)

    card = {
        "version": run.version,
        "algorithm": run.algorithm,
        "stage": "stage1_supervised",
        "trained_at": run.trained_at,
        "trained_on": run.trained_on,
        "schema_hash": run.schema_hash,
        "classes": run.classes,
        "held_out_families": run.held_out_families,
        "port_encoding": run.port_encoding,
        "hyperparameters": run.hyperparameters,
        "thresholds": {"tau_sup": run.threshold["tau"], "tau_anom": None},
        "validation": run.validation,
        "previous_champion": previous.get("version"),
        # The full run record travels with the card rather than being looked up
        # by algorithm name. A challenger that loses still leaves its
        # `training_<algorithm>.json` on disk, so reading the record by name
        # would pair the champion's model with a different run's sweep tables.
        "run": asdict(run),
    }
    (artifacts_dir / MODEL_CARD).write_text(json.dumps(card, indent=2), encoding="utf-8")
    logger.info("promoted %s to champion (validation PR-AUC %.4f)", run.version, challenger)
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def train(
    train_frame: pd.DataFrame,
    val_frame: pd.DataFrame,
    artifacts_dir: Path,
    algorithm: str = "rf",
    port_encoding: str = DEFAULT_PORT_ENCODING,
    seed: int = 7,
    depth_grid: tuple[int, ...] = RF_DEPTH_GRID,
    sweep_rows: int = RF_SWEEP_ROWS,
    min_class_support: int = MIN_CLASS_SUPPORT,
    settings: Any = None,
    bundle: PreprocessingBundle | None = None,
) -> TrainingRun:
    """Fit one Stage 1 model end to end and write its artifacts.

    ``bundle`` freezes the feature contract instead of refitting the scaler on
    ``train_frame``. Phase 7's retrain passes the served one: the scaler is
    shared with Stage 2, so a Stage 1 promotion that refitted it would move the
    autoencoder's inputs under it with the schema hash unchanged -- the one
    form of train/serve skew the hash cannot see.
    """
    if settings is None:
        from app.config import settings as default_settings

        settings = default_settings

    data = prepare(
        train_frame,
        val_frame,
        port_encoding=port_encoding,
        min_class_support=min_class_support,
        bundle=bundle,
    )
    logger.info(
        "training %s on %d rows x %d features, classes %s",
        algorithm,
        len(data.y_train),
        data.x_train.shape[1],
        data.classes,
    )

    if algorithm == "rf":
        model, hyperparameters, sweep = fit_random_forest(data, depth_grid, seed, sweep_rows)
    elif algorithm == "lgbm":
        model, hyperparameters, sweep = fit_lightgbm(data, seed)
    else:
        raise ValueError(f"unknown algorithm {algorithm!r}; expected one of {ALGORITHMS}")

    choice, validation, threshold_table = measure_validation(
        model,
        data,
        target_fpr=settings.target_fpr,
        daily_flow_volume=settings.expected_daily_flow_volume,
        analyst_capacity_per_hour=settings.analyst_capacity_per_hour,
        analyst_shift_hours=settings.analyst_shift_hours,
    )

    now = datetime.now(UTC)
    run = TrainingRun(
        algorithm=algorithm,
        port_encoding=port_encoding,
        version=_version(algorithm, now),
        trained_at=now.isoformat(timespec="seconds"),
        trained_on="CICIDS2017 Tuesday+Wednesday (data/processed/train.parquet)",
        classes=list(model.classes_),
        schema_hash=data.bundle["schema_hash"],
        feature_count=len(data.bundle["feature_order"]),
        training_rows=int(len(data.y_train)),
        validation_rows=int(len(data.y_val)),
        class_support={
            str(name): int(count) for name, count in data.y_train.value_counts().items()
        },
        held_out_families=data.held_out,
        min_class_support=min_class_support,
        label_mapping=data.label_mapping,
        hyperparameters=hyperparameters,
        depth_sweep=sweep,
        threshold=asdict(choice),
        threshold_sweep=threshold_table,
        validation=validation,
    )

    write_artifacts(model, run, data.bundle, artifacts_dir)
    return run


def port_ablation(
    train_frame: pd.DataFrame,
    val_frame: pd.DataFrame,
    reports_dir: Path,
    algorithm: str = "rf",
    seed: int = 7,
    depth_grid: tuple[int, ...] = RF_DEPTH_GRID,
    sweep_rows: int = RF_SWEEP_ROWS,
    settings: Any = None,
) -> dict[str, TrainingRun]:
    """Train once with the raw destination port and once with it bucketed.

    Phase 1 defined both encodings and deferred the comparison to here. The
    question is not which scores higher but whether the raw port scores *much*
    higher: destination port is genuinely predictive and also a memorisation
    trap, and a large gain from the raw value is evidence the model learned the
    lab's port assignments rather than attack behaviour.

    Scored on the validation day. A feature-encoding decision made on the test
    day is a decision that has already spent the test day.
    """
    runs: dict[str, TrainingRun] = {}
    with tempfile.TemporaryDirectory(prefix="recluse-port-ablation-") as scratch:
        for encoding in PORT_ENCODING_STRATEGIES:
            runs[encoding] = train(
                train_frame,
                val_frame,
                artifacts_dir=Path(scratch) / encoding,
                algorithm=algorithm,
                port_encoding=encoding,
                seed=seed,
                depth_grid=depth_grid,
                sweep_rows=sweep_rows,
                settings=settings,
            )

    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "port_ablation.md").write_text(
        render_port_ablation(runs, algorithm), encoding="utf-8"
    )
    return runs


def render_port_ablation(runs: dict[str, TrainingRun], algorithm: str) -> str:
    raw = runs[PORT_ENCODING_RAW]
    bucketed = runs[PORT_ENCODING_BUCKETED]
    gain = raw.val_pr_auc - bucketed.val_pr_auc
    relative = (gain / bucketed.val_pr_auc * 100) if bucketed.val_pr_auc else float("nan")

    verdict = (
        "The raw port gives a large gain, and that gain is suspect: it is the "
        "size of improvement you get from memorising which ports this lab "
        "assigned to which activity, not from learning attack behaviour."
        if relative > 10
        else "The two encodings land close together, so the raw destination port "
        "is not carrying the model. The bucketed encoding is the safer default "
        "for traffic whose port assignments differ from this lab's."
    )

    return "\n".join(
        [
            "# Destination-port ablation",
            "",
            f"`{algorithm}`, trained twice on CICIDS2017 Tuesday+Wednesday and scored "
            "on the Thursday validation day. Everything except the destination-port "
            "encoding is identical.",
            "",
            "| Encoding | Features | Validation PR-AUC | ROC-AUC | tau_sup | FPR at tau | "
            "Attack recall at tau |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *[
                f"| `{run.port_encoding}` | {run.feature_count} | **{run.val_pr_auc:.4f}** | "
                f"{run.validation['roc_auc']:.4f} | {run.threshold['tau']:.4f} | "
                f"{run.threshold['fpr']:.2e} | {run.validation['attack_recall_at_tau']:.1%} |"
                for run in (raw, bucketed)
            ],
            "",
            f"Raw minus bucketed: **{gain:+.4f} PR-AUC** ({relative:+.1f}% relative).",
            "",
            verdict,
            "",
            "The bucketed encoding replaces the raw port with its IANA service group "
            "(well-known / registered / ephemeral) plus a one-hot for the twenty most "
            "frequent ports, fitted on the training split alone so a port first seen at "
            "serving time cannot widen the feature matrix under a trained model.",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="train_supervised.py",
        description="Train Stage 1, the supervised classifier.",
    )
    parser.add_argument("--algorithm", choices=ALGORITHMS, default="rf")
    parser.add_argument("--port-encoding", choices=PORT_ENCODING_STRATEGIES, default=None)
    parser.add_argument(
        "--port-ablation",
        action="store_true",
        help="Train both port encodings and write reports/port_ablation.md instead",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--depth-grid",
        default=",".join(str(depth) for depth in RF_DEPTH_GRID),
        help="Comma-separated max_depth candidates for the RandomForest sweep",
    )
    parser.add_argument("--sweep-rows", type=int, default=RF_SWEEP_ROWS)
    parser.add_argument("--min-class-support", type=int, default=MIN_CLASS_SUPPORT)
    parser.add_argument("--no-promote", action="store_true")
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    processed_dir = args.processed_dir or settings.data_path / "processed"
    artifacts_dir = args.artifacts_dir or settings.artifacts_path
    reports_dir = args.reports_dir or settings.reports_path
    depth_grid = tuple(int(value) for value in args.depth_grid.split(",") if value.strip())

    train_frame = load_split(processed_dir, "train")
    val_frame = load_split(processed_dir, "val")

    if args.port_ablation:
        runs = port_ablation(
            train_frame,
            val_frame,
            reports_dir=reports_dir,
            algorithm=args.algorithm,
            seed=args.seed,
            depth_grid=depth_grid,
            sweep_rows=args.sweep_rows,
            settings=settings,
        )
        echo(render_port_ablation(runs, args.algorithm))
        return 0

    run = train(
        train_frame,
        val_frame,
        artifacts_dir=artifacts_dir,
        algorithm=args.algorithm,
        port_encoding=args.port_encoding or DEFAULT_PORT_ENCODING,
        seed=args.seed,
        depth_grid=depth_grid,
        sweep_rows=args.sweep_rows,
        min_class_support=args.min_class_support,
        settings=settings,
    )

    echo(run.render())
    if not args.no_promote:
        promoted = promote(run, artifacts_dir)
        echo(f"\nchampion         {'promoted' if promoted else 'unchanged'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
