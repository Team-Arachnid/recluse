"""Phase 5 -- the CICIDS2017 lab host inventory.

``addresses_for`` turns a replayed row's attack family into a source and
destination address; ``criticality_for`` turns a replayed address into an
asset criticality. The tests exist to pin the rulings behind both:
addresses are deterministic and spread across a family's documented host
set rather than collapsed onto one pair (collapsing would fold a whole
family into a single dedupe bucket), the attacker side never leaks into the
asset inventory, an unknown family is refused rather than guessed at, and
``provenance()`` never hands out a dict a caller could corrupt for the next
one.
"""

from __future__ import annotations

import pytest

from app.models import ALERT_FAMILIES, SEVERITIES
from app.topology import DERIVED_FIELDS, HOSTS, addresses_for, criticality_for, provenance

# The attacker-side addresses named in the brief, transcribed independently
# of app.topology's own private constants, so this file does not just agree
# with itself about what counts as "attacker side".
_ATTACKER_ADDRESSES = {
    "172.16.0.1",
    "205.174.165.69",
    "205.174.165.70",
    "205.174.165.71",
    "205.174.165.73",
}

# The three servers (DNS/domain controller and the two public servers), as
# opposed to the ten workstations. Used below to check that
# UNCLASSIFIED_ANOMALY's destinations land on the workstation set, not a
# server.
_SERVER_ADDRESSES = {"192.168.10.3", "192.168.10.50", "192.168.10.51"}


# ---------------------------------------------------------------------------
# addresses_for: determinism and spread
# ---------------------------------------------------------------------------


def test_addresses_for_is_deterministic() -> None:
    assert addresses_for("brute_force", 3) == addresses_for("brute_force", 3)
    assert addresses_for(None, 7) == addresses_for(None, 7)


def test_ddos_spreads_across_its_three_sources() -> None:
    """No collapsing: a long replay must visit all three LOIC machines, not
    just the first one `index % len` happens to land on."""
    sources = {addresses_for("ddos", i)[0] for i in range(6)}
    assert sources == {"205.174.165.69", "205.174.165.70", "205.174.165.71"}


def test_botnet_spreads_across_its_five_destinations() -> None:
    destinations = {addresses_for("botnet", i)[1] for i in range(10)}
    assert destinations == {
        "192.168.10.15",
        "192.168.10.9",
        "192.168.10.14",
        "192.168.10.5",
        "192.168.10.8",
    }


def test_dos_spreads_across_both_destinations() -> None:
    """The resolved ambiguity: Heartbleed hit .51, and training/labels.py
    would call that web_attack, but the brief keeps both public servers
    under `dos` so the family does not collapse to a single dedupe bucket."""
    destinations = {addresses_for("dos", i)[1] for i in range(4)}
    assert destinations == {"192.168.10.50", "192.168.10.51"}


@pytest.mark.parametrize("family", ALERT_FAMILIES)
def test_every_attack_family_has_an_address(family: str) -> None:
    """Iterates the real vocabulary, so a family added to ALERT_FAMILIES
    without a matching topology entry breaks here, not at replay time."""
    src, dst = addresses_for(family, 0)
    assert src
    assert dst


def test_an_unknown_family_raises_and_names_the_vocabulary() -> None:
    """A family outside ALERT_FAMILIES is a bug upstream; the message says
    so by naming the real vocabulary rather than hiding the bug behind an
    invented address."""
    with pytest.raises(KeyError, match="ransomware") as exc_info:
        addresses_for("ransomware", 0)
    assert "ALERT_FAMILIES" in str(exc_info.value)


def test_a_none_family_returns_a_pair_instead_of_raising() -> None:
    src, dst = addresses_for(None, 0)
    assert src
    assert dst


def test_an_empty_string_family_also_returns_a_pair() -> None:
    src, dst = addresses_for("", 0)
    assert src
    assert dst


# ---------------------------------------------------------------------------
# UNCLASSIFIED_ANOMALY's fallback attribution
# ---------------------------------------------------------------------------


def test_unclassified_anomaly_sources_from_the_firewall_address() -> None:
    sources = {addresses_for(None, i)[0] for i in range(3)}
    assert sources == {"172.16.0.1"}


def test_unclassified_anomaly_destinations_are_known_workstations() -> None:
    """Every destination is a real HOSTS entry and never one of the three
    servers -- the weakest derivation in the module still has to land on a
    real victim-network host rather than inventing one."""
    for i in range(12):
        _, dst = addresses_for(None, i)
        assert criticality_for(dst) is not None
        assert dst not in _SERVER_ADDRESSES


# ---------------------------------------------------------------------------
# criticality_for and the asset inventory
# ---------------------------------------------------------------------------


def test_criticality_for_an_unknown_address_is_none() -> None:
    assert criticality_for("10.0.0.99") is None


@pytest.mark.parametrize("address", list(HOSTS))
def test_every_known_host_has_a_valid_criticality(address: str) -> None:
    assert criticality_for(address) in SEVERITIES


def test_the_criticality_tiers_match_the_briefs_assignment() -> None:
    """Pins the reviewed assignment, the same way test_playbook.py pins
    MITRE technique IDs, so a silent edit fails a test rather than a
    code review."""
    assert criticality_for("192.168.10.3") == "critical"  # DNS / domain controller
    assert criticality_for("192.168.10.50") == "critical"  # public web server
    assert criticality_for("192.168.10.51") == "high"  # second public server
    assert criticality_for("192.168.10.12") == "medium"  # Ubuntu workstation
    assert criticality_for("192.168.10.5") == "low"  # Windows workstation
    assert criticality_for("192.168.10.25") == "low"  # macOS workstation


def test_criticality_tiers_partition_the_victim_network() -> None:
    """2 critical, 1 high, 4 medium (the non-public Ubuntu hosts), 6 low
    (the Windows/macOS desktops) -- every host accounted for exactly once."""
    tiers = [criticality_for(address) for address in HOSTS]
    assert tiers.count("critical") == 2
    assert tiers.count("high") == 1
    assert tiers.count("medium") == 4
    assert tiers.count("low") == 6
    assert len(tiers) == 13


def test_no_host_in_the_inventory_is_an_attacker_side_address() -> None:
    """The inventory is assets only -- a later edit that folds an attacker
    address into HOSTS would silently misrepresent who owns it."""
    assert set(HOSTS).isdisjoint(_ATTACKER_ADDRESSES)


# ---------------------------------------------------------------------------
# provenance()
# ---------------------------------------------------------------------------


def test_provenance_returns_a_fresh_object_each_call() -> None:
    first = provenance()
    first["note"] = "mutated"
    first["new_key"] = "should not leak"
    second = provenance()
    assert second["note"] != "mutated"
    assert "new_key" not in second


def test_derived_fields_appear_in_provenance_as_derived() -> None:
    """The tuple and the map are not allowed to disagree about which fields
    are derived."""
    record = provenance()
    for field in DERIVED_FIELDS:
        assert record[field] == "derived"


def test_provenance_marks_exactly_the_documented_fields() -> None:
    record = provenance()
    assert record["dst_port"] == "observed"
    assert record["src_port"] == "absent"
    assert record["protocol"] == "absent"
    assert record["detected_at"] == "replay_clock"
    assert "derived" in record["note"]
