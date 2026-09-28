"""Threshold arithmetic and the quantities Stage 1 is judged on.

The threshold is the project's central engineering claim -- an operating point
derived from analyst capacity rather than defaulted to 0.5 -- so the derivation
is pinned here rather than trusted to a comment.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from training.metrics import (
    alert_volume,
    attack_confidence,
    attack_probability,
    detection_curves,
    false_positive_rate,
    predicted_attack_family,
    recall_by_family,
    select_threshold,
)

CLASSES = ["benign", "brute_force", "dos"]


def test_attack_confidence_is_the_largest_attack_class_not_one_minus_benign() -> None:
    """The two differ when evidence is split across families, and the fusion
    rule uses the first: a row Stage 1 cannot confidently *name* belongs to
    Stage 2."""
    proba = np.array([[0.4, 0.3, 0.3]])

    assert attack_confidence(proba, CLASSES)[0] == 0.3
    assert attack_probability(proba, CLASSES)[0] == pytest.approx(0.6)


def test_benign_never_wins_the_attack_family_slot() -> None:
    proba = np.array([[0.9, 0.06, 0.04]])

    assert predicted_attack_family(proba, CLASSES)[0] == "brute_force"


def test_threshold_is_the_smallest_one_inside_the_budget() -> None:
    """Smallest, not safest: every step above it discards recall the analysts
    had the capacity to absorb."""
    scores = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95])
    is_benign = np.ones(10, dtype=bool)

    choice = select_threshold(scores, is_benign, target_fpr=0.2)

    assert choice.tau == 0.9
    assert choice.fpr == 0.2
    assert choice.false_alerts == 2
    assert not choice.above_every_benign_score
    # One step lower would have been 3 benign rows in 10, over the budget.
    assert false_positive_rate(scores, is_benign, 0.8) > 0.2


def test_the_threshold_is_never_the_argmax_default() -> None:
    """A budget of 32 alerts in 100,000 flows cannot be met at 0.5."""
    rng = np.random.default_rng(3)
    scores = rng.uniform(0.0, 1.0, 20_000)
    is_benign = np.ones(20_000, dtype=bool)

    choice = select_threshold(scores, is_benign, target_fpr=320 / 1_000_000)

    assert choice.tau > 0.5
    assert choice.fpr <= 320 / 1_000_000


def test_a_budget_no_observed_score_satisfies_is_reported_not_hidden() -> None:
    """Every benign row tied at the top: the only threshold that fits is above
    all of them, and the caller has to be told rather than handed a number that
    looks ordinary."""
    scores = np.ones(100)
    is_benign = np.ones(100, dtype=bool)

    choice = select_threshold(scores, is_benign, target_fpr=0.001)

    assert choice.above_every_benign_score
    assert choice.fpr == 0.0
    assert choice.tau > 1.0


def test_false_positive_rate_counts_only_benign_rows() -> None:
    scores = np.array([0.9, 0.9, 0.1, 0.1])
    is_benign = np.array([True, False, True, False])

    assert false_positive_rate(scores, is_benign, 0.5) == 0.5


def test_the_projection_is_driven_by_false_positives_not_the_split_density() -> None:
    """A CICIDS2017 attack day is over a third attack traffic. Multiplying that
    density by a million flows would project a queue no real network produces,
    so the projection uses the false-positive rate and the measured alert rate
    stays a measurement of the split."""
    scores = np.array([0.9, 0.9, 0.1, 0.1])
    is_benign = np.array([True, False, True, True])

    volume = alert_volume(
        scores,
        is_benign,
        tau=0.5,
        daily_flow_volume=1_000_000,
        analyst_capacity_per_hour=40,
        analyst_shift_hours=8,
    )

    assert volume.alert_rate == 0.5
    assert volume.fpr == 1 / 3
    assert volume.attack_share_of_split == 0.25
    assert volume.false_alerts_per_day == pytest.approx(333_333.3, rel=1e-6)
    assert volume.alerts_per_analyst_hour == pytest.approx(41_666.7, rel=1e-6)
    assert not volume.within_budget


def test_detection_curves_report_both_areas_and_the_curves_themselves() -> None:
    rng = np.random.default_rng(5)
    is_attack = np.array([False] * 900 + [True] * 100)
    scores = np.concatenate([rng.uniform(0, 0.4, 900), rng.uniform(0.6, 1.0, 100)])

    curves = detection_curves(is_attack, scores)

    assert curves.pr_auc > 0.9
    assert curves.roc_auc > 0.9
    assert curves.pr_curve and curves.roc_curve
    assert curves.positives == 100
    assert curves.negatives == 900


def test_detection_curves_do_not_invent_a_number_for_a_one_class_split() -> None:
    curves = detection_curves(np.zeros(10, dtype=bool), np.linspace(0, 1, 10))

    assert np.isnan(curves.pr_auc)
    assert curves.pr_curve == []


def test_family_recall_counts_a_flag_regardless_of_the_name_given() -> None:
    """A DDoS flow flagged as `dos` is caught. The per-class report scores it as
    a misclassification; the fusion pipeline does not care."""
    truth = pd.Series(["ddos", "ddos", "ddos", "benign"])
    flagged = np.array([True, True, False, False])

    result = recall_by_family(truth, flagged, ["benign", "ddos"])

    assert result["ddos"] == {"support": 3, "flagged": 2, "recall": 2 / 3}
