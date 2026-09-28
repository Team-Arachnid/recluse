"""The class vocabulary Stage 1 trains against.

Every one of these is a defect that would be silent in production: a family
that maps to the wrong class, a label that disappears, or a vocabulary that
drifts from the wire contract. None of them raises on its own.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.models import ALERT_FAMILIES
from training.labels import (
    ATTACK_FAMILIES,
    BENIGN_FAMILY,
    LABEL_TO_FAMILY,
    MIN_CLASS_SUPPORT,
    UnmappedLabel,
    canonical,
    held_out_families,
    map_labels,
    mapping_report,
    to_family,
    vocabulary,
)

# Every label string CICIDS2017 ships, in the spelling clean.py produces.
PUBLISHED_LABELS: tuple[str, ...] = (
    "BENIGN",
    "DoS Hulk",
    "DoS GoldenEye",
    "DoS slowloris",
    "DoS Slowhttptest",
    "DDoS",
    "FTP-Patator",
    "SSH-Patator",
    "PortScan",
    "Web Attack Brute Force",
    "Web Attack XSS",
    "Web Attack Sql Injection",
    "Heartbleed",
    "Bot",
    "Infiltration",
)


def test_the_training_vocabulary_matches_the_wire_contract() -> None:
    """Two copies exist so the training package stays free of SQLAlchemy.

    They must not drift: a family the model can emit and the database rejects
    is an alert that fails to insert at runtime.
    """
    assert ATTACK_FAMILIES == ALERT_FAMILIES


@pytest.mark.parametrize("label", PUBLISHED_LABELS)
def test_every_published_label_maps(label: str) -> None:
    assert to_family(label) in (BENIGN_FAMILY, *ATTACK_FAMILIES)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("BENIGN", "benign"),
        ("DoS Hulk", "dos"),
        ("DDoS", "ddos"),
        ("FTP-Patator", "brute_force"),
        ("PortScan", "port_scan"),
        ("Infiltration", "infiltration"),
        ("Bot", "botnet"),
        # Heartbleed has no slot of its own in the eight-class vocabulary.
        # T1190 -- malformed input to a public-facing service -- covers it.
        ("Heartbleed", "web_attack"),
    ],
)
def test_families_collapse_where_they_should(published: str, expected: str) -> None:
    assert to_family(published) == expected


@pytest.mark.parametrize(
    "spelling",
    [
        "Web Attack Brute Force",
        "Web Attack - Brute Force",
        "Web Attack – Brute Force",
        "  web attack   brute force  ",
    ],
)
def test_web_attack_brute_force_is_a_web_attack_not_a_brute_force(spelling: str) -> None:
    """The trap this mapping exists to avoid.

    The string contains "Brute Force", so any substring match puts Thursday's
    web attacks into Tuesday's class -- which would also put a family on both
    sides of the Phase 4 hold-out loop and quietly invalidate its result. The
    variants cover the original release, its corrected re-releases and the
    spacing clean.py normalises away.
    """
    assert to_family(spelling) == "web_attack"


def test_an_unknown_label_raises_rather_than_becoming_benign() -> None:
    """A default bucket would delete an attack family from training in silence."""
    with pytest.raises(UnmappedLabel):
        to_family("Some Attack Nobody Mapped")


def test_map_labels_reports_every_unknown_at_once() -> None:
    series = pd.Series(["BENIGN", "Novel One", "Novel Two"])

    with pytest.raises(UnmappedLabel) as excinfo:
        map_labels(series)

    assert "Novel One" in str(excinfo.value)
    assert "Novel Two" in str(excinfo.value)


def test_canonical_absorbs_punctuation_and_case() -> None:
    assert canonical("FTP-Patator") == "ftp patator"
    assert canonical("  DoS   slowloris ") == "dos slowloris"


def test_the_map_covers_the_published_set_and_nothing_invented() -> None:
    assert {canonical(label) for label in PUBLISHED_LABELS} == set(LABEL_TO_FAMILY)


def test_rare_classes_fall_below_the_support_floor() -> None:
    families = pd.Series(["benign"] * 500 + ["dos"] * 200 + ["web_attack"] * 5)

    assert held_out_families(families) == ["web_attack"]
    assert vocabulary(families) == ["benign", "dos"]


def test_benign_is_never_held_out_however_rare() -> None:
    """A split where benign is rare is a broken split, not a rare class."""
    families = pd.Series(["benign"] * 3 + ["dos"] * 400)

    assert held_out_families(families) == []


def test_vocabulary_is_returned_in_a_fixed_order() -> None:
    """The order is the column order of predict_proba, so it cannot be incidental."""
    families = pd.Series(["port_scan"] * 200 + ["dos"] * 200 + ["benign"] * 200)

    assert vocabulary(families) == ["benign", "dos", "port_scan"]


def test_the_mapping_report_shows_what_a_class_is_made_of() -> None:
    labels = pd.Series(["BENIGN"] * 300 + ["DoS Hulk"] * 150 + ["Heartbleed"] * 5)
    families = map_labels(labels)

    rendered = mapping_report(labels, families)

    assert "DoS Hulk" in rendered
    assert "Heartbleed" in rendered
    assert "held out" in rendered
    assert str(MIN_CLASS_SUPPORT) in rendered
