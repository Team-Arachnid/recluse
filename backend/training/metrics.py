"""Phase 2 -- the quantities Stage 1 is judged on, and the threshold arithmetic.

Shared by ``train_supervised.py`` (which picks ``tau_sup`` and the depth on the
validation day) and ``evaluate.py`` (which reports the test day), so the two
cannot compute the same number two different ways.

Accuracy is computed here and appears in one table cell, never as a headline.
On traffic that is 99% benign, a model that always answers benign scores 99%;
the number describes the class balance rather than the model. PR-AUC is the
headline, and it is reported next to ROC-AUC precisely so the gap between them
is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from training.labels import BENIGN_FAMILY

# The curves are persisted for the dashboard to draw. A quarter of a million
# points would be a 20 MB JSON payload nobody can render, and a curve is a
# shape: 512 points is more than a chart can resolve.
MAX_CURVE_POINTS = 512


def attack_columns(classes: list[str]) -> list[int]:
    """Indices of the attack classes in a ``predict_proba`` matrix."""
    return [index for index, name in enumerate(classes) if name != BENIGN_FAMILY]


def attack_confidence(proba: np.ndarray, classes: list[str]) -> np.ndarray:
    """The quantity ``tau_sup`` cuts: the largest single attack-class probability.

    This is the brief's fusion rule (``p[attack_classes].max()``) rather than
    ``1 - P(benign)``, and the difference is deliberate. A flow split evenly
    across two attack families scores lower here than under ``1 - P(benign)``,
    so it falls through to Stage 2 -- which is the right destination for a row
    Stage 1 cannot confidently *name*. ``attack_probability`` computes the
    other quantity, and the evaluation reports the PR-AUC of both so the choice
    is auditable rather than assumed.
    """
    columns = attack_columns(classes)
    if not columns:
        return np.zeros(len(proba), dtype="float64")
    return proba[:, columns].max(axis=1)


def attack_probability(proba: np.ndarray, classes: list[str]) -> np.ndarray:
    """``1 - P(benign)``: total evidence that the row is an attack of some kind."""
    if BENIGN_FAMILY not in classes:
        return np.ones(len(proba), dtype="float64")
    return 1.0 - proba[:, classes.index(BENIGN_FAMILY)]


def predicted_attack_family(proba: np.ndarray, classes: list[str]) -> np.ndarray:
    """The family an alert would carry: the highest-scoring attack class."""
    columns = attack_columns(classes)
    if not columns:
        return np.array([None] * len(proba), dtype=object)
    names = np.array([classes[index] for index in columns], dtype=object)
    return names[proba[:, columns].argmax(axis=1)]


def false_positive_rate(scores: np.ndarray, is_benign: np.ndarray, tau: float) -> float:
    """Benign rows the threshold would alert on, as a fraction of all benign rows."""
    benign = np.asarray(scores, dtype="float64")[np.asarray(is_benign, dtype=bool)]
    if benign.size == 0:
        return 0.0
    return float((benign >= tau).mean())


@dataclass
class ThresholdChoice:
    """``tau_sup`` and the evidence for it."""

    tau: float
    fpr: float
    target_fpr: float
    benign_rows: int
    false_alerts: int
    above_every_benign_score: bool

    def render(self) -> str:
        lines = [
            f"tau_sup          {self.tau:.6f}",
            f"target FPR       {self.target_fpr:.2e}",
            f"achieved FPR     {self.fpr:.2e}  "
            f"({self.false_alerts:,} of {self.benign_rows:,} benign rows)",
        ]
        if self.above_every_benign_score:
            lines.append(
                "note             the budget is only met above every benign score "
                "on this split; the threshold silences anything scoring like benign"
            )
        return "\n".join(lines)


def select_threshold(
    scores: np.ndarray, is_benign: np.ndarray, target_fpr: float
) -> ThresholdChoice:
    """The smallest threshold whose false-positive rate fits the budget.

    Smallest, not safest: every step above the budget-satisfying threshold
    throws away recall the analysts had capacity to absorb. ``argmax`` and a
    default 0.5 are both absent by design -- the threshold is an operating
    decision derived from analyst capacity, and that derivation is in
    ``Settings.target_fpr``.

    Candidates are the distinct benign scores, so the FPR at the chosen
    threshold is a measured value rather than an interpolation, plus one
    sentinel above all of them for the case where no observed score fits.
    """
    scores = np.asarray(scores, dtype="float64")
    benign = np.sort(scores[np.asarray(is_benign, dtype=bool)])
    if benign.size == 0:
        raise ValueError("cannot derive a false-positive budget without benign rows")

    observed = np.unique(benign)
    candidates = np.append(observed, np.nextafter(observed[-1], np.inf))
    # count(benign >= c) falls as c rises, so the first candidate that fits the
    # budget is also the smallest one.
    above = benign.size - np.searchsorted(benign, candidates, side="left")
    fprs = above / benign.size

    index = int(np.flatnonzero(fprs <= target_fpr)[0])
    return ThresholdChoice(
        tau=float(candidates[index]),
        fpr=float(fprs[index]),
        target_fpr=float(target_fpr),
        benign_rows=int(benign.size),
        false_alerts=int(above[index]),
        above_every_benign_score=index == len(observed),
    )


def threshold_sweep(
    scores: np.ndarray,
    is_benign: np.ndarray,
    is_attack: np.ndarray,
    candidates: list[float],
    daily_flow_volume: int,
) -> list[dict[str, float]]:
    """The table that makes the threshold choice mechanical rather than asserted.

    One row per candidate: its FPR, the attack recall it buys, and the alert
    volume it implies at the configured daily flow count.
    """
    scores = np.asarray(scores, dtype="float64")
    is_benign = np.asarray(is_benign, dtype=bool)
    is_attack = np.asarray(is_attack, dtype=bool)

    rows: list[dict[str, float]] = []
    for tau in candidates:
        flagged = scores >= tau
        fpr = float(flagged[is_benign].mean()) if is_benign.any() else 0.0
        recall = float(flagged[is_attack].mean()) if is_attack.any() else 0.0
        rows.append(
            {
                "tau": float(tau),
                "fpr": fpr,
                "attack_recall": recall,
                "alerts_per_day": fpr * daily_flow_volume,
            }
        )
    return rows


@dataclass
class AlertVolume:
    """What a threshold costs the queue, in the unit a SOC budgets in.

    The projection is driven by the false-positive rate, not by the alert rate
    measured on the split. A CICIDS2017 attack day is over a third attack
    traffic; multiplying that density by a million flows would project a queue
    no real network produces and would make the budget comparison meaningless.
    ``alert_rate`` is still reported, as a measurement of this split rather than
    a projection onto a day.
    """

    tau: float
    alert_rate: float
    fpr: float
    false_alerts_per_day: float
    alerts_per_analyst_hour: float
    budget_per_day: int
    attack_share_of_split: float

    @property
    def within_budget(self) -> bool:
        return self.false_alerts_per_day <= self.budget_per_day


def alert_volume(
    scores: np.ndarray,
    is_benign: np.ndarray,
    tau: float,
    daily_flow_volume: int,
    analyst_capacity_per_hour: int,
    analyst_shift_hours: int,
) -> AlertVolume:
    """Project a threshold onto a day of traffic.

    ``fpr`` is measured on benign rows alone and is the quantity the budget is
    defined against; true positives sit on top of it and their number depends
    on how much attack traffic the network actually carries, which is not
    something a lab capture can tell you.
    """
    scores = np.asarray(scores, dtype="float64")
    is_benign = np.asarray(is_benign, dtype=bool)
    flagged = scores >= tau
    fpr = false_positive_rate(scores, is_benign, tau)
    false_alerts_per_day = fpr * daily_flow_volume
    return AlertVolume(
        tau=float(tau),
        alert_rate=float(flagged.mean()) if flagged.size else 0.0,
        fpr=fpr,
        false_alerts_per_day=false_alerts_per_day,
        alerts_per_analyst_hour=false_alerts_per_day / analyst_shift_hours,
        budget_per_day=analyst_capacity_per_hour * analyst_shift_hours,
        attack_share_of_split=float((~is_benign).mean()) if is_benign.size else 0.0,
    )


def _downsample(*columns: np.ndarray, limit: int = MAX_CURVE_POINTS) -> list[list[float]]:
    length = len(columns[0])
    if length <= limit:
        keep = np.arange(length)
    else:
        keep = np.unique(np.linspace(0, length - 1, limit).astype(int))
    return [[float(column[index]) for column in columns] for index in keep]


@dataclass
class DetectionCurves:
    """PR and ROC for the binary question "is this an attack at all?".

    Both are kept because the dashboard draws them side by side, and the gap
    between them is the argument for why ROC-AUC flatters a detector on traffic
    that is 99% benign: FPR's denominator is the enormous benign count, so
    thousands of false positives barely move the ROC curve while every one of
    them costs precision immediately.
    """

    pr_auc: float
    roc_auc: float
    pr_curve: list[list[float]] = field(default_factory=list)
    roc_curve: list[list[float]] = field(default_factory=list)
    positives: int = 0
    negatives: int = 0


def detection_curves(is_attack: np.ndarray, scores: np.ndarray) -> DetectionCurves:
    """PR-AUC, ROC-AUC and both curves, downsampled for transport."""
    from sklearn.metrics import (
        average_precision_score,
        precision_recall_curve,
        roc_auc_score,
        roc_curve,
    )

    truth = np.asarray(is_attack, dtype=bool)
    scores = np.asarray(scores, dtype="float64")
    positives = int(truth.sum())
    negatives = int((~truth).sum())

    if positives == 0 or negatives == 0:
        return DetectionCurves(
            pr_auc=float("nan"),
            roc_auc=float("nan"),
            positives=positives,
            negatives=negatives,
        )

    precision, recall, _ = precision_recall_curve(truth, scores)
    fpr, tpr, _ = roc_curve(truth, scores)

    return DetectionCurves(
        pr_auc=float(average_precision_score(truth, scores)),
        roc_auc=float(roc_auc_score(truth, scores)),
        pr_curve=_downsample(recall, precision),
        roc_curve=_downsample(fpr, tpr),
        positives=positives,
        negatives=negatives,
    )


def per_class_report(
    truth: pd.Series, predicted: np.ndarray, classes: list[str]
) -> dict[str, dict[str, float]]:
    """Per-class precision, recall, F1 and support.

    ``classes`` spans every family present in the truth as well as every family
    the model can emit, so a family the model has no column for shows up as a
    row of zeros with its real support rather than vanishing from the table.
    That row is the point: on a temporal split the test day's families are
    mostly ones Stage 1 was never shown.
    """
    from sklearn.metrics import classification_report

    report = classification_report(
        truth.astype(str).to_numpy(),
        np.asarray(predicted, dtype=object).astype(str),
        labels=classes,
        output_dict=True,
        zero_division=0,
    )
    return {
        name: {
            "precision": float(values["precision"]),
            "recall": float(values["recall"]),
            "f1": float(values["f1-score"]),
            "support": int(values["support"]),
        }
        for name, values in report.items()
        if name in classes
    }


def confusion(truth: pd.Series, predicted: np.ndarray, classes: list[str]) -> list[list[int]]:
    """Rows are true families, columns predicted ones, both in ``classes`` order.

    Aggregate metrics say a class is weak; only the matrix says what it is being
    mistaken for, which is what drives the next fix.
    """
    from sklearn.metrics import confusion_matrix

    matrix = confusion_matrix(
        truth.astype(str).to_numpy(),
        np.asarray(predicted, dtype=object).astype(str),
        labels=classes,
    )
    return [[int(cell) for cell in row] for row in matrix]


def recall_by_family(
    truth: pd.Series, flagged: np.ndarray, families: list[str]
) -> dict[str, dict[str, float]]:
    """How much of each family clears the threshold, regardless of the name given.

    This is the number the fusion pipeline and the Phase 4 hold-out table are
    built on: a DDoS flow flagged as ``dos`` is caught, even though the
    per-class report scores it as a misclassification.
    """
    truth = truth.astype(str)
    flagged = np.asarray(flagged, dtype=bool)
    result: dict[str, dict[str, float]] = {}
    for family in families:
        mask = (truth == family).to_numpy()
        support = int(mask.sum())
        if support == 0:
            continue
        result[family] = {
            "support": support,
            "flagged": int(flagged[mask].sum()),
            "recall": float(flagged[mask].mean()),
        }
    return result


def accuracy(truth: pd.Series, predicted: np.ndarray) -> float:
    """Reported in one table cell for comparability, never as a headline."""
    return float((truth.astype(str).to_numpy() == np.asarray(predicted).astype(str)).mean())
