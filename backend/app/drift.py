"""Phase 7 -- drift detection.

Nightly job computing Population Stability Index per feature against the
training reference distribution, storing snapshots:

    PSI = sum over bins of (actual_pct - expected_pct) * ln(actual_pct / expected_pct)

Warning bands: 0.1 moderate, 0.25 significant. Crossing 0.25 raises the
retrain-recommended banner on the drift screen.

Also overlays the training benign score distribution on the last 24 hours --
when those curves separate, the baseline has moved.
"""

from __future__ import annotations

from typing import Any


def population_stability_index(*_: Any, **__: Any) -> float:
    raise NotImplementedError("PSI is implemented in Phase 7 (drift and active learning).")
