"""Phase 5 -- the measured numbers, loaded once beside the model bundle.

`backend/training/` writes three evaluation artifacts next to the model it
evaluated: `metrics_supervised.json` (Phase 2), `metrics_anomaly.json`
(Phase 3) and `metrics_loao.json` (Phase 4). The API serves them; it never
recomputes them, because the numbers on the Model Performance screen have to
be the numbers that were measured on the held-out day, not a fresh figure
computed from whatever happens to be in the database.

**Loaded once at startup, beside the bundle, not per request.** The three
files are written by the same offline run as the model, so re-reading them per
request would eventually let an endpoint answer from a newer file than the
model that is actually loaded and serving -- a model card describing a
champion that was replaced an hour ago. The cost is that a retrain needs a
restart before new numbers appear, which is already true of the model itself.

Absence is not an error. The phases land in order and the API is expected to
serve before any evaluation exists, so a missing file leaves its section empty
and the endpoint says so rather than inventing a plausible-looking curve.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPERVISED_METRICS_FILE = "metrics_supervised.json"
ANOMALY_METRICS_FILE = "metrics_anomaly.json"
LOAO_METRICS_FILE = "metrics_loao.json"


@dataclass
class MetricsStore:
    """The three evaluation artifacts, as loaded from disk."""

    artifacts_dir: Path
    supervised: dict[str, Any] = field(default_factory=dict)
    anomaly: dict[str, Any] = field(default_factory=dict)
    loao: dict[str, Any] = field(default_factory=dict)

    @property
    def is_loaded(self) -> bool:
        """True once at least one evaluation artifact is resident."""
        return bool(self.supervised or self.anomaly or self.loao)

    def load(self) -> MetricsStore:
        for attribute, filename in (
            ("supervised", SUPERVISED_METRICS_FILE),
            ("anomaly", ANOMALY_METRICS_FILE),
            ("loao", LOAO_METRICS_FILE),
        ):
            path = self.artifacts_dir / filename
            if not path.exists():
                logger.info(
                    "no %s in %s -- its metrics section will be empty", filename, path.parent
                )
                continue
            setattr(self, attribute, json.loads(path.read_text(encoding="utf-8")))
        return self

    # -- the shapes the endpoints read -----------------------------------

    @property
    def stage1_test(self) -> dict[str, Any]:
        """Stage 1's held-out-day evaluation, or `{}`."""
        return self.supervised.get("test") or {}

    @property
    def stage2_test(self) -> dict[str, Any]:
        """Stage 2's held-out-day evaluation, or `{}`."""
        return self.anomaly.get("test") or {}

    @property
    def budget(self) -> dict[str, Any]:
        """The false-positive budget the thresholds were cut against.

        Read from whichever artifact has it: both trainers record the same
        block, and the pair is written from one `Settings` so they agree.
        """
        return self.supervised.get("budget") or self.anomaly.get("budget") or {}

    def error_histogram(self, name: str) -> dict[str, Any] | None:
        """One of Stage 2's persisted error distributions, by name.

        `metrics_anomaly.json` carries `validation_benign`, `test_benign` and
        `test_attack` on **shared bin edges**, which is what makes an FPR and
        a recall at the same candidate threshold directly comparable: the two
        are read off the same axis rather than from two independently binned
        histograms whose boundaries do not line up.
        """
        histograms = (self.anomaly.get("training") or {}).get("histograms") or {}
        found = histograms.get(name)
        return found if isinstance(found, dict) and "counts" in found else None


def load_metrics(artifacts_dir: Path) -> MetricsStore:
    """Build and load the process-wide metrics store."""
    return MetricsStore(artifacts_dir=artifacts_dir).load()
