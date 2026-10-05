"""Phase 5 -- the triage queue's ordering key and the severity badge beside it.

`risk_score` and `severity` are policy, not a known formula to check a
reference implementation against -- the module docstring in `app/risk.py` is
the spec, and these tests exist to pin the properties that policy is supposed
to have: the pinned endpoints the threshold-relative rescale is built around,
the degeneracy it exists to avoid, monotonicity, and the specific lift numbers
the brief named. They are deliberately not a transcription of the arithmetic
back into assertions with the same numbers -- where a number is checked, it is
derived independently (from the named constants, or from an independently
rebuilt histogram) rather than copied from the implementation.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from app.models import ALERT_KINDS, SEVERITIES
from app.risk import (
    CRITICALITY_LIFT,
    HISTORY_LIFT_MAX,
    HISTORY_SATURATES_AT,
    LIFT_CAP,
    SEVERITY_BANDS,
    risk_score,
    severity,
    stage1_base,
    stage2_base,
)
from training.metrics import ErrorHistogram

# The shipped champion's thresholds (the model card persists these on
# `model_versions.tau_sup`/`tau_anom`), used wherever a test needs a plausible
# operating point rather than an arbitrary one.
TAU_SUP = 0.38790842847095985
TAU_ANOM = 0.10981125503778419


def _benign_histogram(rows: int = 396_328) -> dict[str, Any]:
    """A dict shaped like `ModelBundle.benign_error_histogram` at runtime:
    60 log-spaced bins over roughly [1.1e-4, 1.83] (the real shipped range),
    right-skewed so the mass above TAU_ANOM is a thin tail -- about 0.6% of
    rows here, close to the real calibration's 0.5%. That closeness matters:
    it is the property stage2_base's threshold-relative rescale exists to
    correct for, so a fixture that did not have it would not exercise the
    thing under test.

    Built here rather than loaded from an artifact: risk.py is pure
    arithmetic and its tests should not need a trained bundle.
    """
    edges = np.geomspace(1.1e-4, 1.83, 61)
    decay = np.geomspace(1.0, 1e-3, 60)  # heavy at low error, thin at high error
    shares = decay / decay.sum()
    counts = np.floor(shares * rows).astype(int)
    counts[0] += rows - int(counts.sum())  # rounding remainder into the fattest bin
    return {
        "edges": edges.tolist(),
        "counts": counts.tolist(),
        "rows": rows,
        "percentiles": {
            "p50": float(edges[5]),
            "p90": float(edges[30]),
            "p99": float(edges[40]),
            "p99.5": TAU_ANOM,
            "p99.9": float(edges[50]),
            "min": float(edges[0]),
            "max": float(edges[-1]),
            "mean": float(edges[10]),
        },
        "spacing": "log",
    }


# ---------------------------------------------------------------------------
# The two pinned endpoints -- the whole point of the rescale
# ---------------------------------------------------------------------------


def test_stage1_base_is_zero_exactly_at_the_threshold() -> None:
    """An alert that only just cleared tau_sup is the least interesting thing
    that still alerted. Before any lift, that is a 0, not some positive
    number a raw probability would have reported."""
    assert stage1_base(TAU_SUP, TAU_SUP) == 0.0


def test_stage1_base_is_one_at_full_confidence() -> None:
    assert stage1_base(1.0, TAU_SUP) == 1.0


def test_risk_score_reproduces_the_pinned_stage1_endpoints_with_no_lift() -> None:
    """Same two pins, through the public entry point, with enrichment left at
    its no-op defaults so risk_score reduces to stage1_base exactly."""
    at_threshold = risk_score(
        kind="KNOWN", confidence=TAU_SUP, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None
    )
    at_max = risk_score(
        kind="KNOWN", confidence=1.0, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None
    )
    assert at_threshold == 0.0
    assert at_max == 1.0


# ---------------------------------------------------------------------------
# The degeneracy this design exists to avoid
# ---------------------------------------------------------------------------


def test_stage2_base_spreads_alerts_a_raw_percentile_would_have_crushed() -> None:
    """This is the regression that matters.

    tau_anom sits at ~99.5th percentile of benign error, so every
    UNCLASSIFIED_ANOMALY alert's *raw* benign-percentile already lives in
    [0.995, 1.0] -- a 0.005-wide sliver. Reporting that raw percentile would
    put a merely-over-threshold anomaly and an extreme one within 0.005 of
    each other: indistinguishable severities. stage2_base's threshold-relative
    rescale is supposed to turn that sliver back into the full [0, 1] range.

    This test proves it does by computing the raw percentile independently --
    via the same ErrorHistogram.above() the production code calls, not via
    app.risk's private helper -- and showing the rescaled spread dwarfs the
    raw one.
    """
    histogram = _benign_histogram()
    hist = ErrorHistogram(**histogram)

    low_error = 0.12  # just above tau_anom
    high_error = 1.2  # deep in the tail

    base_low = stage2_base(low_error, TAU_ANOM, histogram)
    base_high = stage2_base(high_error, TAU_ANOM, histogram)

    # What a raw-percentile base (the pre-flight ruling's original version)
    # would have reported instead, computed independently of stage2_base.
    raw_low = 1 - hist.above(low_error) / hist.rows
    raw_high = 1 - hist.above(high_error) / hist.rows
    raw_spread = raw_high - raw_low
    rescaled_spread = base_high - base_low

    assert base_high > base_low  # different bases, not a tie
    assert raw_spread < 0.01  # the raw percentiles are nearly indistinguishable
    assert rescaled_spread > 0.5  # the rescale recovers real separation
    assert rescaled_spread > 10 * raw_spread  # and it is the rescale doing it


# ---------------------------------------------------------------------------
# Monotonicity
# ---------------------------------------------------------------------------


def test_higher_confidence_never_lowers_the_score() -> None:
    confidences = [TAU_SUP, 0.5, 0.7, 0.85, 0.95, 1.0]
    scores = [stage1_base(c, TAU_SUP) for c in confidences]
    assert scores == sorted(scores)


def test_higher_anomaly_score_never_lowers_the_score_with_a_histogram() -> None:
    histogram = _benign_histogram()
    errors = [TAU_ANOM, 0.12, 0.2, 0.4, 0.8, 1.2, 1.8]
    scores = [stage2_base(e, TAU_ANOM, histogram) for e in errors]
    assert scores == sorted(scores)


def test_higher_anomaly_score_never_lowers_the_score_without_a_histogram() -> None:
    """The fallback branch: cruder, but still monotonic. Values stay below
    2x tau_anom except the last, so most of the sequence exercises the live
    part of the ramp rather than the post-clamp plateau at 1.0."""
    errors = [TAU_ANOM, 0.12, 0.14, 0.17, 0.20, 0.2196, 5.0]
    scores = [stage2_base(e, TAU_ANOM, None) for e in errors]
    assert scores == sorted(scores)
    assert all(0.0 <= s <= 1.0 for s in scores)


# ---------------------------------------------------------------------------
# Lift
# ---------------------------------------------------------------------------


def test_critical_plus_saturated_history_hits_the_cap_not_the_sum() -> None:
    """0.30 + 0.15 is 0.45; LIFT_CAP is 0.40. The capped value, not the
    uncapped sum, is what must show up in the result."""
    base_only = risk_score(
        kind="KNOWN", confidence=0.6, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None
    )
    lifted = risk_score(
        kind="KNOWN",
        confidence=0.6,
        anomaly_score=None,
        tau_sup=TAU_SUP,
        tau_anom=None,
        asset_criticality="critical",
        host_prior_alert_count=HISTORY_SATURATES_AT,
    )
    base = stage1_base(0.6, TAU_SUP)
    expected_capped = base + (1 - base) * LIFT_CAP
    expected_uncapped = base + (1 - base) * (CRITICALITY_LIFT["critical"] + HISTORY_LIFT_MAX)

    assert lifted <= 1.0
    assert lifted > base_only
    assert lifted == round(expected_capped, 4)
    assert lifted != round(expected_uncapped, 4)  # the cap actually bit


def test_asset_criticality_none_and_low_score_identically() -> None:
    """The documented intent: unassessed is not evidence of anything. A later
    edit that defaults unknown hosts to a nonzero lift breaks this test, not
    some assertion buried inside risk_score's body."""
    common: dict[str, Any] = dict(
        kind="UNCLASSIFIED_ANOMALY",
        confidence=None,
        anomaly_score=0.5,
        tau_sup=None,
        tau_anom=TAU_ANOM,
        histogram=None,
        host_prior_alert_count=3,
    )
    assert risk_score(**common, asset_criticality=None) == risk_score(
        **common, asset_criticality="low"
    )


def test_history_lift_saturates() -> None:
    """10 prior alerts and 500 prior alerts must give the same score, or the
    saturation the brief specifies is not actually happening."""
    common: dict[str, Any] = dict(
        kind="KNOWN", confidence=0.6, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None
    )
    at_saturation = risk_score(**common, host_prior_alert_count=HISTORY_SATURATES_AT)
    way_past_saturation = risk_score(**common, host_prior_alert_count=500)
    assert at_saturation == way_past_saturation


# ---------------------------------------------------------------------------
# Refusing to guess
# ---------------------------------------------------------------------------


def test_known_alert_without_confidence_raises() -> None:
    with pytest.raises(ValueError, match="confidence"):
        risk_score(
            kind="KNOWN", confidence=None, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None
        )


def test_known_alert_without_tau_sup_raises() -> None:
    with pytest.raises(ValueError, match="tau_sup"):
        risk_score(kind="KNOWN", confidence=0.9, anomaly_score=None, tau_sup=None, tau_anom=None)


def test_unclassified_anomaly_without_anomaly_score_raises() -> None:
    with pytest.raises(ValueError, match="anomaly_score"):
        risk_score(
            kind="UNCLASSIFIED_ANOMALY",
            confidence=None,
            anomaly_score=None,
            tau_sup=None,
            tau_anom=TAU_ANOM,
        )


def test_unclassified_anomaly_without_tau_anom_raises() -> None:
    with pytest.raises(ValueError, match="tau_anom"):
        risk_score(
            kind="UNCLASSIFIED_ANOMALY",
            confidence=None,
            anomaly_score=0.5,
            tau_sup=None,
            tau_anom=None,
        )


def test_unknown_kind_raises_naming_the_vocabulary() -> None:
    with pytest.raises(ValueError) as exc_info:
        risk_score(
            kind="SUSPICIOUS", confidence=0.9, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None
        )
    message = str(exc_info.value)
    assert "ALERT_KINDS" in message
    # Named against the real tuple, not just the string "ALERT_KINDS", so a
    # future rename of the vocabulary's values is caught here too.
    assert all(kind in message for kind in ALERT_KINDS)


# ---------------------------------------------------------------------------
# The histogram fallback
# ---------------------------------------------------------------------------


def test_histogram_none_still_returns_a_score_in_range() -> None:
    score = risk_score(
        kind="UNCLASSIFIED_ANOMALY",
        confidence=None,
        anomaly_score=0.5,
        tau_sup=None,
        tau_anom=TAU_ANOM,
        histogram=None,
    )
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (0.0, "low"),
        (0.1, "low"),
        (0.24999, "low"),
        (0.25, "medium"),  # boundary: lands in the higher band
        (0.4, "medium"),
        (0.5, "high"),  # boundary: lands in the higher band
        (0.6, "high"),
        (0.75, "critical"),  # boundary: lands in the higher band
        (0.9, "critical"),
        (1.0, "critical"),
    ],
)
def test_severity_bands(risk: float, expected: str) -> None:
    assert severity(risk) == expected


def test_severity_band_names_are_the_real_vocabulary() -> None:
    """Asserted against app.models.SEVERITIES itself, not a copy of it, so
    the two vocabularies cannot silently drift apart."""
    assert {label for _, label in SEVERITY_BANDS} == set(SEVERITIES)


def test_severity_rejects_a_score_outside_risk_scores_contract() -> None:
    """severity() is a total function over [0, 1], not over all floats.

    risk_score() never produces a value below 0.0, so a negative input here
    means a caller passed something that did not come from risk_score. That
    should fail loudly, not fall through SEVERITY_BANDS' last (0.00, "low")
    floor and return None from a function typed to return str.
    """
    with pytest.raises(ValueError, match="0, 1"):
        severity(-0.01)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_risk_score_stays_in_range_across_a_sweep_of_inputs() -> None:
    histogram = _benign_histogram()
    cases: list[dict[str, Any]] = [
        dict(kind="KNOWN", confidence=TAU_SUP, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None),
        dict(kind="KNOWN", confidence=1.0, anomaly_score=None, tau_sup=TAU_SUP, tau_anom=None),
        dict(
            kind="KNOWN",
            confidence=0.7,
            anomaly_score=None,
            tau_sup=TAU_SUP,
            tau_anom=None,
            asset_criticality="critical",
            host_prior_alert_count=500,
        ),
        dict(
            kind="UNCLASSIFIED_ANOMALY",
            confidence=None,
            anomaly_score=TAU_ANOM,
            tau_sup=None,
            tau_anom=TAU_ANOM,
            histogram=histogram,
        ),
        dict(
            kind="UNCLASSIFIED_ANOMALY",
            confidence=None,
            anomaly_score=1.8,
            tau_sup=None,
            tau_anom=TAU_ANOM,
            histogram=histogram,
            asset_criticality="high",
            host_prior_alert_count=2,
        ),
        dict(
            kind="UNCLASSIFIED_ANOMALY",
            confidence=None,
            anomaly_score=0.5,
            tau_sup=None,
            tau_anom=TAU_ANOM,
            histogram=None,
        ),
    ]
    for case in cases:
        score = risk_score(**case)
        assert 0.0 <= score <= 1.0
        assert severity(score) in SEVERITIES
