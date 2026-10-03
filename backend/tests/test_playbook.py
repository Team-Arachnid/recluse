"""Phase 5 -- the two reviewed lookups behind "what is this / how do I fix it".

Both are static tables, and the tests exist mostly to pin the one entry that
matters most: the honest one. `UNCLASSIFIED_ANOMALY` has no MITRE technique and
no playbook, and the system has to say so rather than attach the nearest-looking
advice. A wrong playbook does more damage than an admitted gap, because a SOC
acts on it.

The rest of the assertions guard the join: every family the model can emit needs
an entry, or an alert reaches the dashboard with an empty "how to fix" panel.
"""

from __future__ import annotations

import pytest

from app.mitre import TECHNIQUES, technique_for
from app.models import ALERT_FAMILIES
from app.remediation import PLAYBOOKS, playbook_for

# ---------------------------------------------------------------------------
# Coverage: a family with no entry is a blank panel on the Alert Detail screen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", ALERT_FAMILIES)
def test_every_attack_family_has_a_technique(family: str) -> None:
    entry = technique_for(family)

    assert entry is not None
    assert entry["technique_id"]
    assert entry["name"]
    assert entry["means"], "the plain-English line the analyst reads instead of opening a tab"


@pytest.mark.parametrize("family", ALERT_FAMILIES)
def test_every_attack_family_has_a_playbook(family: str) -> None:
    entry = playbook_for(family)

    assert entry is not None
    assert entry["actions"], "a playbook with no actions is a blank panel"
    assert all(isinstance(action, str) and action for action in entry["actions"])


# ---------------------------------------------------------------------------
# The honest entry
# ---------------------------------------------------------------------------


def test_an_unclassified_anomaly_maps_to_no_technique() -> None:
    """Saying so plainly is the point of Stage 2, not a gap in the table.

    The brief is explicit: for `UNCLASSIFIED_ANOMALY` the panel says "doesn't
    match a known technique -- this is exactly what Stage 2 exists to catch".
    Attaching the nearest-looking ATT&CK ID would turn the project's strongest
    claim into a mislabelled alert.
    """
    assert technique_for(None) is None
    assert technique_for("") is None


def test_an_unclassified_anomaly_gets_an_honest_playbook_not_an_empty_one() -> None:
    """There is no playbook, and that is different from there being no answer.

    An empty response would render as a missing panel, which reads as a bug. The
    honest answer is a populated panel that says no playbook exists yet and
    routes the alert for manual investigation.
    """
    entry = playbook_for(None)

    assert entry is not None
    assert entry["technique_id"] is None
    assert entry["actions"], "the no-playbook case still tells the analyst what to do"
    assert "manual" in " ".join(entry["actions"]).lower()
    assert entry["has_playbook"] is False


def test_every_real_family_is_marked_as_having_a_playbook() -> None:
    assert all(playbook_for(family)["has_playbook"] for family in ALERT_FAMILIES)


# ---------------------------------------------------------------------------
# The table is reviewed, not generated
# ---------------------------------------------------------------------------


def test_the_techniques_match_the_briefs_reviewed_table() -> None:
    """These IDs were chosen deliberately, so a silent edit should fail a test."""
    assert technique_for("brute_force")["technique_id"] == "T1110"
    assert technique_for("port_scan")["technique_id"] == "T1046"
    assert technique_for("web_attack")["technique_id"] == "T1190"
    assert technique_for("botnet")["technique_id"] == "T1071"
    assert technique_for("infiltration")["technique_id"] == "T1204"
    # DoS and DDoS share a pair: endpoint denial of service and network denial
    # of service are the two halves of the same objective.
    assert technique_for("dos")["technique_id"] == "T1499"
    assert technique_for("ddos")["technique_id"] == "T1498"


def test_no_playbook_action_contains_an_automatic_containment_instruction() -> None:
    """The system alerts; a human contains.

    The playbook may tell an analyst to block a source range -- that is advice
    to a person. What it must never read as is something the system does by
    itself, because then the static table becomes the auto-block path the whole
    design refuses to have.
    """
    for family in [*ALERT_FAMILIES, None]:
        text = " ".join(playbook_for(family)["actions"]).lower()
        assert "automatically" not in text
        assert "auto-block" not in text


def test_the_two_tables_agree_on_which_families_exist() -> None:
    """They are joined per alert, so a family in one and not the other is a
    half-populated detail panel."""
    assert set(TECHNIQUES) == set(ALERT_FAMILIES)
    assert set(PLAYBOOKS) == set(ALERT_FAMILIES)


def test_an_unknown_family_is_refused_rather_than_guessed() -> None:
    """A family that is not in the vocabulary is a bug upstream, and guessing a
    technique for it would hide that bug behind plausible advice."""
    with pytest.raises(KeyError, match="ransomware"):
        technique_for("ransomware")
    with pytest.raises(KeyError, match="ransomware"):
        playbook_for("ransomware")
