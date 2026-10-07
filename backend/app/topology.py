"""Phase 5 -- the CICIDS2017 lab host inventory: addresses and asset criticality
for a replayed flow.

The published MachineLearningCSV release of CICIDS2017 ships no addresses: Flow
ID, Source IP, Destination IP, Source Port, Protocol and Timestamp were all
stripped before publication (see ``training/features.py``'s ``LEAKAGE_COLUMNS``
comment and its ``DAY_COLUMN`` note). A replayed row therefore has a destination
port and seventy flow statistics, and nothing to put in ``alerts.src_ip`` or
``alerts.dst_ip`` -- both ``NOT NULL`` -- and nothing to build the dedupe key's
source host from (``app/dedupe.py::dedupe_key``).

The decision taken (recorded in the plan's ledger as a user decision, asked and
answered, and binding on this module) is to derive addresses from the published
lab topology for the alert's attack family, rather than re-run Phases 1-4
against the GeneratedLabelledFlows release or make the schema nullable. Every
surface that shows a derived address says so -- that is what ``provenance()``
and ``DERIVED_FIELDS`` are for. Making the derivation honest and inspectable,
rather than handing out a plausible-looking IP with no caveat attached, is the
point of this module.

A family's rows are spread deterministically across the documented host *set*
for that family (``addresses_for``) rather than collapsed onto one pair,
because ``dedupe_key`` keys on the source host: collapsing a family onto one
pair would fold the whole family into a single alert per dedupe window and
leave the top-hosts tables degenerate.

Topology and per-family attribution are from the dataset's own documentation:
Sharafaldin, Lashkari, Ghorbani, "Toward Generating a New Intrusion Detection
Dataset and Intrusion Traffic Characterization", ICISSP 2018, plus the per-day
attack schedule published with the capture.

``UNCLASSIFIED_ANOMALY`` (Stage 2's output) has no documented attribution --
nobody labelled this traffic, so there is nothing in the capture schedule to
point at. It still gets an address, because an alert that cannot be stored is
worse than one carrying the weakest derivation in the module; see
``addresses_for``.
"""

from __future__ import annotations

from typing import TypedDict

from app.models import ALERT_FAMILIES


class Host(TypedDict):
    """One row of the lab asset inventory."""

    label: str
    asset_criticality: str  # one of app.models.SEVERITIES


class Attribution(TypedDict):
    """Sources and destinations the capture schedule documents for one family."""

    sources: tuple[str, ...]
    destinations: tuple[str, ...]


# ---------------------------------------------------------------------------
# HOSTS -- the victim network, 192.168.10.0/24
#
# Criticality is assigned by what the host is, not by vibe:
#   critical -- the DNS/domain controller (the network's identity) and the
#               public web server (the only thing the internet can reach).
#   high     -- the second public server.
#   medium   -- the other Ubuntu hosts. The published topology's own host
#               column calls these "workstation", same word it uses for the
#               Windows/macOS boxes -- but the criticality bucket is keyed on
#               running a server-class OS rather than a desktop one, so the
#               label text and the bucket name disagree on the word
#               "workstation". That is intentional, not a transcription slip.
#   low      -- the Windows and macOS desktop workstations.
# ---------------------------------------------------------------------------
HOSTS: dict[str, Host] = {
    "192.168.10.3": {
        "label": "DNS / domain controller",
        "asset_criticality": "critical",
    },
    "192.168.10.50": {
        "label": "Web server, Ubuntu 16 (public; also 205.174.165.68)",
        "asset_criticality": "critical",
    },
    "192.168.10.51": {
        "label": "Ubuntu server 12 (public; also 205.174.165.66)",
        "asset_criticality": "high",
    },
    "192.168.10.5": {
        "label": "Windows 8.1, 64-bit workstation",
        "asset_criticality": "low",
    },
    "192.168.10.8": {
        "label": "Windows Vista, 64-bit workstation",
        "asset_criticality": "low",
    },
    "192.168.10.9": {
        "label": "Windows 7 Pro, 64-bit workstation",
        "asset_criticality": "low",
    },
    "192.168.10.12": {
        "label": "Ubuntu 16.4, 64-bit workstation",
        "asset_criticality": "medium",
    },
    "192.168.10.14": {
        "label": "Windows 10 Pro, 32-bit workstation",
        "asset_criticality": "low",
    },
    "192.168.10.15": {
        "label": "Windows 10, 64-bit workstation",
        "asset_criticality": "low",
    },
    "192.168.10.16": {
        "label": "Ubuntu 16.4, 32-bit workstation",
        "asset_criticality": "medium",
    },
    "192.168.10.17": {
        "label": "Ubuntu 14.4, 64-bit workstation",
        "asset_criticality": "medium",
    },
    "192.168.10.19": {
        "label": "Ubuntu 14.4, 32-bit workstation",
        "asset_criticality": "medium",
    },
    "192.168.10.25": {
        "label": "macOS workstation",
        "asset_criticality": "low",
    },
}


# ---------------------------------------------------------------------------
# Attacker side. Not assets -- deliberately absent from HOSTS, because nothing
# in the lab owns this equipment and an inventory that priced it would be
# assessing gear that is not the lab's.
# ---------------------------------------------------------------------------
_KALI = "172.16.0.1"  # reaches the victim network through the firewall
_LOIC_SOURCES = ("205.174.165.69", "205.174.165.70", "205.174.165.71")
_ARES_C2 = "205.174.165.73"

# UNCLASSIFIED_ANOMALY's destinations: every host in the inventory other than
# the domain controller and the two public servers. Built from HOSTS rather
# than typed out again, so this set cannot drift from the inventory above.
_SERVERS = ("192.168.10.3", "192.168.10.50", "192.168.10.51")
_WORKSTATIONS = tuple(address for address in HOSTS if address not in _SERVERS)


# ---------------------------------------------------------------------------
# ATTRIBUTION -- per-family sources and destinations, as the capture schedule
# documents them. UNCLASSIFIED_ANOMALY is deliberately not a key here: it has
# no family, so `addresses_for` branches on an empty family before this dict
# is ever consulted, and keeping it out means a test that iterates
# app.models.ALERT_FAMILIES cannot be accidentally satisfied by it.
# ---------------------------------------------------------------------------
ATTRIBUTION: dict[str, Attribution] = {
    "brute_force": {
        "sources": (_KALI,),
        "destinations": ("192.168.10.50",),
    },
    "dos": {
        "sources": (_KALI,),
        # Two destinations, deliberately. The four DoS tools (Slowloris,
        # Slowhttptest, Hulk, GoldenEye) hit .50; Heartbleed hit .51. But
        # training/labels.py maps Heartbleed to web_attack, not dos -- so
        # strictly, the .51 destination belongs to web_attack's row, not this
        # one. Both public servers genuinely were targets that week, and
        # keeping both here is what stops `dos` degenerating to a single
        # dedupe bucket; tying this table to the exact label-to-family
        # collapse would just make it a second copy of training/labels.py.
        "destinations": ("192.168.10.50", "192.168.10.51"),
    },
    "ddos": {
        "sources": _LOIC_SOURCES,
        "destinations": ("192.168.10.50",),
    },
    "port_scan": {
        "sources": (_KALI,),
        "destinations": ("192.168.10.50",),
    },
    "web_attack": {
        "sources": (_KALI,),
        "destinations": ("192.168.10.50",),
    },
    "botnet": {
        "sources": (_ARES_C2,),
        "destinations": (
            "192.168.10.15",
            "192.168.10.9",
            "192.168.10.14",
            "192.168.10.5",
            "192.168.10.8",
        ),
    },
    "infiltration": {
        # Source is inside the victim network, deliberately -- this is the
        # point of the family. 192.168.10.8 was compromised first (Dropbox /
        # Cool Disk), then used to scan and move laterally. A source outside
        # the victim network would misrepresent what the capture shows.
        "sources": ("192.168.10.8",),
        "destinations": (
            "192.168.10.25",
            "192.168.10.9",
            "192.168.10.12",
            "192.168.10.16",
        ),
    },
}


def addresses_for(family: str | None, index: int) -> tuple[str, str]:
    """The ``(src_ip, dst_ip)`` pair for the ``index``-th row of ``family``.

    Deterministic: the same ``(family, index)`` always returns the same pair,
    with no RNG and no global state, so a replay is reproducible and a test
    can assert an exact value.

    Spreads across the documented set rather than always returning one fixed
    pair -- ``sources[index % len(sources)]`` and
    ``destinations[index % len(destinations)]``. The same ``index`` drives
    both lists, so the pairing walks them in lockstep rather than covering
    their cross-product; that is deliberate, because the published schedule
    describes concurrent sessions rather than a full mesh, and a
    cross-product would invent pairs the capture never had.

    ``None`` and the empty string both mean UNCLASSIFIED_ANOMALY: Stage 2
    fires on traffic nobody labelled, so there is no documented attribution
    for it. It still gets an address rather than raising -- ``172.16.0.1`` as
    source and the victim network's workstations as destinations -- because
    Stage 2 alerts are the project's headline and they need an address to be
    storable. This is the weakest derivation in the module, which is exactly
    why ``provenance()`` exists: the caveat travels with the alert instead of
    being silently assumed away.

    A family that is neither empty nor in ``ATTRIBUTION`` is a bug upstream --
    the model can only emit ``app.models.ALERT_FAMILIES`` -- so this raises
    instead of inventing an address that would hide the bug, for the same
    reason ``app/mitre.py::technique_for`` and
    ``app/remediation.py::playbook_for`` raise.
    """
    if not family:
        sources: tuple[str, ...] = (_KALI,)
        destinations: tuple[str, ...] = _WORKSTATIONS
    else:
        try:
            attribution = ATTRIBUTION[family]
        except KeyError as exc:
            raise KeyError(
                f"{family!r} has no entry in ATTRIBUTION. The alert vocabulary "
                f"is app.models.ALERT_FAMILIES {ALERT_FAMILIES}; a family "
                "outside it reaching here means something upstream invented "
                "one, and inventing an address for it would hide that rather "
                "than surface it."
            ) from exc
        sources, destinations = attribution["sources"], attribution["destinations"]

    return sources[index % len(sources)], destinations[index % len(destinations)]


def criticality_for(address: str) -> str | None:
    """The asset criticality for ``address``, or ``None`` if it is not ours.

    ``None`` covers every attacker-side address as well as anything outside
    the lab entirely. It is the honest answer rather than a default of
    ``"low"``: a ``"low"`` for an address this module has never seen would
    read as "we assessed this and it does not matter," when the truth is that
    nothing was assessed at all.
    """
    host = HOSTS.get(address)
    return host["asset_criticality"] if host is not None else None


# The field names that are derived rather than observed. The UI and the tests
# both need to ask "is this field trustworthy" without string-matching `note`.
DERIVED_FIELDS: tuple[str, ...] = ("src_ip", "dst_ip")


# What `detected_at` means for each way a flow can reach the pipeline. The
# note's last sentence is chosen from here, so an alert never claims a clock
# its source did not have.
CLOCKS: dict[str, str] = {
    "replay_clock": "detected_at is replay wall-clock time, not the original capture time.",
    "seed_clock": (
        "detected_at was assigned by `make seed`, which spreads a replay of the "
        "committed demo flows evenly across the hours before it ran: it is neither "
        "the original capture time nor the moment the flow was scored."
    ),
}


def provenance(clock: str = "replay_clock") -> dict[str, str]:
    """The per-field provenance map for ``raw_flow["_provenance"]``.

    Four facts, exactly as the ruling states them: ``dst_port`` is observed
    (the release ships it); ``src_ip``/``dst_ip`` are derived (this module,
    see ``DERIVED_FIELDS``); ``src_port``/``protocol`` are absent (the release
    never shipped them); and ``detected_at`` is replay wall-clock (a replay
    genuinely detects at replay time, not at the original 2017 capture time)
    -- or, for ``make seed``, the seed's own even spread, which is neither
    (``clock="seed_clock"``; see ``CLOCKS``).
    ``note`` is the human-readable version of the same four facts, so the
    Alert Detail drawer can render the caveat without the frontend hardcoding
    the sentence.

    The derived entries are built from ``DERIVED_FIELDS`` instead of being
    typed out a second time, so the tuple and the map cannot disagree.

    Returns a fresh dict every call. A module-level dict handed out by
    reference would let whoever stores it mutate the shared instance, and the
    next alert would silently inherit the corruption.
    """
    fields: dict[str, str] = dict.fromkeys(DERIVED_FIELDS, "derived")
    fields.update(
        dst_port="observed",
        src_port="absent",
        protocol="absent",
        detected_at=clock,
        note=(
            "src_ip and dst_ip are derived from the published CICIDS2017 lab "
            "topology, not observed: the MachineLearningCSV release strips "
            "Flow ID, Source IP, Destination IP, Source Port, Protocol and "
            "Timestamp before publication. dst_port survives and is observed; "
            "src_port and protocol are absent and stay null; " + CLOCKS[clock]
        ),
    )
    return fields
