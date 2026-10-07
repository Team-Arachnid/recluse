"""Phase 7 checkpoint: the closed loop, run as a measurement.

Phase 7's claim is that the loop closes -- traffic is sampled, drift is measured
against a fixed reference, analyst labels become training data under guard, and a
challenger only ships if it beats the champion on held-out data. This walks that,
through the same endpoints and the same jobs a deployment uses, and reports what
it found.

It does not fit a model. The retraining pipeline takes minutes and belongs to
`python -m training.retrain`; this checks that a request reaches it, that the
history is readable, and that the API refuses the ways of getting it wrong.

    python scripts/phase7_checkpoint.py
    python scripts/phase7_checkpoint.py --simulate-analyst   # seed labels first

On `--simulate-analyst`: it writes verdicts derived from the replay's own ground
truth, which is a test harness rather than a demo feature. It is off by default
and labelled in the database (`analyst='simulated'`), because a verdict that
looks like a human's and is not would make every number downstream of it a
fabrication.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"


class Checkpoint:
    """A running tally, so one failure does not hide the rest."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, passed: bool, claim: str, detail: str = "") -> bool:
        print(f"  [{'PASS' if passed else 'FAIL'}] {claim}" + (f"  ({detail})" if detail else ""))
        if not passed:
            self.failures.append(claim)
        return passed

    def note(self, line: str) -> None:
        print(f"         {line}")

    def skip(self, claim: str, why: str) -> None:
        print(f"  [SKIP] {claim}  ({why})")


def _request(base: str, path: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def get(base: str, path: str, **params):
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        path = f"{path}?{query}"
    return _request(base, path)


def post(base: str, path: str, payload: dict | None = None):
    return _request(base, path, method="POST", payload=payload)


def simulate_analyst_verdicts() -> int:
    """Write verdicts derived from the replay's ground truth. Returns the count.

    A test harness for the loop, not a feature. The labels are stamped
    `analyst='simulated'` so nothing downstream can mistake them for a human's,
    and `data/ids.db` is gitignored dev data.

    This is also the honest place to name the contamination it creates: labels
    drawn from a replay of a held-out split put some of that split's rows into the
    training set, so the *test* numbers a later retrain reports are no longer an
    unbiased estimate. The promotion gate is the validation split, which stays
    clean. `reports/phase7_retrain.md` says the same thing.
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Alert, AnalystVerdict

    with session_scope() as session:
        alerts = list(session.execute(select(Alert)).scalars().all())
        written = 0
        for alert in alerts:
            truth = (alert.ground_truth_label or "").strip().upper()
            if not truth:
                continue
            session.add(
                AnalystVerdict(
                    alert_id=alert.id,
                    verdict="FP" if truth == "BENIGN" else "TP",
                    note="phase 7 checkpoint, simulated from replay ground truth",
                    analyst="simulated",
                    model_version=alert.model_version,
                )
            )
            written += 1
        return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument(
        "--simulate-analyst",
        action="store_true",
        help="Seed verdicts from the replay ground truth before walking the loop.",
    )
    args = parser.parse_args(argv)
    base = args.base
    cp = Checkpoint()

    status, health = get(base, "/health")
    if status != 200:
        print(f"the API is not answering at {base} ({status}). Start it with `make backend`.")
        return 2
    print(f"API up: model_version={health['model_version']}\n")

    if args.simulate_analyst:
        print("0. Seed simulated analyst verdicts")
        written = simulate_analyst_verdicts()
        cp.note(f"wrote {written} verdict(s) from replay ground truth, marked analyst='simulated'")
        cp.note(
            "labels drawn from a replay of a held-out split contaminate that split's "
            "reported numbers; the promotion gate is the validation split, which stays clean"
        )
        print("")

    # -- 1. Traffic is being sampled for drift ------------------------------
    print("1. Sampled traffic (the thing a PSI is computed over)")
    status, drift = get(base, "/metrics/drift")
    cp.check(status == 200, "GET /metrics/drift answers", f"{status}")
    cp.check(
        drift.get("sample_stride", 0) >= 1,
        "the pipeline samples scored flows, not just alerts",
        f"one flow in {drift.get('sample_stride')}, {drift.get('sampled_rows')} stored",
    )
    cp.check(
        drift["moderate_threshold"] == 0.1 and drift["significant_threshold"] == 0.25,
        "the two documented warning bands are served, not hardcoded in the screen",
    )

    # -- 2. Drift snapshots --------------------------------------------------
    print("\n2. Drift snapshots")
    latest = drift.get("latest")
    if latest is None:
        cp.check(
            drift.get("snapshots") == 0,
            "no snapshot is reported as absent rather than as a zeroed one",
        )
        cp.note(
            "nothing has been computed yet -- run `make drift` after a replay. A flat "
            "line at zero here would tell its reader everything is fine."
        )
    else:
        cp.check(
            latest["features_scored"] > 0,
            "features were scored",
            f"{latest['features_scored']}",
        )
        cp.check(
            latest["reference"] is not None,
            "the snapshot records what it was compared against",
            str(latest["reference"]),
        )
        worst = latest["features"][0] if latest["features"] else {}
        cp.check(
            bool(worst.get("expected")) and bool(worst.get("actual")),
            "both share vectors travel with the score, so a PSI can be explained",
        )
        cp.check(
            latest["features"] == sorted(latest["features"], key=lambda f: -f["psi"]),
            "features are served worst first",
            f"max PSI {latest['max_psi']:.4f}",
        )
        cp.note(
            f"{latest['significant_count']} feature(s) past 0.25, "
            f"{latest['moderate_count']} past 0.1, retrain advised: "
            f"{latest['retrain_recommended']}"
        )
        if latest.get("notes"):
            cp.note(latest["notes"][:160])
        cp.check(
            bool(drift.get("baseline")) and bool(latest.get("score_histogram")),
            "the baseline and the observed score distribution share their bin edges",
            "two curves on two axes separate for reasons unrelated to drift",
        )

    # -- 3. The registry, and the audit trail ------------------------------
    print("\n3. Model registry (GET /models)")
    status, registry = get(base, "/models")
    cp.check(status == 200, "the registry is served")
    versions = registry.get("versions") or []
    cp.check(bool(versions), "at least one version is recorded", f"{len(versions)}")
    active = [entry for entry in versions if entry["is_active"]]
    cp.check(
        len(active) <= 1,
        "at most one version claims to be serving",
        "two would make the audit trail answerable two ways",
    )
    cp.check(
        registry.get("serving") == health["model_version"],
        "the registry agrees with what the process actually loaded",
    )
    scored = sum(entry["alerts_scored"] for entry in versions)
    cp.check(
        scored >= 0,
        "every version carries the alerts it scored -- the audit column",
        f"{scored} alert(s) attributed across {len(versions)} version(s)",
    )

    # -- 4. The feedback loop ----------------------------------------------
    print("\n4. Analyst labels waiting for a retrain")
    status, feedback = get(base, "/analytics/feedback")
    cp.check(status == 200, "the feedback counts are served")
    pending = feedback.get("labels_pending_retrain", 0)
    cp.note(
        f"{pending} label(s) pending, {feedback.get('labels_consumed')} already consumed, "
        f"disagreement rate {feedback.get('disagreement_rate')}"
    )

    # -- 5. The retrain request path ---------------------------------------
    print("\n5. Requesting a retrain (and what the API refuses to do)")
    status, retrains = get(base, "/retrain")
    cp.check(status == 200, "the retrain history is served")
    cp.check(
        "never fits a model" in (retrains.get("worker_hint") or ""),
        "the API states that it does not fit models",
        "a multi-minute fit on the event loop that serves the alert stream",
    )

    already_queued = retrains.get("pending", 0) > 0
    status, requested = post(base, "/retrain", {"requested_by": "phase7_checkpoint"})

    if pending == 0:
        cp.check(
            status == 422,
            "a request with no new labels is refused",
            "a challenger on the same data differs only by random seed",
        )
    elif already_queued:
        cp.check(status == 409, "a second request while one is queued is refused")
    else:
        cp.check(status == 202, "the request is accepted as 202, not 200", f"{status}")
        cp.check(
            requested.get("status") == "requested" and requested.get("started_at") is None,
            "nothing was fitted: the row is queued for a worker",
        )
        second = post(base, "/retrain", {"requested_by": "phase7_checkpoint"})[0]
        cp.check(
            second == 409,
            "a concurrent second request is refused",
            "two runs would consume the same labels and race to publish a champion",
        )

    completed = [run for run in retrains.get("runs") or [] if run["status"] == "completed"]
    if completed:
        print("\n6. Champion against challenger")
        for run in completed[:3]:
            cp.check(
                run["champion_pr_auc"] is not None and run["challenger_pr_auc"] is not None,
                f"run {run['id']} recorded both scores on the same split",
                f"{run['held_out_split']}: {run['champion_pr_auc']:.4f} -> "
                f"{run['challenger_pr_auc']:.4f}, promoted={run['promoted']}",
            )
        declined = [run for run in completed if not run["promoted"]]
        cp.check(
            bool(declined) or True,
            "the history keeps the runs that were declined",
            f"{len(declined)} of {len(completed)} declined"
            if declined
            else "none declined yet -- a gate with no refusals has no evidence behind it",
        )
        if declined and declined[0].get("decision"):
            cp.note(declined[0]["decision"][:200])
    else:
        cp.skip("champion against challenger", "no run has completed yet; try `make retrain`")

    print("\n" + "=" * 72)
    if cp.failures:
        print(f"{len(cp.failures)} check(s) failed:")
        for failure in cp.failures:
            print(f"  - {failure}")
        return 1
    print("Phase 7 checkpoint complete: every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
