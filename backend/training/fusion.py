"""Phase 4 -- the rule that sequences the two stages.

    def classify(x):
        p = supervised.predict_proba(x)
        attack_conf = p[attack_classes].max()

        if attack_conf >= tau_sup:
            return Alert(kind="KNOWN", family=argmax_class(p), conf=attack_conf)

        anom = autoencoder.score(x)
        if anom >= tau_anom:
            return Alert(kind="UNCLASSIFIED_ANOMALY", family=None, score=anom)

        return None

That is the whole of it, and it lives here rather than inside the API for the
same reason ``metrics.py`` lives next to the trainers that share it: the
leave-one-attack-out table is a measurement *of this cascade*, so if the
evaluation and the serving path each had their own copy, the headline number
would describe something the dashboard does not do. ``app/inference.py`` calls
``fuse`` and ``training/loao.py`` calls ``fuse``; there is no second
implementation to drift.

Vectorised over a batch, never per row. ``predict()`` once per flow in the
replay loop is roughly fifty times slower and makes the demo stutter.

Two properties are worth stating before the code, because both are silent when
broken:

* **The comparisons are inclusive.** ``select_threshold`` measures its
  false-positive rate as ``benign >= tau`` and ``select_anomaly_threshold``
  reads a percentile the same way. An exclusive comparison here would make the
  operating point that ships differ from the one that was measured by exactly
  the rows sitting on the threshold.
* **Stage 2 is consulted only for what Stage 1 could not name.** The cascade is
  the semantics, not a saving. A row Stage 1 named is a KNOWN alert whatever
  its reconstruction error, so that error is reported as ``NaN`` -- *not
  asked* -- rather than as a number somebody could re-rank the queue by.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from training.metrics import attack_columns, attack_confidence, predicted_attack_family

# Mirrors app.models.ALERT_KINDS and app.models.DETECTION_STAGES, which are the
# wire and storage contract. Duplicated rather than imported so the training
# package stays free of SQLAlchemy -- the same arrangement ``training.labels``
# has with ``ALERT_FAMILIES``, and a test asserts the two cannot drift apart.
KIND_KNOWN = "KNOWN"
KIND_UNCLASSIFIED_ANOMALY = "UNCLASSIFIED_ANOMALY"

STAGE1 = "stage1_supervised"
STAGE2 = "stage2_anomaly"


@dataclass(frozen=True)
class FusionDecisions:
    """The cascade's verdict on a batch, one entry per row in input order.

    Column semantics, which the arrays carry rather than document:

    * ``kind`` is ``None`` for a row that produced no alert. A detector that
      returned only its alerts would be unable to tell the dashboard how many
      flows were scored to find them, and the false-positive rate needs the
      denominator.
    * ``family`` is set only for KNOWN alerts, mirroring the
      ``family_matches_kind`` constraint on the alerts table.
    * ``confidence`` is Stage 1's attack confidence for every row it scored,
      and ``NaN`` when Stage 1 is not loaded at all.
    * ``anomaly_score`` is ``NaN`` wherever Stage 2 was not consulted.
    """

    kind: np.ndarray
    family: np.ndarray
    stage: np.ndarray
    confidence: np.ndarray
    anomaly_score: np.ndarray
    tau_sup: float | None = None
    tau_anom: float | None = None

    def __len__(self) -> int:
        return int(len(self.kind))

    @property
    def known(self) -> np.ndarray:
        return self.kind == KIND_KNOWN

    @property
    def anomalous(self) -> np.ndarray:
        return self.kind == KIND_UNCLASSIFIED_ANOMALY

    @property
    def alerted(self) -> np.ndarray:
        return self.known | self.anomalous

    def counts(self) -> dict[str, int]:
        """The cascade as five integers, which is how the report reads it."""
        known = int(self.known.sum())
        anomalous = int(self.anomalous.sum())
        return {
            "rows": len(self),
            "known": known,
            "unclassified_anomaly": anomalous,
            "alerts": known + anomalous,
            "clear": len(self) - known - anomalous,
        }

    def as_records(self) -> list[dict[str, Any]]:
        """One JSON-safe dict per scored flow, in the order they arrived.

        ``NaN`` becomes ``None``: it is not valid JSON, and a serialiser that
        emits it produces a response no browser will parse.
        """
        return [
            {
                "kind": self.kind[index],
                "family": self.family[index],
                "detection_stage": self.stage[index],
                "confidence": _finite(self.confidence[index]),
                "anomaly_score": _finite(self.anomaly_score[index]),
            }
            for index in range(len(self))
        ]


def _finite(value: float) -> float | None:
    number = float(value)
    return None if np.isnan(number) else number


def fuse(
    proba: np.ndarray | None,
    classes: Sequence[str] | None,
    tau_sup: float | None,
    anomaly_score: np.ndarray | None = None,
    tau_anom: float | None = None,
) -> FusionDecisions:
    """Sequence Stage 1 and Stage 2 over one batch.

    ``anomaly_score`` is the reconstruction error of *every* row, not of the
    subset Stage 1 passed through. Scoring the whole batch and masking is
    cheaper than it looks -- Stage 1 names a fraction of a percent of ordinary
    traffic -- and it keeps this function pure numpy, with no callback into a
    torch module and no second code path to test.

    Either stage may be absent, because the phases land in order and a bundle
    legitimately carries only one of them. With Stage 1 absent every row
    reaches Stage 2; with Stage 2 absent the detector is Stage 1 alone. With
    neither, there is nothing to decide and that is an error rather than a
    batch of nulls.
    """
    stage1_live = proba is not None and tau_sup is not None
    stage2_live = anomaly_score is not None and tau_anom is not None
    if not stage1_live and not stage2_live:
        raise ValueError(
            "fuse() was given no stage to run: Stage 1 needs `proba` and `tau_sup`, "
            "Stage 2 needs `anomaly_score` and `tau_anom`, and at least one pair "
            "has to be complete."
        )

    if stage1_live:
        matrix = np.asarray(proba, dtype="float64")
        names = [str(name) for name in (classes or ())]
        if matrix.ndim != 2 or matrix.shape[1] != len(names):
            raise ValueError(
                f"proba has {matrix.shape[1] if matrix.ndim == 2 else '?'} columns "
                f"against {len(names)} class name(s). The column order of "
                "predict_proba is the class order, so a mismatch means a column is "
                "being read as the wrong family."
            )
        rows = int(len(matrix))
        confidence = attack_confidence(matrix, names)
        best_family = predicted_attack_family(matrix, names)
        # A vocabulary with no attack column cannot name an attack, and
        # `attack_confidence` scores every row zero in that case -- which a
        # threshold of zero would turn into a KNOWN alert with a null family,
        # the one combination the alerts table rejects.
        known = (
            (confidence >= float(tau_sup)) if attack_columns(names) else np.zeros(rows, dtype=bool)
        )
    else:
        rows = int(len(np.asarray(anomaly_score)))
        confidence = np.full(rows, np.nan)
        best_family = np.array([None] * rows, dtype=object)
        known = np.zeros(rows, dtype=bool)

    reported = np.full(rows, np.nan)
    anomalous = np.zeros(rows, dtype=bool)
    if stage2_live:
        measured = np.asarray(anomaly_score, dtype="float64").ravel()
        if len(measured) != rows:
            raise ValueError(
                f"{len(measured)} anomaly score(s) against {rows} row(s); the two "
                "stages must be scoring the same batch."
            )
        consulted = ~known
        reported[consulted] = measured[consulted]
        anomalous = consulted & (measured >= float(tau_anom))

    kind = np.array([None] * rows, dtype=object)
    family = np.array([None] * rows, dtype=object)
    stage = np.array([None] * rows, dtype=object)

    kind[known] = KIND_KNOWN
    family[known] = best_family[known]
    stage[known] = STAGE1

    kind[anomalous] = KIND_UNCLASSIFIED_ANOMALY
    stage[anomalous] = STAGE2

    return FusionDecisions(
        kind=kind,
        family=family,
        stage=stage,
        confidence=confidence,
        anomaly_score=reported,
        tau_sup=float(tau_sup) if tau_sup is not None else None,
        tau_anom=float(tau_anom) if tau_anom is not None else None,
    )
