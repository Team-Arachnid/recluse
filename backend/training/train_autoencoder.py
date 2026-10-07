"""Phase 3 -- Model B, the anomaly detector.

Trains on benign rows only: Monday in full, plus the benign rows of Tuesday and
Wednesday, which is the split Phase 1 already wrote and asserted attack-free.
This module asserts it again before touching the optimiser. Not because Phase 1
is suspected, but because the cost of being wrong is silent: an autoencoder that
has seen an attack learns to reconstruct it, stops flagging it, and the
project's central claim quietly stops being true without a single exception
being raised.

Rules this module holds to:

* **No label reaches the fit.** The label column is read exactly twice -- once
  to assert the training set is benign, and once to measure what the finished
  model does to attack traffic. Never in between.
* **The feature contract is Stage 1's.** Stage 2 is fitted against the
  canonical ``preprocessing.pkl``, the same bundle the champion was fitted
  against and the same one the API loads. At serve time there is one feature
  matrix and both stages read it; a Stage 2 trained on its own scaling would
  score confident nonsense at serving time and raise nothing.
* **``tau_anom`` is a percentile, not a tuning.** The 99.5th of reconstruction
  error on the held-out validation day's benign rows. Benign-only, on a day the
  network never trained on.
* **Baselines run, and a baseline that wins is reported.** IsolationForest, LOF
  and ECOD on the same rows, on the same input, fitted benign-only. A neural
  network that ties an IsolationForest is a neural network that should not be in
  the system.
* **Nothing is chosen on the test day.** The input transform and its clip bound
  come off the validation day, over three seeds, in ``--input-ablation``. The
  test day is opened once, after every decision has already been made.

One thing about this phase was not in the plan, and it is the reason
``autoencoder.prepare_input`` exists. The shared matrix carries columns whose
interquartile range is zero, which ``RobustScaler`` leaves unscaled -- and one of
them accounted for 93.9% of the magnitude an MSE loss could see, which made the
first run's score a proxy for that column and ranked attack traffic *below*
benign traffic. See ``prepare_input`` and ``reports/input_ablation.md``.

Artifacts: ``autoencoder.pt`` (a bare state dict), ``training_autoencoder.json``,
``metrics_anomaly.json``, ``tau_anom`` and the benign error histogram into
``model_card.json``, and ``reports/phase3_anomaly.md``.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from training.autoencoder import (
    ENCODER_DROPOUT,
    ENCODER_WIDTHS,
    INPUT_CLIP,
    Autoencoder,
    per_feature_error,
    prepare_input,
    reconstruction_error,
    top_contributors,
)
from training.console import echo
from training.features import build_feature_matrix, load_preprocessing_bundle
from training.labels import BENIGN_FAMILY, FAMILIES, map_labels
from training.metrics import (
    ANOMALY_PERCENTILE,
    DEFAULT_HISTOGRAM_BINS,
    AnomalyThreshold,
    ErrorHistogram,
    alert_volume,
    detection_curves,
    error_histogram,
    log_bin_edges,
    recall_by_family,
    render_histograms,
    select_anomaly_threshold,
)
from training.split import AttackInBenignTrainingSet
from training.train_supervised import load_split

logger = logging.getLogger(__name__)

AUTOENCODER_ARTIFACT = "autoencoder.pt"
METRICS_ARTIFACT = "metrics_anomaly.json"
RUN_ARTIFACT = "training_autoencoder.json"
MODEL_CARD = "model_card.json"
REPORT_FILENAME = "phase3_anomaly.md"

ANOMALY_ALGORITHM = "autoencoder"

# Training. A network of seventeen thousand parameters over a million rows
# converges in tens of epochs, so the ceiling is a guard against a pathological
# run rather than a target -- early stopping is what actually ends training.
MAX_EPOCHS = 60
EARLY_STOPPING_PATIENCE = 6
BATCH_ROWS = 1_024
LEARNING_RATE = 1e-3

# A tenth of the benign training rows are held back to early-stop on. They come
# from the same days as the fit, which is what makes the comparison a test of
# overfitting rather than of domain shift -- the validation day answers that
# separate question, and it is where tau_anom is cut.
EARLY_STOPPING_FRACTION = 0.1

# The baselines are quadratic or near it. LOF over a million reference rows does
# not finish, so every detector -- the autoencoder included -- is compared on
# the same subsample, and the autoencoder's own numbers are reported separately
# on the full splits.
BASELINE_FIT_ROWS = 40_000
BASELINE_ARENA_BENIGN_ROWS = 40_000

# Per-family reconstruction explanations are a mean over a sample. Five thousand
# rows fixes a mean to more decimal places than the report prints.
EXPLANATION_SAMPLE_ROWS = 5_000

# The input ablation. Short runs on a subsample, because what is being compared
# is the ordering of five representations rather than any one of their final
# numbers -- and three seeds rather than one because a single seed picks a
# different winner. `None` means log1p with no clip at all.
INPUT_ABLATION_BOUNDS: tuple[float | None, ...] = (None, 4.0, 6.0, 8.0, 12.0)
INPUT_ABLATION_SEEDS: tuple[int, ...] = (7, 13, 29)
INPUT_ABLATION_ROWS = 200_000
INPUT_ABLATION_EPOCHS = 12

# Stands in for "no clip" when the bound has to be a number. log1p of the largest
# value the scaled matrix carries is about 18, so nothing is clipped at 64.
NO_CLIP = 64.0

SEED = 7


class MissingChampion(RuntimeError):
    """Raised when Stage 2 is asked to train against artifacts that do not exist.

    Stage 2 does not need Stage 1's model, but it does need Stage 1's feature
    contract: the API builds one matrix per batch and feeds it to both stages,
    so a Stage 2 fitted against a different scaler or column order is a Stage 2
    that scores garbage in production. ``tau_anom`` also has nowhere to live but
    the model card, which Phase 2 writes.
    """


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class BenignData:
    """The fit set, the early-stopping slice, and how they were obtained.

    Both matrices are in *shared* space -- straight out of
    ``build_feature_matrix``, before ``prepare_input``. That is deliberate:
    ``prepare_input`` is not idempotent, so the one thing that must not happen
    is two places applying it to the same rows. Scoring applies it itself, and
    ``fit_autoencoder`` applies it once at the top, which leaves these fields
    safe to hand to either.
    """

    x_fit: np.ndarray
    x_early_stop: np.ndarray
    source_rows: int
    duplicate_rows: int

    @property
    def input_dim(self) -> int:
        return int(self.x_fit.shape[1])

    @property
    def retained_rows(self) -> int:
        """Rows that survived deduplication: the fit set plus the held slice."""
        return int(len(self.x_fit) + len(self.x_early_stop))


def assert_attack_free(frame: pd.DataFrame) -> int:
    """Refuse to train on anything but benign rows. Fatal, never a warning.

    Phase 1 already guarantees this and raises the same exception if it ever
    stops being true. Re-checking here costs one pass over a label column and
    removes the possibility that a hand-edited Parquet file, or a future change
    to the splitting rules, silently turns Stage 2 into a supervised model with
    no labels.
    """
    families = map_labels(frame["label"]).astype(str)
    contaminated = families[families != BENIGN_FAMILY]
    if not contaminated.empty:
        found = sorted(set(contaminated))
        raise AttackInBenignTrainingSet(
            f"{len(contaminated):,} attack row(s) in the Stage 2 training set: {found}. "
            "The autoencoder's entire claim is that it has never seen an attack, so "
            "this is fatal rather than a warning. Re-run the Phase 1 split."
        )
    return int(len(families))


def prepare_benign(
    benign_train: pd.DataFrame,
    bundle: dict[str, Any],
    early_stopping_fraction: float = EARLY_STOPPING_FRACTION,
    seed: int = SEED,
) -> BenignData:
    """Build the benign feature matrix and split off an early-stopping slice.

    Exact duplicates go first. Phase 1 drops them within each capture file and
    across the supervised splits, but the benign-only set is assembled from
    three days after that pass, so a benign flow that appears identically on
    Monday and on Tuesday survives twice. Left in, it would weight those rows
    double in the fit and -- worse -- put the same row on both sides of the
    early-stopping split, which makes the validation loss optimistic and stops
    training later than it should.
    """
    before = len(benign_train)
    deduplicated = benign_train.drop_duplicates().reset_index(drop=True)
    duplicates = before - len(deduplicated)
    if duplicates:
        logger.info("dropped %d exact duplicate benign row(s) of %d", duplicates, before)

    matrix = build_feature_matrix(deduplicated, bundle).to_numpy(dtype="float32")

    rng = np.random.default_rng(seed)
    held = rng.permutation(len(matrix))
    cut = int(len(matrix) * early_stopping_fraction)
    if cut < 2:
        raise ValueError(
            f"{len(matrix)} benign rows cannot yield an early-stopping slice at "
            f"fraction {early_stopping_fraction}; batch norm needs at least two rows"
        )

    return BenignData(
        x_fit=matrix[held[cut:]],
        x_early_stop=matrix[held[:cut]],
        source_rows=before,
        duplicate_rows=duplicates,
    )


def load_artifacts(artifacts_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The canonical preprocessing bundle and the model card, or a loud refusal."""
    bundle_path = artifacts_dir / "preprocessing.pkl"
    card_path = artifacts_dir / MODEL_CARD
    if not bundle_path.exists():
        raise MissingChampion(
            f"{bundle_path} not found. Stage 2 is fitted against the same feature "
            "contract Stage 1 serves under, so the Phase 1 pipeline has to have run "
            "first (make data)."
        )
    if not card_path.exists():
        raise MissingChampion(
            f"{card_path} not found. tau_anom and the benign error histogram are "
            "recorded in the model card, which Phase 2 writes, so train Stage 1 "
            "first (make train)."
        )

    bundle = load_preprocessing_bundle(bundle_path)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    if card.get("schema_hash") != bundle.get("schema_hash"):
        raise MissingChampion(
            f"champion {card.get('version')} was trained against schema "
            f"{card.get('schema_hash')} but preprocessing.pkl carries "
            f"{bundle.get('schema_hash')}. The canonical pair is mismatched; "
            "re-run the Phase 2 training to repair it (make train)."
        )
    return bundle, card


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


def fit_autoencoder(
    data: BenignData,
    widths: tuple[int, ...] = ENCODER_WIDTHS,
    dropout: float = ENCODER_DROPOUT,
    max_epochs: int = MAX_EPOCHS,
    patience: int = EARLY_STOPPING_PATIENCE,
    batch_rows: int = BATCH_ROWS,
    learning_rate: float = LEARNING_RATE,
    seed: int = SEED,
    clip: float = INPUT_CLIP,
    initial_state: dict[str, Any] | None = None,
) -> tuple[Autoencoder, dict[str, Any], list[dict[str, float]]]:
    """Fit on benign rows, early-stopping on held-out benign reconstruction loss.

    MSE is both the loss and the score, so the quantity being minimised here is
    the quantity ``tau_anom`` will be cut from. There is no separate objective
    to reconcile.

    The best epoch's weights are restored before returning. Stopping at the
    epoch where patience ran out would ship a model six epochs past its own
    best validation loss.

    ``prepare_input`` is applied here, once, and this is the only place in the
    project that does it outside the scoring path. It has to be: the network
    reconstructs toward its input, so the target has to be compressed too, and
    compressing inside ``forward`` would leave the loss comparing a compressed
    reconstruction against an uncompressed target.

    ``initial_state`` starts from a trained network instead of from random
    weights -- the Phase 7 benign-baseline refit, which moves the champion
    toward recently confirmed-benign traffic rather than relearning normal from
    nothing. Its geometry wins over ``widths``: a refit that silently changed
    the architecture would not be a refit of the model that is serving.
    """
    import torch
    from torch import nn

    torch.manual_seed(seed)
    if initial_state is not None:
        input_dim, widths = Autoencoder.geometry(initial_state)
        if input_dim != data.input_dim:
            raise ValueError(
                f"the starting weights take {input_dim} features but the refit data "
                f"has {data.input_dim}; they come from different feature contracts"
            )
    model = Autoencoder(input_dim=data.input_dim, widths=widths, dropout=dropout)
    if initial_state is not None:
        model.load_state_dict(initial_state)
    optimiser = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.MSELoss()

    x_fit = torch.from_numpy(prepare_input(data.x_fit, clip=clip))
    x_val = torch.from_numpy(prepare_input(data.x_early_stop, clip=clip))
    generator = np.random.default_rng(seed)

    history: list[dict[str, float]] = []
    best_loss = float("inf")
    best_epoch = 0
    best_state: dict[str, Any] = copy.deepcopy(model.state_dict())

    for epoch in range(1, max_epochs + 1):
        model.train()
        order = generator.permutation(len(x_fit))
        total = 0.0
        seen = 0
        for start in range(0, len(order), batch_rows):
            index = order[start : start + batch_rows]
            # Batch norm cannot compute a batch statistic from one row, and the
            # last batch of an epoch is whatever is left over.
            if len(index) < 2:
                continue
            batch = x_fit[index]
            optimiser.zero_grad()
            loss = criterion(model(batch), batch)
            loss.backward()
            optimiser.step()
            total += float(loss.item()) * len(index)
            seen += len(index)

        model.eval()
        with torch.no_grad():
            validation = float(criterion(model(x_val), x_val).item())
        train_loss = total / max(seen, 1)
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": validation})
        logger.info("epoch %2d  train %.6f  benign val %.6f", epoch, train_loss, validation)

        if validation < best_loss:
            best_loss, best_epoch = validation, epoch
            best_state = copy.deepcopy(model.state_dict())
        elif epoch - best_epoch >= patience:
            logger.info(
                "no improvement on benign validation loss for %d epochs; "
                "stopping and restoring epoch %d (%.6f)",
                patience,
                best_epoch,
                best_loss,
            )
            break

    model.load_state_dict(best_state)
    model.eval()

    hyperparameters = {
        "architecture": model.architecture(),
        "encoder_widths": list(widths),
        "bottleneck": model.bottleneck,
        "input_transform": f"sign(x) * log1p(|x|), clipped to +/-{clip:g}",
        "input_clip": clip,
        "dropout": dropout,
        "dropout_scope": "encoder hidden layers, not the bottleneck",
        "batch_norm": True,
        "activation": "relu",
        "output_activation": None,
        "loss": "mse",
        "optimiser": "adam",
        "learning_rate": learning_rate,
        "batch_rows": batch_rows,
        "max_epochs": max_epochs,
        "early_stopping_patience": patience,
        "epochs_run": len(history),
        "best_epoch": best_epoch,
        "best_val_loss": best_loss,
        "parameters": int(sum(p.numel() for p in model.parameters())),
        "seed": seed,
    }
    return model, hyperparameters, history


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


@dataclass
class BaselineResult:
    """One detector's separation on the shared arena."""

    name: str
    library: str
    pr_auc: float
    roc_auc: float
    fit_rows: int
    note: str = ""


def _subsample(matrix: np.ndarray, rows: int, seed: int) -> np.ndarray:
    if len(matrix) <= rows:
        return matrix
    return matrix[np.random.default_rng(seed).choice(len(matrix), size=rows, replace=False)]


def run_baselines(
    x_fit: np.ndarray,
    arena: np.ndarray,
    is_attack: np.ndarray,
    fit_rows: int = BASELINE_FIT_ROWS,
    seed: int = SEED,
) -> list[BaselineResult]:
    """IsolationForest, LOF and ECOD on the same benign rows and the same arena.

    They are here to answer whether the autoencoder earns its complexity, and
    the answer is allowed to be no. A baseline that wins is a finding: the
    two-stage design does not depend on Stage 2 being a neural network, and a
    project that reports "ECOD beat our autoencoder and here is where" is more
    credible than one that drops the comparison.

    Every one of them is fitted on benign rows only, the same discipline the
    autoencoder is held to, and every one of them sees the same
    ``prepare_input`` output the autoencoder does. Handing the baselines the raw
    scaled matrix would flatter the autoencoder for free: LOF is a Euclidean
    method and IsolationForest partitions axis by axis, so both would be pulled
    apart by the same zero-IQR column that broke the network. An autoencoder
    that needed a handicapped LOF to look good would not be worth shipping.
    """
    from sklearn.ensemble import IsolationForest
    from sklearn.neighbors import LocalOutlierFactor

    reference = prepare_input(_subsample(x_fit, fit_rows, seed))
    arena = prepare_input(arena)
    results: list[BaselineResult] = []

    def measure(name: str, library: str, scores: np.ndarray, note: str = "") -> None:
        curves = detection_curves(is_attack, scores)
        results.append(
            BaselineResult(
                name=name,
                library=library,
                pr_auc=curves.pr_auc,
                roc_auc=curves.roc_auc,
                fit_rows=len(reference),
                note=note,
            )
        )
        logger.info("%-18s PR-AUC %.4f  ROC-AUC %.4f", name, curves.pr_auc, curves.roc_auc)

    forest = IsolationForest(n_estimators=200, random_state=seed, n_jobs=-1).fit(reference)
    # `score_samples` is higher for inliers; every detector here has to report
    # "more anomalous is larger" or the curves come out inverted.
    measure("IsolationForest", "scikit-learn", -forest.score_samples(arena))

    lof = LocalOutlierFactor(n_neighbors=20, novelty=True).fit(reference)
    measure(
        "LOF",
        "scikit-learn",
        -lof.score_samples(arena),
        note="novelty=True, so the benign rows are the reference set rather than the scored set",
    )

    try:
        from pyod.models.ecod import ECOD
    except ImportError:  # pragma: no cover - pyod is a declared dependency
        logger.warning(
            "pyod is not installed, so ECOD is skipped; install it or pass --no-baselines"
        )
    else:
        ecod = ECOD()
        ecod.fit(reference)
        measure("ECOD", "pyod", np.asarray(ecod.decision_function(arena)))

    return results


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


@dataclass
class SplitScores:
    """Reconstruction error on one split, with the labels kept beside it."""

    name: str
    errors: np.ndarray
    families: pd.Series

    @property
    def is_attack(self) -> np.ndarray:
        return (self.families != BENIGN_FAMILY).to_numpy()

    @property
    def benign_errors(self) -> np.ndarray:
        return self.errors[~self.is_attack]

    @property
    def attack_errors(self) -> np.ndarray:
        return self.errors[self.is_attack]


def score_split(
    model: Autoencoder, frame: pd.DataFrame, bundle: dict[str, Any], name: str
) -> SplitScores:
    """Score a labelled split. The labels are read after scoring, never before."""
    matrix = build_feature_matrix(frame, bundle).to_numpy(dtype="float32")
    return SplitScores(
        name=name,
        errors=reconstruction_error(model, matrix),
        families=map_labels(frame["label"]).astype(str).reset_index(drop=True),
    )


def family_explanations(
    model: Autoencoder,
    frame: pd.DataFrame,
    bundle: dict[str, Any],
    families: pd.Series,
    feature_order: list[str],
    sample_rows: int = EXPLANATION_SAMPLE_ROWS,
    seed: int = SEED,
) -> dict[str, list[dict[str, float | str]]]:
    """Which features the network fails hardest to reconstruct, per family.

    This is the Stage 2 explanation in aggregate. It is worth computing in the
    training report and not only per alert, because it is the evidence that the
    detector is responding to attack *behaviour* rather than to one artefact of
    the capture: a port scan whose worst-reconstructed features are packet
    counts and inter-arrival times is a port scan the model noticed for the
    right reasons.
    """
    rng = np.random.default_rng(seed)
    explanations: dict[str, list[dict[str, float | str]]] = {}
    for family in [BENIGN_FAMILY, *(name for name in FAMILIES if name != BENIGN_FAMILY)]:
        index = np.flatnonzero((families == family).to_numpy())
        if index.size == 0:
            continue
        if index.size > sample_rows:
            index = rng.choice(index, size=sample_rows, replace=False)
        sample = build_feature_matrix(frame.iloc[index], bundle).to_numpy(dtype="float32")
        mean_error = per_feature_error(model, sample).mean(axis=0)
        explanations[family] = top_contributors(mean_error, feature_order)
    return explanations


@dataclass
class AnomalyRun:
    """Everything the report, the metrics file and the model card need."""

    version: str
    algorithm: str
    trained_at: str
    trained_on: str
    schema_hash: str
    feature_count: int
    source_rows: int
    duplicate_rows: int
    fit_rows: int
    early_stopping_rows: int
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, float]] = field(default_factory=list)
    threshold: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    test: dict[str, Any] = field(default_factory=dict)
    baselines: list[dict[str, Any]] = field(default_factory=list)
    arena: dict[str, Any] = field(default_factory=dict)
    histograms: dict[str, Any] = field(default_factory=dict)
    explanations: dict[str, Any] = field(default_factory=dict)
    # Stage 1's per-family recall on the same day, lifted from the model card so
    # the comparison in the report is a measurement rather than a recollection.
    # `evaluate.py` puts it there; an unevaluated champion simply leaves it empty.
    stage1_family_recall: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        volume = self.test.get("volume", {})
        lines = [
            f"algorithm        {self.algorithm}",
            f"version          {self.version}",
            f"architecture     {self.hyperparameters.get('architecture')}",
            f"parameters       {self.hyperparameters.get('parameters'):,}",
            f"features         {self.feature_count}",
            f"benign rows      {self.fit_rows:,} fitted, "
            f"{self.early_stopping_rows:,} held for early stopping",
            f"epochs           {self.hyperparameters.get('epochs_run')} run, "
            f"best at {self.hyperparameters.get('best_epoch')} "
            f"(benign val loss {self.hyperparameters.get('best_val_loss', float('nan')):.6f})",
            "",
            "THRESHOLD",
            AnomalyThreshold(**self.threshold).render(),
            "",
            "TEST DAY (Friday)",
            f"PR-AUC           {self.test.get('pr_auc', float('nan')):.4f}   (headline)",
            f"ROC-AUC          {self.test.get('roc_auc', float('nan')):.4f}",
            f"attack recall    {self.test.get('attack_recall', float('nan')):.1%} at tau_anom",
            f"FPR              {volume.get('fpr', float('nan')):.2e}",
            f"alerts/analyst/h {volume.get('alerts_per_analyst_hour', 0.0):,.1f}",
        ]
        if self.baselines:
            lines += ["", "BASELINES (shared arena)"]
            lines += [
                f"{row['name']:<18} PR-AUC {row['pr_auc']:.4f}  ROC-AUC {row['roc_auc']:.4f}"
                for row in self.baselines
            ]
        return "\n".join(lines)


def _version(now: datetime) -> str:
    return f"stage2-autoencoder-{now:%Y%m%d%H%M}"


def measure(
    model: Autoencoder,
    data: BenignData,
    validation: SplitScores,
    test: SplitScores,
    bundle: dict[str, Any],
    test_frame: pd.DataFrame,
    settings: Any,
    hyperparameters: dict[str, Any],
    history: list[dict[str, float]],
    card: dict[str, Any] | None = None,
    percentile: float = ANOMALY_PERCENTILE,
    histogram_bins: int = DEFAULT_HISTOGRAM_BINS,
    baselines: list[BaselineResult] | None = None,
    arena: dict[str, Any] | None = None,
    seed: int = SEED,
) -> AnomalyRun:
    """Cut ``tau_anom`` on the validation day, then measure the test day once."""
    threshold = select_anomaly_threshold(
        validation.benign_errors,
        percentile=percentile,
        calibrated_on="the Thursday validation day's benign rows",
        target_fpr=settings.target_fpr,
    )

    edges = log_bin_edges(
        validation.benign_errors, test.benign_errors, test.attack_errors, bins=histogram_bins
    )
    histograms = {
        "edges": edges,
        "spacing": "log",
        "validation_benign": error_histogram(validation.benign_errors, edges),
        "test_benign": error_histogram(test.benign_errors, edges),
        "test_attack": error_histogram(test.attack_errors, edges),
    }

    flagged = test.errors >= threshold.tau
    curves = detection_curves(test.is_attack, test.errors)
    volume = alert_volume(
        test.errors,
        ~test.is_attack,
        threshold.tau,
        settings.expected_daily_flow_volume,
        settings.analyst_capacity_per_hour,
        settings.analyst_shift_hours,
    )
    labels = [family for family in FAMILIES if family in set(test.families)]
    validation_curves = detection_curves(validation.is_attack, validation.errors)

    now = datetime.now(UTC)
    return AnomalyRun(
        version=_version(now),
        algorithm=ANOMALY_ALGORITHM,
        trained_at=now.isoformat(timespec="seconds"),
        trained_on=(
            "CICIDS2017 benign rows only: Monday in full plus the benign rows of "
            "Tuesday and Wednesday (data/processed/benign_train.parquet)"
        ),
        schema_hash=str(bundle["schema_hash"]),
        feature_count=len(bundle["feature_order"]),
        source_rows=data.source_rows,
        duplicate_rows=data.duplicate_rows,
        fit_rows=int(len(data.x_fit)),
        early_stopping_rows=int(len(data.x_early_stop)),
        threshold=asdict(threshold),
        calibration={
            "split": "val",
            "benign_rows": int(len(validation.benign_errors)),
            "attack_rows": int(len(validation.attack_errors)),
            "pr_auc": validation_curves.pr_auc,
            "roc_auc": validation_curves.roc_auc,
            "attack_recall": float((validation.attack_errors >= threshold.tau).mean())
            if validation.attack_errors.size
            else 0.0,
        },
        test={
            "split": "test",
            "rows": int(len(test.errors)),
            "benign_rows": int((~test.is_attack).sum()),
            "attack_rows": int(test.is_attack.sum()),
            "pr_auc": curves.pr_auc,
            "roc_auc": curves.roc_auc,
            "pr_curve": curves.pr_curve,
            "roc_curve": curves.roc_curve,
            "attack_recall": float(flagged[test.is_attack].mean()) if test.is_attack.any() else 0.0,
            "family_recall": recall_by_family(test.families, flagged, labels),
            "volume": asdict(volume),
            "median_benign_error": float(np.median(test.benign_errors)),
            "median_attack_error": float(np.median(test.attack_errors))
            if test.attack_errors.size
            else float("nan"),
        },
        baselines=[asdict(result) for result in (baselines or [])],
        arena=arena or {},
        histograms=histograms,
        explanations=family_explanations(
            model, test_frame, bundle, test.families, list(bundle["feature_order"]), seed=seed
        ),
        hyperparameters=hyperparameters,
        history=history,
        stage1_family_recall=((card or {}).get("test") or {}).get("family_recall") or {},
    )


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def _histograms_as_json(histograms: dict[str, Any]) -> dict[str, Any]:
    return {
        key: asdict(value) if isinstance(value, ErrorHistogram) else value
        for key, value in histograms.items()
    }


def write_artifacts(
    model: Autoencoder, run: AnomalyRun, artifacts_dir: Path, settings: Any
) -> tuple[Path, Path]:
    """Persist the weights, the run record and the metrics payload.

    ``autoencoder.pt`` stays a bare state dict rather than a checkpoint dict.
    That is what lets ``app/inference.py`` read it with ``weights_only=True``,
    which makes the file data rather than code; the architecture is recovered
    from the tensor shapes by ``Autoencoder.from_state_dict``.
    """
    import torch

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    weights_path = artifacts_dir / AUTOENCODER_ARTIFACT
    torch.save(model.state_dict(), weights_path)

    payload = asdict(run)
    payload["histograms"] = _histograms_as_json(run.histograms)
    (artifacts_dir / RUN_ARTIFACT).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Same shape as metrics_supervised.json, because Phase 5 serves both from
    # one endpoint and a second layout would be a second parser.
    metrics_path = artifacts_dir / METRICS_ARTIFACT
    metrics_path.write_text(
        json.dumps(
            {
                "stage": "stage2_anomaly",
                "budget": {
                    "expected_daily_flow_volume": settings.expected_daily_flow_volume,
                    "analyst_capacity_per_hour": settings.analyst_capacity_per_hour,
                    "analyst_shift_hours": settings.analyst_shift_hours,
                    "max_alerts_per_day": settings.max_alerts_per_day,
                    "target_fpr": settings.target_fpr,
                },
                "training": {key: value for key, value in payload.items() if key != "test"},
                "test": run.test,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return weights_path, metrics_path


def update_model_card(run: AnomalyRun, artifacts_dir: Path) -> Path:
    """Record ``tau_anom``, the benign histogram and Stage 2's version on the card.

    The card is where both live because ``autoencoder.pt`` is a bare state dict
    with nowhere to put them, and because ``app/inference.py`` already reads the
    card for exactly this. Stage 1's entries are untouched: this is an addition
    to the champion's record, not a new one.
    """
    card_path = artifacts_dir / MODEL_CARD
    card = json.loads(card_path.read_text(encoding="utf-8"))
    if card.get("schema_hash") != run.schema_hash:
        raise MissingChampion(
            f"{MODEL_CARD} carries schema {card.get('schema_hash')} but Stage 2 was "
            f"fitted against {run.schema_hash}. Something rewrote the canonical "
            "preprocessing bundle mid-run; retrain rather than record this."
        )

    thresholds = dict(card.get("thresholds") or {})
    thresholds["tau_anom"] = run.threshold["tau"]
    card["thresholds"] = thresholds
    card["anomaly_algorithm"] = run.algorithm
    card["stage2"] = {
        "version": run.version,
        "algorithm": run.algorithm,
        "trained_at": run.trained_at,
        "trained_on": run.trained_on,
        "architecture": run.hyperparameters.get("architecture"),
        "parameters": run.hyperparameters.get("parameters"),
        "fit_rows": run.fit_rows,
        "threshold": run.threshold,
        "benign_error_histogram": asdict(run.histograms["validation_benign"]),
        "test": {
            key: run.test[key]
            for key in ("pr_auc", "roc_auc", "attack_recall", "family_recall")
            if key in run.test
        },
        "baselines": run.baselines,
    }
    card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
    return card_path


# ---------------------------------------------------------------------------
# The write-up
# ---------------------------------------------------------------------------


def _separation_verdict(run: AnomalyRun) -> str:
    """The checkpoint, stated as a pass or a failure, from the measured numbers.

    The brief is blunt about this one: if the distributions do not separate, the
    model is not working and no dashboard will hide it. So the verdict is
    generated rather than written, and a rerun that moves the numbers cannot
    leave a stale claim behind.
    """
    recall = run.test.get("attack_recall", 0.0)
    roc = run.test.get("roc_auc", float("nan"))
    benign = run.test.get("median_benign_error", float("nan"))
    attack = run.test.get("median_attack_error", float("nan"))
    ratio = attack / benign if benign else float("nan")

    # The medians can land either way round, and they do: a detector can rank
    # well overall while the *typical* attack flow reconstructs no worse than
    # the typical benign one, because the separation lives in the tail.
    if np.isnan(ratio):
        medians = "The median flows cannot be compared on this split"
    elif ratio >= 1.05:
        medians = (
            f"The median attack flow reconstructs {ratio:.1f}x worse than the median benign one"
        )
    elif ratio <= 0.95:
        medians = (
            f"The median attack flow reconstructs *better* than the median benign one "
            f"({1 / ratio:.1f}x lower error), so whatever separation there is lives in the "
            f"tail rather than in the middle of the distribution"
        )
    else:
        medians = (
            "The two medians are within 5% of each other, so the separation lives in the "
            "tail rather than in the middle of the distribution"
        )

    if recall >= 0.5 and roc >= 0.75:
        return (
            f"**The distributions separate.** {medians}, and at `tau_anom` the detector "
            f"surfaces {recall:.1%} of a test day whose every attack family is one no part "
            f"of this system was trained on."
        )
    if recall >= 0.2 or roc >= 0.65:
        return (
            f"**The distributions separate partially.** {medians}. ROC-AUC is {roc:.4f}, so "
            f"the ranking carries real signal, but at `tau_anom` only {recall:.1%} of the "
            f"test day's attack traffic clears the line. The per-family table below is "
            f"where that average comes apart, and it is the honest reading of this "
            f"checkpoint rather than a pass."
        )
    return (
        f"**The distributions do not separate.** ROC-AUC is {roc:.4f} and recall at "
        f"`tau_anom` is {recall:.1%}. The brief is explicit about what that means: the "
        f"model is not working, and no dashboard will hide it. Do not advance to fusion "
        f"on this artifact."
    )


def _cell(text: str) -> str:
    """Escape a value going into a Markdown table cell.

    A pipe splits a cell even inside backticks, and the Stage 2 input transform
    is written ``log1p(|x|)``. Without this the architecture table renders with
    two phantom columns.
    """
    return str(text).replace("|", "\\|")


def _join(items: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c`` -- a list that reads as a sentence."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _calibration_drift(run: AnomalyRun, settings: Any) -> str:
    """What one day's benign percentile costs on the next day's benign traffic.

    Written from the two measured rates rather than asserted, because this is
    the dataset-internal version of the domain shift Phase 9 has to handle on
    live traffic, and it is cheap evidence for it: the threshold is a statement
    about Thursday, and Friday is a different day on the same network.
    """
    calibrated = run.threshold["fpr"]
    achieved = run.test.get("volume", {}).get("fpr", float("nan"))
    if not calibrated or np.isnan(achieved):
        return ""

    ratio = achieved / calibrated
    direction = "more" if ratio >= 1 else "less"
    return (
        f"`tau_anom` was cut to alert on {calibrated:.2%} of Thursday's benign flows. On "
        f"Friday's benign traffic the same threshold fires on {achieved:.2%} of them — "
        f"{ratio:.1f}x {direction} often, which is "
        f"{run.test.get('volume', {}).get('false_alerts_per_day', 0.0):,.0f} false alerts a "
        f"day against a budget of {settings.max_alerts_per_day:,}. Nothing about the model "
        f"changed between those two numbers; the benign traffic did. This is the "
        f"dataset-internal version of the domain shift Phase 9 has to handle on live "
        f"capture, measured across two days of one lab network rather than across five "
        f"years and a different one — and it is the argument for the shadow-mode burn-in "
        f"and the locally recomputed threshold that phase prescribes, made as evidence "
        f"rather than as a worry."
    )


def _family_commentary(run: AnomalyRun) -> str:
    """What the per-family table says that the average does not.

    Generated, because the interesting reading of this table is always the same
    *shape* of reading -- one family carries the recall and another is missed
    almost entirely -- while which family is which depends on the run.
    """
    caught = {
        name: scores["recall"]
        for name, scores in run.test.get("family_recall", {}).items()
        if name != BENIGN_FAMILY
    }
    if not caught:
        return ""

    best, best_recall = max(caught.items(), key=lambda item: item[1])
    missed = sorted(
        (name for name, recall in caught.items() if recall < 0.05),
        key=lambda name: caught[name],
    )

    lines = [
        f"The average is carried by one family. `{best}` accounts for "
        f"{best_recall:.1%} of its own rows, which is most of the "
        f"{run.test.get('attack_recall', 0.0):.1%} figure above."
    ]

    if missed:
        names = _join([f"`{name}` ({caught[name]:.1%})" for name in missed])
        # Whether Stage 1 also misses them is the question fusion turns on, and
        # the answer is on the model card rather than in anyone's memory.
        stage1 = {
            name: float(scores["recall"])
            for name, scores in run.stage1_family_recall.items()
            if name in missed and isinstance(scores, dict) and "recall" in scores
        }
        both = {name: recall for name, recall in stage1.items() if recall < 0.05}

        lines.append(
            f"The families Stage 2 does **not** close are {names}. That is the number to "
            f"carry into Phase 4 rather than the average."
        )
        if both:
            pairs = _join(
                [
                    f"`{name}` (Stage 1 {recall:.1%}, Stage 2 {caught[name]:.1%})"
                    for name, recall in sorted(both.items(), key=lambda item: item[1])
                ]
            )
            lines.append(
                f"**Stage 1 misses {'them' if len(both) > 1 else 'it'} too**: {pairs}, from the "
                f"champion's own test-day figures on the model card. A family neither stage "
                f"surfaces is a gap in the system rather than a gap in one model, and fusing "
                f"two detectors that look past the same traffic does not produce a third "
                f"that does not. Fusion helps where the two miss *different* rows, so this "
                f"is the row of the leave-one-attack-out table to read first."
            )
        elif stage1:
            pairs = _join(
                [
                    f"`{name}` (Stage 1 {recall:.1%}, Stage 2 {caught[name]:.1%})"
                    for name, recall in sorted(stage1.items(), key=lambda item: item[1])
                ]
            )
            lines.append(
                f"Stage 1 does better on {pairs}, which is the arrangement fusion is for: "
                f"the two stages missing different traffic is what makes a cascade worth "
                f"more than either half."
            )
        lines.append(
            "The mechanism is worth naming, because the explanation table below makes it "
            "look like a contradiction. The score is a *mean* over every feature, so a flow "
            "can have a highly distinctive error signature and still score low: short, "
            "sparse flows reconstruct easily on most columns, and a large error on five of "
            "them is divided by ninety-two. Stage 2 can be responding to the right features "
            "and still rank the row below the threshold, which is a limitation of the "
            "aggregate rather than of the representation."
        )

    return "\n\n".join(lines)


def _family_table(run: AnomalyRun) -> list[str]:
    """Stage 2 per family, with Stage 1's own column beside it where it is known.

    The Stage 1 numbers come off the model card rather than being recomputed, so
    the two columns are the same day scored by the two models that actually
    shipped. The thresholds they are read at are *not* comparable and the prose
    below the table says so; what the pairing is for is seeing which families each
    stage misses.
    """
    stage1 = run.stage1_family_recall
    header = "| Family on the test day | Rows | Flagged by Stage 2 | Stage 2 recall |"
    divider = "| --- | --- | --- | --- |"
    if stage1:
        header += " Stage 1 recall |"
        divider += " --- |"
    rows = [header, divider]

    for name, scores in run.test.get("family_recall", {}).items():
        benign = name == BENIGN_FAMILY
        marker = "Benign — these are false positives" if benign else f"`{name}`"
        # The benign row is a false-positive rate rather than a recall, and
        # Stage 1's is small enough that one decimal place rounds it to zero --
        # which would read as "Stage 1 had no false positives" rather than as
        # "Stage 1 had four hundred times fewer of them".
        places = ".2%" if benign else ".1%"
        row = (
            f"| {marker} | {int(scores['support']):,} | {int(scores['flagged']):,} | "
            f"**{scores['recall']:{places}}** |"
        )
        if stage1:
            theirs = stage1.get(name)
            row += f" {theirs['recall']:{places}} |" if isinstance(theirs, dict) else " — |"
        rows.append(row)
    return rows


def _baseline_table(run: AnomalyRun) -> list[str]:
    if not run.baselines:
        return ["Baselines were skipped for this run."]
    arena = run.arena
    classical = [row for row in run.baselines if row["name"] != "Autoencoder"]
    reference_rows = classical[0]["fit_rows"] if classical else run.fit_rows
    rows = [
        f"Every detector is scored on the same {arena.get('rows', 0):,}-row arena — "
        f"{arena.get('attack_rows', 0):,} attack flows and "
        f"{arena.get('benign_rows', 0):,} benign ones, drawn from the Thursday validation "
        f"day. The validation day rather than the test day, because choosing between "
        f"detectors is a choice, and choices are not made on the test day. Identical rows, "
        f"so the columns compare; a subsample of the benign traffic, so the absolute PR-AUC "
        f"is not the same quantity as the full-split figure above.",
        "",
        "| Detector | Library | Fitted on | PR-AUC | ROC-AUC |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in run.baselines:
        emphasis = "**" if row["name"] == "Autoencoder" else ""
        rows.append(
            f"| {emphasis}{row['name']}{emphasis} | {row['library']} | "
            f"{row['fit_rows']:,} benign rows | "
            f"{emphasis}{row['pr_auc']:.4f}{emphasis} | {row['roc_auc']:.4f} |"
        )
    rows += [
        "",
        f"The classical detectors are fitted on a {reference_rows:,}-row benign reference "
        f"set rather than on all {run.fit_rows:,}. LOF is a k-nearest-neighbour method: "
        f"scoring against a million reference rows does not finish, and an autoencoder that "
        f"needed a handicapped LOF to look good would not be worth shipping. Every one of "
        f"them sees benign rows only, the same discipline the autoencoder is held to.",
    ]

    ranked = sorted(run.baselines, key=lambda row: row["pr_auc"], reverse=True)
    winner = ranked[0]
    ours = next((row for row in run.baselines if row["name"] == "Autoencoder"), None)
    rows.append("")
    if ours is None or winner["name"] == "Autoencoder":
        runner_up = ranked[1] if len(ranked) > 1 else None
        margin = (
            f" — {winner['pr_auc'] - runner_up['pr_auc']:+.4f} PR-AUC over "
            f"`{runner_up['name']}`, the best of the classical detectors"
            if runner_up
            else ""
        )
        rows.append(f"The autoencoder wins on this arena{margin}, so it earns its complexity here.")
    else:
        rows.append(
            f"**`{winner['name']}` beats the autoencoder on this arena** "
            f"({winner['pr_auc']:.4f} against {ours['pr_auc']:.4f} PR-AUC). That is "
            f"reported rather than hidden, and it is not a defeat for the two-stage "
            f"design: the design needs a Stage 2 that catches families nobody named, not "
            f"a Stage 2 that is a neural network. The finding is that a parameter-free "
            f"detector does that job at least as well on this data, and it belongs in the "
            f"write-up either way."
        )
    for row in run.baselines:
        if row.get("note"):
            rows.append("")
            rows.append(f"`{row['name']}`: {row['note']}.")
    return rows


def _explanation_table(run: AnomalyRun) -> list[str]:
    rows = [
        "| Family | The five features it fails hardest to reconstruct |",
        "| --- | --- |",
    ]
    for family, contributors in run.explanations.items():
        names = ", ".join(f"`{item['feature']}` ({item['share']:.0%})" for item in contributors)
        label = "benign (for contrast)" if family == BENIGN_FAMILY else f"`{family}`"
        rows.append(f"| {label} | {names} |")
    return rows


def render_report(run: AnomalyRun, settings: Any) -> str:
    threshold = AnomalyThreshold(**run.threshold)
    volume = run.test.get("volume", {})
    histograms = {
        "benign": run.histograms["test_benign"],
        "attack": run.histograms["test_attack"],
    }

    return "\n".join(
        [
            "# Phase 3 — Stage 2, the anomaly detector",
            "",
            f"Model `{run.version}`: a PyTorch autoencoder, "
            f"`{run.hyperparameters.get('architecture')}`, "
            f"{run.hyperparameters.get('parameters'):,} parameters, fitted on "
            f"{run.fit_rows:,} benign flows and nothing else.",
            "",
            "Stage 1 answers *which named attack is this*. Stage 2 answers *how unlike "
            "normal traffic is this*, and it answers it having never been shown an attack "
            "of any kind. That is the whole reason it can say something about a family "
            "nobody labelled.",
            "",
            "## The training set carries no attacks",
            "",
            f"{run.source_rows:,} benign rows from `data/processed/benign_train.parquet` — "
            "Monday in full plus the benign rows of Tuesday and Wednesday. "
            f"{run.duplicate_rows:,} exact duplicates were dropped, leaving "
            f"{run.fit_rows:,} rows to fit on and {run.early_stopping_rows:,} held back to "
            "early-stop on.",
            "",
            "The attack-free property is asserted in code, twice: Phase 1 raises "
            "`AttackInBenignTrainingSet` when it assembles the split, and this phase "
            "re-checks the label column before the optimiser is constructed. Both are "
            "fatal rather than warnings. An autoencoder that has seen an attack learns to "
            "reconstruct it and stops flagging it, and nothing anywhere raises — the "
            "claim just quietly stops being true.",
            "",
            "Duplicates are dropped for a reason worth naming. Phase 1 removes them "
            "within each capture file and across the supervised splits, but the "
            "benign-only set is assembled from three days after that pass, so a benign "
            "flow appearing identically on Monday and Tuesday survives twice. Left in, it "
            "lands on both sides of the early-stopping split and makes the validation "
            "loss optimistic, which stops training later than it should.",
            "",
            "## Architecture",
            "",
            "```",
            str(run.hyperparameters.get("architecture")),
            "```",
            "",
            "| Element | Choice |",
            "| --- | --- |",
            f"| Input | `{_cell(run.hyperparameters.get('input_transform'))}`, applied to "
            f"the shared feature matrix |",
            "| Activation | ReLU on every hidden layer; the output layer is a bare `Linear` |",
            "| Loss | MSE — the loss and the score are the same quantity |",
            f"| Optimiser | Adam, lr {run.hyperparameters.get('learning_rate')} |",
            f"| Regularisation | Dropout {run.hyperparameters.get('dropout')} in the "
            f"encoder's hidden layers |",
            "| Normalisation | Batch norm after every hidden linear layer |",
            f"| Bottleneck | {run.hyperparameters.get('bottleneck')} units |",
            f"| Batch | {run.hyperparameters.get('batch_rows'):,} rows |",
            f"| Stopping | Early stopping on benign validation loss, patience "
            f"{run.hyperparameters.get('early_stopping_patience')} |",
            f"| Epochs | {run.hyperparameters.get('epochs_run')} run, best at "
            f"{run.hyperparameters.get('best_epoch')} "
            f"(loss {run.hyperparameters.get('best_val_loss', float('nan')):.6f}) |",
            "",
            "Two of those are decisions rather than defaults. The output layer has no "
            "activation because the features arrive signed: squashing the output through "
            "ReLU would make every negative target unreachable and put a floor under the "
            "reconstruction error of every row, benign ones included. And dropout is "
            "absent from the bottleneck itself — zeroing a tenth of sixteen code units is "
            "a much heavier perturbation than a tenth of sixty-four, and the bottleneck is "
            "already the regulariser this network is built around.",
            "",
            "### The input transform, and the pathology that forced it",
            "",
            "The first run of this phase produced a detector that ranked attack traffic "
            "*below* benign traffic: ROC-AUC 0.2337 on the shared validation-day arena, "
            "where the three classical baselines scored 0.71 to 0.86 on the same rows, "
            "and 0.4676 on the test day. That is worth writing down, because the cause "
            "is a property of the shared "
            "preprocessing bundle rather than of the network.",
            "",
            "`RobustScaler` divides each column by its interquartile range, and when a "
            "column's IQR is zero scikit-learn leaves the divisor at 1.0 — the column "
            "passes through essentially unscaled. CICIDS2017 has such columns. Over three "
            "quarters of benign flows report `idle_std` of exactly zero, so its IQR is "
            "zero, while the flows that do idle report values up to 7.6 × 10⁷ "
            "microseconds. Squared, that one column accounted for **93.9%** of the total "
            "magnitude the MSE loss could see, with `active_std` taking another 4.0% and "
            "the top three together 98.7%.",
            "",
            "An MSE objective under those conditions is not a reconstruction objective. "
            "The gradient belongs to one column, eighty-nine features are invisible to it, "
            "and the score that comes out is a proxy for *does this flow have a large idle "
            "gap* — which benign traffic has more of than attack traffic does. Hence the "
            "inversion. The loss was also still falling monotonically at epoch 60, in the "
            "tens of billions, having never triggered early stopping.",
            "",
            "Stage 2 therefore reads the shared matrix through "
            f"`{run.hyperparameters.get('input_transform')}`. `log1p` compresses the "
            "magnitudes without discarding the ordering — a flow ten thousand IQRs out "
            "still scores above one a hundred IQRs out — and the clip bounds what is left "
            "of the tail. Both halves are monotonic in `|x|`, so nothing about *further "
            "from normal is more anomalous* is lost. The bound is chosen on the validation "
            "day over three seeds; the comparison is in "
            "[`reports/input_ablation.md`](input_ablation.md), reproducible with "
            "`make ablation-input`.",
            "",
            "The transform belongs to Stage 2, not to the bundle. Changing the scaler "
            "would change the schema hash and force Stage 1 to be retrained for the "
            "benefit of a model that is not scale-sensitive at all — a tree ensemble does "
            "not care what a column's units are. It is applied in exactly two places, "
            "once on the way into a forward pass and once at the top of the fit, because "
            "it is not idempotent; `BenignData` carries shared-space rows so that nothing "
            "else can apply it twice, and a test asserts the two paths agree.",
            "",
            "Every baseline below sees the same transformed input. Handing them the raw "
            "scaled matrix would flatter the autoencoder for free: LOF is a Euclidean "
            "method and IsolationForest partitions axis by axis, so both are pulled apart "
            "by the same column that broke the network.",
            "",
            f"Stage 2 is fitted against the champion's own preprocessing bundle — "
            f"{run.feature_count} features under schema `{run.schema_hash}`. At serving "
            "time one feature matrix is built per batch and both stages read it, so a "
            "Stage 2 fitted against its own scaling would produce confident nonsense in "
            "production without raising anything. The scaler is a label-free centring and "
            "scaling statistic; what makes Stage 2's claim true is that no *label* and no "
            "attack row reached the fit.",
            "",
            "## The threshold",
            "",
            "```",
            threshold.render(),
            "```",
            "",
            "`tau_anom` is a statement about normal traffic, not a value tuned until the "
            "attacks landed above it. It is read off the benign rows of the Thursday "
            "validation day — benign traffic from a day the network never trained on, and "
            "the same day `tau_sup` was cut from.",
            "",
            f"The budget row is the uncomfortable one and it is reported on purpose. "
            f"Phase 2 derived `tau_sup` from an analyst queue: "
            f"{settings.max_alerts_per_day:,} alerts a day at "
            f"{settings.expected_daily_flow_volume:,} flows, a false-positive rate of "
            f"{settings.target_fpr:.2e}. A 99.5th percentile is a false-positive rate of "
            f"5 × 10⁻³, which is {5e-3 / settings.target_fpr:.0f}x looser — around "
            f"{5e-3 * settings.expected_daily_flow_volume:,.0f} false alerts a day against "
            f"a budget of {settings.max_alerts_per_day:,}. The brief specifies the "
            f"percentile, so the percentile is what ships and what Phase 4 fuses on; the "
            f"budget-equivalent threshold is recorded beside it so the gap is a measured "
            f"quantity rather than a surprise, and the Live Traffic screen's threshold "
            f"slider is where whoever owns the queue moves between them.",
            "",
            "## The checkpoint: benign against attack",
            "",
            "Reconstruction error on the held-out **Friday** test day, "
            f"{run.test.get('benign_rows', 0):,} benign flows against "
            f"{run.test.get('attack_rows', 0):,} attack flows, binned on a log axis "
            "because the error runs over orders of magnitude. Each column is scaled to "
            "its own tallest bin — the two differ tenfold in row count — and the "
            "percentage beside each bar is the share of that column.",
            "",
            "```",
            render_histograms(histograms, threshold.tau),
            "```",
            "",
            _separation_verdict(run),
            "",
            "| Measured on the test day | Value |",
            "| --- | --- |",
            f"| **PR-AUC (headline)** | **{run.test.get('pr_auc', float('nan')):.4f}** |",
            f"| ROC-AUC | {run.test.get('roc_auc', float('nan')):.4f} |",
            f"| Attack recall at `tau_anom` | "
            f"**{run.test.get('attack_recall', float('nan')):.1%}** |",
            f"| False-positive rate | {volume.get('fpr', float('nan')):.2e} |",
            f"| Projected false alerts/day at V = "
            f"{settings.expected_daily_flow_volume:,} | "
            f"{volume.get('false_alerts_per_day', 0.0):,.0f} |",
            f"| Alerts per analyst per hour | {volume.get('alerts_per_analyst_hour', 0.0):,.1f} |",
            f"| Median benign reconstruction error | "
            f"{run.test.get('median_benign_error', float('nan')):.3e} |",
            f"| Median attack reconstruction error | "
            f"{run.test.get('median_attack_error', float('nan')):.3e} |",
            "",
            _calibration_drift(run, settings),
            "",
            "## Per family",
            "",
            "Every attack family on the Friday test day is one Stage 1 has no name for, "
            "and one the autoencoder has never seen an example of. This table is what "
            "Stage 2 does with them on its own, before any fusion. Stage 1's column is "
            "lifted from the champion's own test-day figures on the model card — the same "
            "day, the two models that shipped. The two are read at thresholds cut by "
            "different rules, so the recall figures are *not* a like-for-like comparison of "
            "the models; what the pairing shows is which families each stage misses.",
            "",
            *_family_table(run),
            "",
            _family_commentary(run),
            "",
            "## Do the baselines beat it",
            "",
            *_baseline_table(run),
            "",
            "## What it failed to reconstruct",
            "",
            "The Stage 2 explanation is free: `(x - x_hat) ** 2` is already computed as "
            "part of the score, and the features the network failed hardest to rebuild "
            "are precisely why the row looks unlike normal traffic. No SHAP, no "
            "perturbation sampling, and more faithful to the model than either. Averaged "
            "over a sample of each family on the test day:",
            "",
            *_explanation_table(run),
            "",
            "## Appendix: the training curve",
            "",
            "| Epoch | Train loss | Benign validation loss |",
            "| --- | --- | --- |",
            *[
                f"| {int(row['epoch'])} | {row['train_loss']:.6f} | {row['val_loss']:.6f} |"
                for row in run.history
            ],
            "",
            "Early stopping watches the second column and the weights of the best epoch "
            "are restored before the artifact is written. Stopping where patience ran out "
            "would ship a model several epochs past its own best loss.",
            "",
            "## What this hands to Phase 4",
            "",
            "`autoencoder.pt` as a bare state dict, `tau_anom` and the benign error "
            "histogram on the model card, and a Stage 2 that shares Stage 1's feature "
            "contract exactly. Fusion needs nothing else: one matrix, Stage 1 first, "
            "Stage 2 on whatever Stage 1 could not confidently name, and "
            "`UNCLASSIFIED_ANOMALY` for what clears `tau_anom` without a family.",
            "",
            "The per-family column above is the Stage 2 column of the leave-one-attack-out "
            "table, measured here without the hold-out loop. Phase 4 runs the loop, which "
            "removes each family from Stage 1's training set in turn and asks the same "
            "question of the pair rather than of Stage 2 alone.",
            "",
        ]
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_arena(
    validation: SplitScores,
    frame: pd.DataFrame,
    bundle: dict[str, Any],
    benign_rows: int = BASELINE_ARENA_BENIGN_ROWS,
    seed: int = SEED,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """The rows every detector is compared on: validation attacks, benign sampled.

    The validation day, because choosing between detectors is a choice and
    choices are not made on the test day. Every attack row it carries, plus a
    benign sample, because LOF over four hundred thousand reference-scored rows
    does not finish.
    """
    rng = np.random.default_rng(seed)
    is_attack = validation.is_attack
    attack_index = np.flatnonzero(is_attack)
    benign_index = np.flatnonzero(~is_attack)
    if benign_index.size > benign_rows:
        benign_index = rng.choice(benign_index, size=benign_rows, replace=False)

    selected = np.sort(np.concatenate([attack_index, benign_index]))
    matrix = build_feature_matrix(frame.iloc[selected], bundle).to_numpy(dtype="float32")
    truth = is_attack[selected]
    return (
        matrix,
        truth,
        validation.errors[selected],
        {
            "split": "val",
            "rows": int(len(selected)),
            "attack_rows": int(truth.sum()),
            "benign_rows": int((~truth).sum()),
            "benign_sampled_from": int((~is_attack).sum()),
        },
    )


def input_ablation(
    benign_train: pd.DataFrame,
    val_frame: pd.DataFrame,
    artifacts_dir: Path,
    reports_dir: Path,
    bounds: tuple[float | None, ...] = INPUT_ABLATION_BOUNDS,
    seeds: tuple[int, ...] = INPUT_ABLATION_SEEDS,
    rows: int = INPUT_ABLATION_ROWS,
    epochs: int = INPUT_ABLATION_EPOCHS,
) -> list[dict[str, Any]]:
    """Choose ``INPUT_CLIP`` on the validation day, over several seeds.

    The test day is absent from this function on purpose. An
    input-representation decision made on the test day is a decision that has
    already spent the test day, which is the same rule the Phase 2 port ablation
    is run under.

    Several seeds because one is not enough to tell these candidates apart: the
    spread between the middle three bounds is wider than the gaps between their
    means, and a single seed picks a different winner from the average of three.
    """
    bundle, _ = load_artifacts(artifacts_dir)
    assert_attack_free(benign_train)

    sample = benign_train
    if len(sample) > rows:
        sample = sample.sample(n=rows, random_state=seeds[0])
    data = prepare_benign(sample, bundle, seed=seeds[0])

    families = map_labels(val_frame["label"]).astype(str).reset_index(drop=True)
    is_attack = (families != BENIGN_FAMILY).to_numpy()
    x_val = build_feature_matrix(val_frame, bundle).to_numpy(dtype="float32")

    results: list[dict[str, Any]] = []
    for bound in bounds:
        clip = NO_CLIP if bound is None else bound
        per_seed: list[dict[str, float]] = []
        for seed in seeds:
            model, hyperparameters, _ = fit_autoencoder(
                data, max_epochs=epochs, patience=epochs, seed=seed, clip=clip
            )
            curves = detection_curves(is_attack, reconstruction_error(model, x_val, clip=clip))
            per_seed.append(
                {
                    "seed": seed,
                    "val_loss": hyperparameters["best_val_loss"],
                    "roc_auc": curves.roc_auc,
                    "pr_auc": curves.pr_auc,
                }
            )
            logger.info(
                "clip %-5s seed %-3d  benign val loss %.5f  validation ROC-AUC %.4f",
                "none" if bound is None else f"+/-{bound:g}",
                seed,
                hyperparameters["best_val_loss"],
                curves.roc_auc,
            )

        roc = np.array([row["roc_auc"] for row in per_seed])
        pr = np.array([row["pr_auc"] for row in per_seed])
        results.append(
            {
                "clip": bound,
                "val_loss": float(np.mean([row["val_loss"] for row in per_seed])),
                "roc_auc_mean": float(roc.mean()),
                "roc_auc_sd": float(roc.std()),
                "pr_auc_mean": float(pr.mean()),
                "pr_auc_sd": float(pr.std()),
                "seeds": per_seed,
            }
        )

    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "input_ablation.md").write_text(
        render_input_ablation(results, data, epochs, seeds), encoding="utf-8"
    )
    return results


def render_input_ablation(
    results: list[dict[str, Any]],
    data: BenignData,
    epochs: int,
    seeds: tuple[int, ...],
) -> str:
    """The write-up for the bound, including the seed spread that justifies three."""
    best = max(results, key=lambda row: row["roc_auc_mean"])
    label = "none" if best["clip"] is None else f"±{best['clip']:g}"
    single_seed_best = max(results, key=lambda row: row["seeds"][0]["roc_auc"])
    disagrees = single_seed_best["clip"] != best["clip"]

    # Is the winner a result or a draw? The strong form of the question a seed
    # sweep exists to answer: does its worst run still beat every rival's
    # average?
    worst_of_best = min(entry["roc_auc"] for entry in best["seeds"])
    clears_every_rival = all(
        worst_of_best > row["roc_auc_mean"] for row in results if row is not best
    )

    lines = [
        "# Stage 2 input-transform ablation",
        "",
        "Stage 2 reads the shared feature matrix through "
        "`sign(x) * log1p(|x|)`, clipped. This is where the clip bound comes from.",
        "",
        "## Why there is a transform at all",
        "",
        "`RobustScaler` divides each column by its interquartile range, and when a "
        "column's IQR is zero scikit-learn leaves the divisor at 1.0 — the column passes "
        "through essentially unscaled. CICIDS2017 has such columns. Over three quarters of "
        "benign flows report `idle_std` of exactly zero, so its IQR is zero, while the "
        "flows that do idle report values up to 7.6 × 10⁷ microseconds.",
        "",
        "Squared, that one column accounted for **93.9%** of the total magnitude an MSE "
        "loss could see, with `active_std` taking another 4.0% and the top three together "
        "98.7%. Under those conditions MSE is not a reconstruction objective: the gradient "
        "belongs to one column, eighty-nine features are invisible to it, and the score "
        "that comes out is a proxy for *does this flow have a large idle gap* — which "
        "benign traffic has more of than attack traffic does. The first run of Phase 3 "
        "measured **ROC-AUC 0.2337** on the shared validation-day arena — worse than a "
        "coin — where the three classical baselines scored 0.71 to 0.86 on the same "
        "rows, and 0.4676 on the test day. Those are historical figures: the current "
        "code has no path that reproduces them, which is why the table below starts "
        "from `log1p` rather than from the raw matrix.",
        "",
        "`log1p` compresses the magnitudes without discarding the ordering — a flow ten "
        "thousand IQRs out still scores above one a hundred IQRs out — and the clip bounds "
        "what is left of the tail. Both halves are monotonic in `|x|`, so nothing about "
        "*further from normal is more anomalous* is lost.",
        "",
        "The transform belongs to Stage 2 rather than to the preprocessing bundle. "
        "Changing the scaler would change the schema hash and force Stage 1 to be "
        "retrained for the benefit of a model that is not scale-sensitive at all — a tree "
        "ensemble does not care what a column's units are.",
        "",
        "## The bound",
        "",
        f"Each row is {len(seeds)} fits of the shipped architecture on the same "
        f"{len(data.x_fit):,} benign rows for {epochs} epochs, scored on the Thursday "
        f"validation day. Seeds {', '.join(str(seed) for seed in seeds)}.",
        "",
        "| Clip (log units) | Benign val loss | Validation ROC-AUC | Validation PR-AUC |",
        "| --- | --- | --- | --- |",
    ]

    for row in results:
        name = "none" if row["clip"] is None else f"±{row['clip']:g}"
        emphasis = "**" if row is best else ""
        lines.append(
            f"| {emphasis}{name}{emphasis} | {row['val_loss']:.5f} | "
            f"{emphasis}{row['roc_auc_mean']:.4f}{emphasis} ± {row['roc_auc_sd']:.4f} | "
            f"{row['pr_auc_mean']:.4f} ± {row['pr_auc_sd']:.4f} |"
        )

    lines += [
        "",
        f"**`INPUT_CLIP = {best['clip'] if best['clip'] is not None else 'none'}`.** Both "
        f"metrics put the candidates in the same order, so the choice does not rest on "
        f"which one is quoted.",
        "",
    ]

    spread = (
        "Several seeds rather than one, because the standard deviations in that table are "
        "comparable to the gaps between the means: one run cannot separate these "
        "candidates."
    )
    if disagrees:
        single = "none" if single_seed_best["clip"] is None else f"±{single_seed_best['clip']:g}"
        spread += (
            f" On the first seed alone the best bound is **{single}**, not {label}, which is "
            f"that fact made concrete."
        )
    if clears_every_rival:
        spread += (
            f" What makes {label} a result rather than a draw is the strong form: its "
            f"*worst* of the {len(seeds)} runs ({worst_of_best:.4f}) still beats every other "
            f"candidate's *mean*."
        )
    else:
        spread += (
            f" {label} wins on the mean, but its worst run ({worst_of_best:.4f}) does not "
            f"clear every rival's mean, so the margin is narrower than the table suggests "
            f"and the choice is the best available rather than a clear separation."
        )
    lines += [spread, ""]

    lines += [
        "| Clip | " + " | ".join(f"seed {seed}" for seed in seeds) + " |",
        "| --- " * (len(seeds) + 1) + "|",
    ]
    for row in results:
        name = "none" if row["clip"] is None else f"±{row['clip']:g}"
        lines.append(
            f"| {name} | " + " | ".join(f"{entry['roc_auc']:.4f}" for entry in row["seeds"]) + " |"
        )

    lines += [
        "",
        "Validation ROC-AUC per seed. PR-AUC on this day runs in the hundredths for every "
        "candidate, because Thursday is 99.5% benign and its two attack families — web "
        "attacks and infiltration — are the hardest in the capture. That is why the "
        "selection is read off ROC-AUC here: every candidate is scored on identical rows, "
        "so prevalence-invariance isolates the ranking quality from the class balance. "
        "PR-AUC remains the headline where it belongs, on the test day, in "
        "`reports/phase3_anomaly.md`.",
        "",
    ]
    return "\n".join(lines)


def train(
    benign_train: pd.DataFrame,
    val_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
    artifacts_dir: Path,
    percentile: float = ANOMALY_PERCENTILE,
    seed: int = SEED,
    max_epochs: int = MAX_EPOCHS,
    patience: int = EARLY_STOPPING_PATIENCE,
    batch_rows: int = BATCH_ROWS,
    baselines: bool = True,
    settings: Any = None,
) -> AnomalyRun:
    """Fit Stage 2 end to end and write its artifacts."""
    if settings is None:
        from app.config import settings as default_settings

        settings = default_settings

    bundle, card = load_artifacts(artifacts_dir)
    rows = assert_attack_free(benign_train)
    logger.info("Stage 2 training set verified attack-free: %d benign rows", rows)

    data = prepare_benign(benign_train, bundle, seed=seed)
    logger.info(
        "fitting on %d benign rows x %d features (champion schema %s)",
        len(data.x_fit),
        data.input_dim,
        card.get("schema_hash"),
    )

    model, hyperparameters, history = fit_autoencoder(
        data, max_epochs=max_epochs, patience=patience, batch_rows=batch_rows, seed=seed
    )

    validation = score_split(model, val_frame, bundle, "val")
    test = score_split(model, test_frame, bundle, "test")

    baseline_results: list[BaselineResult] = []
    arena_record: dict[str, Any] = {}
    if baselines:
        arena, truth, our_errors, arena_record = build_arena(
            validation, val_frame, bundle, seed=seed
        )
        logger.info(
            "baseline arena: %d rows, %d attacks", arena_record["rows"], arena_record["attack_rows"]
        )
        ours = detection_curves(truth, our_errors)
        baseline_results = [
            BaselineResult(
                name="Autoencoder",
                library="torch",
                pr_auc=ours.pr_auc,
                roc_auc=ours.roc_auc,
                fit_rows=int(len(data.x_fit)),
                note="scored on the arena from the same weights measured on the full splits",
            ),
            *run_baselines(data.x_fit, arena, truth, seed=seed),
        ]

    run = measure(
        model,
        data,
        validation,
        test,
        bundle,
        test_frame,
        settings,
        hyperparameters,
        history,
        card=card,
        percentile=percentile,
        baselines=baseline_results,
        arena=arena_record,
        seed=seed,
    )

    write_artifacts(model, run, artifacts_dir, settings)
    update_model_card(run, artifacts_dir)
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="train_autoencoder.py",
        description="Train Stage 2, the benign-only anomaly detector.",
    )
    parser.add_argument("--percentile", type=float, default=ANOMALY_PERCENTILE)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--max-epochs", type=int, default=MAX_EPOCHS)
    parser.add_argument("--patience", type=int, default=EARLY_STOPPING_PATIENCE)
    parser.add_argument("--batch-rows", type=int, default=BATCH_ROWS)
    parser.add_argument(
        "--no-baselines",
        action="store_true",
        help="Skip IsolationForest, LOF and ECOD (they are the slow part of the run)",
    )
    parser.add_argument(
        "--input-ablation",
        action="store_true",
        help="Choose the input clip bound on the validation day and write "
        "reports/input_ablation.md instead of training",
    )
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--artifacts-dir", type=Path, default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    from app.config import settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    processed_dir = args.processed_dir or settings.data_path / "processed"
    artifacts_dir = args.artifacts_dir or settings.artifacts_path
    reports_dir = args.reports_dir or settings.reports_path

    if args.input_ablation:
        input_ablation(
            load_split(processed_dir, "benign_train"),
            load_split(processed_dir, "val"),
            artifacts_dir=artifacts_dir,
            reports_dir=reports_dir,
        )
        echo((reports_dir / "input_ablation.md").read_text(encoding="utf-8"))
        return 0

    run = train(
        load_split(processed_dir, "benign_train"),
        load_split(processed_dir, "val"),
        load_split(processed_dir, "test"),
        artifacts_dir=artifacts_dir,
        percentile=args.percentile,
        seed=args.seed,
        max_epochs=args.max_epochs,
        patience=args.patience,
        batch_rows=args.batch_rows,
        baselines=not args.no_baselines,
        settings=settings,
    )

    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / REPORT_FILENAME
    report_path.write_text(render_report(run, settings), encoding="utf-8")

    echo(run.render())
    echo("")
    echo(
        render_histograms(
            {"benign": run.histograms["test_benign"], "attack": run.histograms["test_attack"]},
            run.threshold["tau"],
        )
    )
    echo(f"\nwritten to {report_path}")
    echo(f"           {artifacts_dir / AUTOENCODER_ARTIFACT}")
    echo(f"           {artifacts_dir / METRICS_ARTIFACT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
