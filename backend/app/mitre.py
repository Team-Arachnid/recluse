"""Phase 5 -- attack family to MITRE ATT&CK technique mapping.

A static, reviewed lookup. Each entry carries the technique ID plus a one-line
plain-English description, so the Alert Detail screen can answer *what this
likely is* without the analyst opening a second tab.

UNCLASSIFIED_ANOMALY maps to no technique, on purpose. Saying so plainly is the
honest answer and it is the whole point of Stage 2: a flow that does not match a
known technique is exactly what the second stage exists to surface, and
attaching the nearest-looking ATT&CK ID would turn the project's strongest claim
into a mislabelled alert. ``technique_for`` therefore returns ``None`` for it
rather than a filler entry, and the dashboard renders that absence as a
sentence.

The same table backs the MITRE coverage heatmap on the analytics screen, which
is why the counts there can show a technique with zero hits as well as one with
many -- the vocabulary comes from here, not from whatever happened to fire.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Technique(TypedDict):
    """One row of the lookup."""

    technique_id: str
    name: str
    url: str
    # The line an analyst reads instead of opening attack.mitre.org. Written to
    # be true of *this* traffic rather than a paraphrase of the ATT&CK page.
    means: str


_BASE = "https://attack.mitre.org/techniques/"


TECHNIQUES: dict[str, Technique] = {
    "brute_force": {
        "technique_id": "T1110",
        "name": "Brute Force",
        "url": f"{_BASE}T1110/",
        "means": ("Repeated login attempts against one service, guessing credentials."),
    },
    "dos": {
        # T1499 is endpoint denial of service: exhausting one service's
        # capacity. T1498 is the network-flood half, and it is `ddos` below.
        # They are separate IDs because the response differs -- one is fixed at
        # the host, the other upstream of it.
        "technique_id": "T1499",
        "name": "Endpoint Denial of Service",
        "url": f"{_BASE}T1499/",
        "means": ("Traffic volume aimed at exhausting a single service's capacity."),
    },
    "ddos": {
        "technique_id": "T1498",
        "name": "Network Denial of Service",
        "url": f"{_BASE}T1498/",
        "means": (
            "Traffic volume from many sources aimed at exhausting a service's "
            "capacity or the bandwidth in front of it."
        ),
    },
    "port_scan": {
        "technique_id": "T1046",
        "name": "Network Service Discovery",
        "url": f"{_BASE}T1046/",
        "means": (
            "A host enumerating open ports on another host, usually "
            "reconnaissance ahead of something else."
        ),
    },
    "web_attack": {
        "technique_id": "T1190",
        "name": "Exploit Public-Facing Application",
        "url": f"{_BASE}T1190/",
        "means": (
            "Malformed input aimed at a public-facing application -- SQL "
            "injection, cross-site scripting, or a malformed protocol message."
        ),
    },
    "botnet": {
        "technique_id": "T1071",
        "name": "Application Layer Protocol",
        "url": f"{_BASE}T1071/",
        "means": (
            "A host checking in with an external command-and-control server, "
            "usually over a protocol chosen because it looks ordinary."
        ),
    },
    "infiltration": {
        "technique_id": "T1204",
        "name": "User Execution",
        "url": f"{_BASE}T1204/",
        "means": (
            "A host behaving as if it has been used as an entry point, "
            "typically after someone ran something they should not have."
        ),
    },
}


def technique_for(family: str | None) -> Technique | None:
    """The technique for an attack family, or ``None`` for an anomaly.

    ``None`` and the empty string both mean *no family*, which is the
    UNCLASSIFIED_ANOMALY case: Stage 2 has no class vocabulary, so there is
    nothing to map. That is reported as an absence rather than as a default.

    A family that is neither empty nor in the table is a bug upstream -- the
    model can only emit the vocabulary in ``app.models.ALERT_FAMILIES`` -- so it
    raises instead of returning plausible advice that would hide the bug.
    """
    if not family:
        return None
    try:
        return TECHNIQUES[family]
    except KeyError as exc:
        raise KeyError(
            f"{family!r} has no entry in TECHNIQUES. The alert vocabulary is "
            "app.models.ALERT_FAMILIES; a family outside it reaching here means "
            "something upstream invented one, and guessing a technique would "
            "hide that rather than surface it."
        ) from exc


def coverage_vocabulary() -> list[dict[str, Any]]:
    """Every technique the table knows, for the coverage heatmap's axis.

    The heatmap's rows come from here rather than from the alerts that have
    fired, so a technique with zero hits is a visible zero instead of a missing
    row. "We have never seen this" and "we cannot see this" look identical when
    the axis is built from the data.
    """
    return [{"family": family, **entry} for family, entry in TECHNIQUES.items()]
