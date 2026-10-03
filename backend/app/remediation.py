"""Phase 5 -- family to response playbook.

A static, reviewed lookup table, not a generative one. A fixed playbook is
something a SOC can trust; advice improvised per alert has to be re-verified
every time, which defeats the purpose of having it. Nothing here is produced by
a model, and nothing here varies with the alert.

Unclassified anomalies get the honest entry: no playbook exists yet, route for
manual investigation. Never invent a fix for something the system does not
actually recognise -- a wrong playbook does more damage than an admitted gap,
because a SOC acts on it. The entry is *populated* rather than empty, though: an
empty response renders as a missing panel, which reads as a bug, and the right
answer is a panel that says plainly that there is no playbook and why.

Every action here is advice to a person. The system has no containment path and
will not grow one, so no action is phrased as something the software does by
itself -- see the "Explicitly absent" section of docs/API-Reference.md.
"""

from __future__ import annotations

from typing import TypedDict

from app.mitre import technique_for


class Playbook(TypedDict):
    """One row of the lookup, as the Alert Detail screen consumes it."""

    family: str | None
    technique_id: str | None
    # False only for the unclassified-anomaly case, so the UI can render the
    # honest panel differently from a real playbook without string-matching.
    has_playbook: bool
    summary: str
    actions: list[str]


PLAYBOOKS: dict[str, Playbook] = {
    "brute_force": {
        "family": "brute_force",
        "technique_id": "T1110",
        "has_playbook": True,
        "summary": "Credential guessing against one service.",
        "actions": [
            "Lock or rotate the credentials of the targeted account.",
            "Enforce MFA on the service if it is not already required.",
            "Rate-limit authentication attempts, or geo-fence the service if "
            "its users are regional.",
            "Check whether any attempt succeeded before the alerts started.",
        ],
    },
    "dos": {
        "family": "dos",
        "technique_id": "T1499",
        "has_playbook": True,
        "summary": "Volume aimed at exhausting one service.",
        "actions": [
            "Enable upstream rate-limiting or scrubbing for the targeted service.",
            "Fail the target over if it is behind a load balancer.",
            "Ask the network team to consider blocking the source range at the edge.",
            "Capture resource metrics for the target while it is happening -- "
            "they are the evidence of impact.",
        ],
    },
    "ddos": {
        "family": "ddos",
        "technique_id": "T1498",
        "has_playbook": True,
        "summary": "Distributed volume aimed at exhausting a service or its link.",
        "actions": [
            "Engage upstream scrubbing -- a distributed flood is not containable at the host.",
            "Fail the target over and confirm the failover path is not behind "
            "the same saturated link.",
            "Collect the source address distribution before it changes; it is "
            "what the provider will ask for.",
            "Ask the network team to consider blocking the worst source ranges at the edge.",
        ],
    },
    "port_scan": {
        "family": "port_scan",
        "technique_id": "T1046",
        "has_playbook": True,
        "summary": "Reconnaissance: a host enumerating another's open ports.",
        "actions": [
            "Review firewall rules on the scanned host and confirm nothing is "
            "exposed that should not be.",
            "Watch the source for follow-up activity -- a scan is usually the "
            "first half of something.",
            "Check whether the scanned ports correspond to anything that is actually listening.",
        ],
    },
    "web_attack": {
        "family": "web_attack",
        "technique_id": "T1190",
        "has_playbook": True,
        "summary": "Malformed input aimed at a public-facing application.",
        "actions": [
            "Patch the endpoint, or add a WAF rule for the specific pattern if "
            "a patch is not available yet.",
            "Rotate the credentials the application uses to reach its database.",
            "Review recent writes to the database for anything the request could have caused.",
            "Pull the application logs for the same window -- the payload is "
            "there and not in the flow record.",
        ],
    },
    "botnet": {
        "family": "botnet",
        "technique_id": "T1071",
        "has_playbook": True,
        "summary": "A host checking in with external command-and-control.",
        "actions": [
            "Isolate the host from the network.",
            "Image it before wiping -- the image is the only evidence of how it got there.",
            "Rotate any credentials that were stored on or used from it.",
            "Look for the same beacon destination from other hosts.",
        ],
    },
    "infiltration": {
        "family": "infiltration",
        "technique_id": "T1204",
        "has_playbook": True,
        "summary": "A host behaving as if it has been used as an entry point.",
        "actions": [
            "Isolate the host.",
            "Check for lateral movement from it, and review what it could reach.",
            "Identify what was executed and when, from endpoint logs rather "
            "than from the flow record.",
            "Rotate credentials that lived on it.",
        ],
    },
}


# The honest entry. Separate from PLAYBOOKS because it is keyed on the absence
# of a family rather than on a family, and because the tests that assert every
# family has a playbook should not accidentally be satisfied by this one.
NO_PLAYBOOK: Playbook = {
    "family": None,
    "technique_id": None,
    "has_playbook": False,
    "summary": (
        "Does not match a known technique -- which is exactly what Stage 2 exists to catch."
    ),
    "actions": [
        "No playbook exists for this yet, and inventing one would be worse than saying so.",
        "Route this for manual investigation.",
        "Start from the features the model could not reconstruct -- they are "
        "listed in the explanation, and they are why this flow looked unlike "
        "normal traffic.",
        "If this turns out to be a known attack, the family belongs in the "
        "Stage 1 training set and this table; if it turns out to be benign, "
        "mark it FP so the Phase 7 retrain learns from it.",
    ],
}


def playbook_for(family: str | None) -> Playbook:
    """The response playbook for an attack family, or the honest no-playbook one.

    ``None`` and the empty string are the UNCLASSIFIED_ANOMALY case. Unlike
    ``mitre.technique_for``, this returns a populated entry rather than ``None``:
    there is no technique to name, but there is still something to tell the
    analyst, and an empty panel would read as a bug rather than as an admission.

    A family outside ``app.models.ALERT_FAMILIES`` raises, for the same reason
    it does in ``mitre.technique_for``.
    """
    if not family:
        return NO_PLAYBOOK
    try:
        return PLAYBOOKS[family]
    except KeyError as exc:
        raise KeyError(
            f"{family!r} has no entry in PLAYBOOKS. The alert vocabulary is "
            "app.models.ALERT_FAMILIES; a family outside it reaching here means "
            "something upstream invented one, and improvising a response for it "
            "is the failure mode this table exists to prevent."
        ) from exc


def advice_for(family: str | None) -> dict[str, object]:
    """Playbook plus technique, which is how an alert stores and serves them.

    One call rather than two because the Alert Detail screen's "what this likely
    is" and "how to fix it" panels are a single story, and they must never
    disagree about whether a technique exists.
    """
    technique = technique_for(family)
    playbook = playbook_for(family)
    return {
        "technique": technique,
        "has_playbook": playbook["has_playbook"],
        "summary": playbook["summary"],
        "actions": list(playbook["actions"]),
    }
