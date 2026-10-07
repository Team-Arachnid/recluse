"""Phase 2 -- the class vocabulary Stage 1 is trained against.

CICIDS2017 ships fifteen label strings. The model is trained on eight classes:
benign plus the seven families in ``app.models.ALERT_FAMILIES``. This module is
the single place that collapse happens, because the same mapping has to hold in
three places that must never disagree -- training, the Phase 4 hold-out loop,
and the family an alert carries into the dashboard.

Two rules govern it:

* **Nothing maps by accident.** Matching is on an exact canonical form of the
  published string, never on a substring: ``Web Attack Brute Force`` contains
  "brute force" and is *not* ``brute_force``. A label with no entry raises
  rather than silently becoming benign, which would delete an attack family
  from the training set without a word.
* **Collapsing is logged.** ``mapping_report`` prints which sub-families landed
  in which class and with how many rows, so a reader can see what a class
  actually consists of instead of trusting its name.

**Heartbleed maps to ``web_attack``.** The brief's eight-class vocabulary has no
slot for it, and MITRE T1190 -- malformed input aimed at a public-facing
application -- describes a malformed TLS heartbeat as well as it describes SQL
injection. That is the honest home for it, but it carries 11 rows in the whole
capture, which is the reason the support floor below exists.

**The support floor.** A collapsed class with a handful of rows is not a class a
tree ensemble can learn. It is worse than useless under ``class_weight
="balanced"``: 11 rows against 821,166 benign earns a weight above 20,000, and
the forest will distort its whole decision surface chasing them. Families below
``MIN_CLASS_SUPPORT`` rows are therefore held out of Stage 1's vocabulary and
reported as held out. They are not deleted from the data and not hidden -- they
are scored like any other traffic, and being unnameable by Stage 1 is exactly
the condition Stage 2 exists to cover.
"""

from __future__ import annotations

import re
from collections import Counter

import pandas as pd

BENIGN_FAMILY = "benign"

# Mirrors app.models.ALERT_FAMILIES, which is the wire contract. Duplicated
# rather than imported so the training package stays free of SQLAlchemy; a test
# asserts the two cannot drift apart.
ATTACK_FAMILIES: tuple[str, ...] = (
    "dos",
    "ddos",
    "brute_force",
    "port_scan",
    "web_attack",
    "botnet",
    "infiltration",
)

FAMILIES: tuple[str, ...] = (BENIGN_FAMILY, *ATTACK_FAMILIES)

# Below this many rows a class is a rounding error with a name. See the module
# docstring for why it is a floor rather than a weight.
MIN_CLASS_SUPPORT = 100

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


class UnmappedLabel(KeyError):
    """A published label with no entry in the collapse map.

    Fatal on purpose. The alternative -- a default bucket -- turns a dataset
    the maintainer has not looked at into a silently mislabelled training set.
    """


def canonical(label: str) -> str:
    """Reduce a published label to the form the collapse map is keyed on.

    Absorbs the spelling differences between the original release and its
    corrected re-releases, which separate the words of a web-attack label with
    a hyphen, an en dash, or nothing at all.

    >>> canonical("FTP-Patator"), canonical("Web Attack - XSS")
    ('ftp patator', 'web attack xss')
    """
    return _NON_ALNUM.sub(" ", label.strip().lower()).strip()


# Canonical published label -> class. Every string CICIDS2017 ships appears
# here; see `canonical` for how the published spellings reduce to these keys.
LABEL_TO_FAMILY: dict[str, str] = {
    "benign": BENIGN_FAMILY,
    "dos hulk": "dos",
    "dos goldeneye": "dos",
    "dos slowloris": "dos",
    "dos slowhttptest": "dos",
    "ddos": "ddos",
    "ftp patator": "brute_force",
    "ssh patator": "brute_force",
    "portscan": "port_scan",
    "web attack brute force": "web_attack",
    "web attack xss": "web_attack",
    "web attack sql injection": "web_attack",
    "heartbleed": "web_attack",
    "bot": "botnet",
    "infiltration": "infiltration",
}

# Analyst-supplied family labels (Phase 7), as a second and deliberately
# separate lookup.
#
# A verdict names a *family*, never a sub-family. An analyst confirming a Stage 1
# alert is asserting "this is DoS-family traffic"; they have not said which of
# the four published DoS variants it is. So the retraining pipeline labels the
# row with the family, and these identity entries are what let that through the
# collapse without inventing a sub-family for it.
#
# Kept out of `LABEL_TO_FAMILY` on purpose. That map's contract is "every string
# CICIDS2017 ships, and nothing else", and a test enforces it -- which is
# precisely the test that should fail if somebody ever adds a convenience bucket
# to it. These keys are not published labels, so they live behind their own name.
FAMILY_SELF_LABELS: dict[str, str] = {canonical(family): family for family in FAMILIES}


def to_family(label: str) -> str:
    """Collapse one label to its class, raising on anything unrecognised.

    Published labels first, then the analyst family labels of
    `FAMILY_SELF_LABELS`. The order matters only in that it keeps the published
    map authoritative: every string the dataset ships resolves through it, and a
    family name is only consulted as a name an analyst supplied.
    """
    key = canonical(label)
    family = LABEL_TO_FAMILY.get(key) or FAMILY_SELF_LABELS.get(key)
    if family is None:
        raise UnmappedLabel(
            f"{label!r} (canonical {key!r}) is neither a published CICIDS2017 "
            "label nor one of this project's family names. Add it deliberately "
            "-- an unmapped attack label must not be guessed at or dropped."
        )
    return family


def map_labels(labels: pd.Series) -> pd.Series:
    """Collapse a label column, reporting every unknown value at once.

    Mapped over the distinct values: labels are low-cardinality and the splits
    run to a million rows.
    """
    distinct = pd.unique(labels.astype(object))
    known = set(LABEL_TO_FAMILY) | set(FAMILY_SELF_LABELS)
    unknown = sorted(str(value) for value in distinct if canonical(str(value)) not in known)
    if unknown:
        raise UnmappedLabel(
            f"{len(unknown)} label(s) with no entry in LABEL_TO_FAMILY: {unknown}. "
            "Add them deliberately -- an unmapped attack label must not be "
            "guessed at or dropped."
        )

    mapping = {value: to_family(str(value)) for value in distinct}
    return labels.astype(object).map(mapping).astype("string")


def held_out_families(families: pd.Series, min_support: int = MIN_CLASS_SUPPORT) -> list[str]:
    """Attack classes too small to train on, smallest first.

    Benign is never a candidate: a split where benign is rare is a broken
    split, not a rare class.
    """
    counts = families.value_counts()
    rare = {
        str(family): int(count)
        for family, count in counts.items()
        if str(family) != BENIGN_FAMILY and int(count) < min_support
    }
    return [family for family, _ in sorted(rare.items(), key=lambda item: item[1])]


def vocabulary(families: pd.Series, min_support: int = MIN_CLASS_SUPPORT) -> list[str]:
    """The classes Stage 1 will actually be fitted on, in ``FAMILIES`` order.

    Fixed order matters downstream: the column order of ``predict_proba`` is
    the class order, and the persisted artifact records it so serving can map a
    column back to a family name.
    """
    present = set(families.astype(str))
    excluded = set(held_out_families(families, min_support))
    return [family for family in FAMILIES if family in present and family not in excluded]


def mapping_report(
    labels: pd.Series, families: pd.Series, min_support: int = MIN_CLASS_SUPPORT
) -> str:
    """Render which published labels collapsed into which class, with counts.

    The brief asks for the mapping to be logged rather than merely applied, and
    the counts are what make the log worth reading: they are how a reviewer
    sees that ``web_attack`` on the training days is 11 Heartbleed rows.
    """
    rolled: dict[str, Counter] = {}
    for label, family in zip(labels.astype(str), families.astype(str), strict=True):
        rolled.setdefault(family, Counter())[label] += 1

    excluded = set(held_out_families(families, min_support))
    lines: list[str] = []
    for family in FAMILIES:
        if family not in rolled:
            continue
        members = rolled[family]
        total = sum(members.values())
        note = "  [held out: below the support floor]" if family in excluded else ""
        lines.append(f"{family:<14} {total:>10,}{note}")
        for label, count in members.most_common():
            lines.append(f"    {label:<28} {count:>10,}")

    if excluded:
        lines.append(
            f"support floor    {min_support} rows -- "
            f"{', '.join(sorted(excluded))} excluded from Stage 1's vocabulary"
        )
    return "\n".join(lines)
