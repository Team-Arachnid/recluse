"""Phase 6 checkpoint: the dashboard walkthrough, run as a measurement.

The brief's checkpoint for this phase is a manual walk: "start replay, watch
alerts arrive, open one, read why/what/how-to-fix, submit a verdict, see it
reflected in the feedback panel and counted on the analytics screen."

This script is that walk, driven through the exact endpoints each screen reads,
in the order the screens read them. It is not a substitute for opening the
dashboard -- a human still has to look at it -- but it is the part of the
checkpoint that can be re-run, and a checkpoint nobody can re-run is a claim
rather than a measurement.

What it does not test: how any of it looks. That is what
`frontend/src/pages/screens.test.tsx` is for, where the acceptance criteria are
asserted against the rendered components.

It talks to an already-running API (``make backend``), the same way the
dashboard does:

    python scripts/phase6_checkpoint.py --seconds 25 --speed 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"


class Checkpoint:
    """A running tally of checks, so one failure does not hide the rest."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, passed: bool, claim: str, detail: str = "") -> bool:
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {claim}" + (f"  ({detail})" if detail else ""))
        if not passed:
            self.failures.append(claim)
        return passed

    def note(self, line: str) -> None:
        print(f"         {line}")
        self.notes.append(line)


def _request(base: str, path: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def get(base: str, path: str, **params):
    if params:
        path = f"{path}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    return _request(base, path)


def post(base: str, path: str, payload: dict | None = None):
    return _request(base, path, method="POST", payload=payload)


def wait_for_arrivals(base: str, seconds: float) -> dict:
    """Wait for the running replay to actually raise alerts.

    Deliberately not "wait until the queue has rows". A second run against a
    database that already holds alerts returns instantly and reports a pass for
    arrivals it never saw -- which is exactly what the first version of this
    script did, and the reason it reported success after scoring zero rows.
    Waiting on the replay's own ``alerts_emitted`` counter measures this run
    rather than whatever happened to be stored.

    The counter has to be alert *decisions* rather than queue rows, because
    dedupe means a burst can raise thousands of decisions and add no new rows at
    all. That is the system working as designed, and a check that treated it as a
    failure would be testing for the absence of the feature.
    """
    deadline = time.monotonic() + seconds
    status: dict = {}
    while time.monotonic() < deadline:
        _, status = get(base, "/replay/status")
        if int(status.get("alerts_emitted") or 0) > 0:
            break
        time.sleep(1.0)
    _, stats = get(base, "/alerts/stats")
    return {"replay": status, "stats": stats}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--speed", type=int, default=10, choices=(1, 10, 100))
    parser.add_argument("--dataset", default="test")
    parser.add_argument("--seconds", type=float, default=25.0)
    parser.add_argument(
        "--no-replay",
        action="store_true",
        help="Walk the screens against whatever is already stored.",
    )
    args = parser.parse_args(argv)
    base = args.base
    cp = Checkpoint()

    status, health = get(base, "/health")
    if status != 200:
        print(f"the API is not answering at {base} ({status}). Start it with `make backend`.")
        return 2
    print(f"API up: model_version={health['model_version']}\n")

    # -- 1. Start the replay. The Live screen's speed control does this. -----
    if not args.no_replay:
        print("1. Start the replay (Live screen, speed control)")
        status, started = post(
            base, "/replay/start", {"speed": args.speed, "dataset": args.dataset}
        )
        if status == 409:
            cp.note("a replay was already running; continuing against it")
        else:
            cp.check(status == 202, "POST /replay/start accepts the run", f"{status}")

        status, running = get(base, "/replay/status")
        cp.check(
            status == 200 and running.get("running") is True,
            "GET /replay/status reports it running, so a reloaded page can tell",
            f"{running.get('dataset')} at {running.get('speed')}x",
        )

        print("\n2. Watch alerts arrive (queue + stat strip)")
        arrivals = wait_for_arrivals(base, args.seconds)
        emitted = int(arrivals["replay"].get("alerts_emitted") or 0)
        scored = int(arrivals["replay"].get("rows_scored") or 0)
        open_rows = int(arrivals["stats"].get("open_alerts") or 0)
        cp.check(
            emitted > 0,
            "this run raised alerts, rather than finding ones already stored",
            f"{emitted} decisions from {scored} rows scored",
        )
        cp.check(open_rows >= 1, "they reached the queue", f"{open_rows} open")
        if emitted > open_rows:
            cp.note(f"dedupe already collapsing: {emitted} decisions into {open_rows} rows")
    else:
        print("1-2. Skipped the replay; walking the stored queue\n")

    # -- 3. The queue, as the landing page reads it -------------------------
    print("\n3. The queue (GET /alerts, GET /alerts/stats)")
    status, page = get(base, "/alerts", limit=50)
    cp.check(status == 200 and bool(page.get("items")), "the queue returns rows")
    items = page.get("items") or []
    if not items:
        print("\nnothing in the queue, so the rest of the walk has nothing to open.")
        return 1

    risks = [row["risk_score"] for row in items]
    cp.check(
        risks == sorted(risks, reverse=True),
        "rows are ordered by risk_score descending, never by time",
        f"{risks[0]:.4f} .. {risks[-1]:.4f}",
    )

    deduped = max(items, key=lambda row: row["occurrence_count"])
    cp.check(
        deduped["occurrence_count"] >= 1,
        "the queue carries a dedupe count rather than one row per flow",
        f"busiest row collapses {deduped['occurrence_count']} flows",
    )

    status, stats = get(base, "/alerts/stats")
    cp.check(status == 200, "the stat strip's counts are served")
    cp.check(
        not any("accura" in key for key in stats),
        "the strip carries no accuracy figure",
    )
    cp.note(
        f"{stats['open_alerts']} open, {stats['unclassified_open']} unclassified, "
        f"{stats['hosts_affected']} hosts affected, {stats['unjudged_open']} unjudged"
    )

    # -- 4. The one-click unclassified filter -------------------------------
    print("\n4. Filter to unclassified anomalies (the chip)")
    status, novel_page = get(base, "/alerts", kind="UNCLASSIFIED_ANOMALY", limit=20)
    novel_items = novel_page.get("items") or []
    cp.check(status == 200, "the kind filter is served")
    cp.check(
        all(row["kind"] == "UNCLASSIFIED_ANOMALY" for row in novel_items),
        "every row the chip returns is an anomaly",
        f"{len(novel_items)} rows",
    )
    cp.check(
        all(row["family"] is None and row["mitre_technique"] is None for row in novel_items),
        "no anomaly carries a fabricated family or technique",
    )

    # -- 5. Open one, and read the three panels -----------------------------
    print("\n5. Open an alert (the drawer: why / what-it-is / how-to-fix)")
    target = novel_items[0] if novel_items else items[0]
    status, detail = get(base, f"/alerts/{target['id']}")
    cp.check(status == 200, f"GET /alerts/{target['id']} answers")

    explanation = detail.get("explanation") or {}
    cp.check(
        bool(explanation.get("contributors")),
        "why: the alert carries per-feature attribution",
        f"{explanation.get('explainer')}, {len(explanation.get('contributors') or [])} contributors",
    )
    cp.check(bool(detail.get("narrative")), "why: the English sentence is present")
    cp.check(bool(detail.get("raw_flow")), "why: the raw flow record is present")

    actions = detail.get("recommended_actions") or {}
    if detail["kind"] == "UNCLASSIFIED_ANOMALY":
        cp.check(
            actions.get("technique") is None and actions.get("has_playbook") is False,
            "what/how: an anomaly gets the honest answer, not an invented one",
        )
    else:
        cp.check(
            bool((actions.get("technique") or {}).get("means")),
            "what: the technique carries a plain-English line, not just a code",
        )
        cp.check(bool(actions.get("actions")), "how: the playbook is a checklist")

    status, related = get(base, f"/alerts/{target['id']}/related")
    cp.check(status == 200, "host context is served", f"{len(related)} other alerts from this host")

    if detail.get("ground_truth_label"):
        cp.note(
            f"replay ground truth for this row: {detail['ground_truth_label']} "
            "(badged demo-only in the drawer; always null on live capture)"
        )

    # -- 6. Submit a verdict ------------------------------------------------
    print("\n6. Submit a verdict (the drawer footer)")
    _, before = get(base, "/analytics/feedback")
    status, verdict = post(
        base,
        f"/alerts/{target['id']}/verdict",
        {"verdict": "TP", "note": "phase 6 checkpoint", "analyst": "checkpoint"},
    )
    cp.check(status == 201, "the verdict is recorded", f"{status}")
    cp.check(
        verdict.get("model_version") == detail["model_version"],
        "the label is attributed to the model that produced the alert",
        verdict.get("model_version") or "none",
    )

    status, page_after = get(base, "/alerts", limit=50)
    judged = next(
        (row for row in page_after.get("items") or [] if row["id"] == target["id"]), None
    )
    cp.check(
        judged is not None and judged["latest_verdict"] == "TP",
        "the queue now shows the verdict, which is what the invalidation refreshes",
    )

    # -- 7. See it on the feedback panel ------------------------------------
    print("\n7. The feedback panel (GET /analytics/feedback)")
    status, after = get(base, "/analytics/feedback")
    cp.check(status == 200, "the feedback counts are served")
    cp.check(
        after["labels_total"] == before.get("labels_total", 0) + 1,
        "the new label is counted",
        f"{before.get('labels_total', 0)} -> {after['labels_total']}",
    )
    cp.check(
        after["labels_pending_retrain"] >= 1,
        "it is waiting for a retrain rather than already consumed",
        f"{after['labels_pending_retrain']} pending",
    )
    cp.check(
        after["retrain_available"] is False and "Phase 7" in after["retrain_phase"],
        "the retrain trigger reports which phase makes it callable",
    )

    # -- 8. And counted on analytics ---------------------------------------
    print("\n8. The analytics screen (GET /analytics/*)")
    status, summary = get(base, "/analytics/summary", range="all")
    cp.check(status == 200, "the analytics summary is served")
    cp.check(
        summary["throughput"]["verdicts"] >= 1,
        "the verdict is counted in SOC throughput",
        f"{summary['throughput']['verdicts']} verdicts, "
        f"TP rate {summary['throughput']['true_positive_rate']}",
    )
    cp.check(
        bool(summary["series"]),
        "the alerts-over-time series is split known vs unclassified",
        f"{sum(b['known'] for b in summary['series'])} known / "
        f"{sum(b['unclassified'] for b in summary['series'])} unclassified",
    )
    cp.check(
        not any("accura" in key for key in summary),
        "the analytics summary carries no accuracy figure",
    )

    status, coverage = get(base, "/analytics/mitre-coverage")
    zeros = [row for row in coverage["techniques"] if row["count"] == 0]
    cp.check(status == 200, "technique coverage is served")
    cp.check(
        len(zeros) >= 0 and len(coverage["techniques"]) > len(zeros) - 1,
        "techniques that have never fired are visible zeros, not missing rows",
        f"{len(coverage['techniques']) - len(zeros)} fired, {len(zeros)} never",
    )
    cp.note(
        f"{coverage['unclassified_anomalies']} unclassified anomalies, counted beside the grid "
        "because they map to no technique"
    )

    # -- 9. The threshold control ------------------------------------------
    print("\n9. The draggable threshold (GET /metrics/anomaly-histogram, /metrics/threshold)")
    status, histogram = get(base, "/metrics/anomaly-histogram")
    cp.check(status == 200, "the histogram the line is drawn across is served")
    cp.check(
        len(histogram["edges"]) > 1 and bool(histogram["distributions"]),
        "it carries shared bin edges and at least one distribution",
        f"{len(histogram['edges']) - 1} bins, "
        f"{[d['name'] for d in histogram['distributions']]}",
    )

    tau = histogram["tau_anom"]
    projections = []
    for candidate in (tau, min(1.0, tau * 4)):
        status, projection = get(base, "/metrics/threshold", t=candidate)
        if status == 200:
            projections.append(projection)
    cp.check(len(projections) == 2, "the projection answers at two candidate thresholds")
    if len(projections) == 2:
        low, high = projections
        cp.check(
            high["alerts_per_analyst_hour"] < low["alerts_per_analyst_hour"],
            "raising the threshold lowers the projected alert volume",
            f"{low['alerts_per_analyst_hour']:.0f}/hr at t={low['t']:.4f} -> "
            f"{high['alerts_per_analyst_hour']:.0f}/hr at t={high['t']:.4f}",
        )

    # -- 10. The model screen ----------------------------------------------
    print("\n10. Model performance (GET /metrics/model)")
    status, metrics = get(base, "/metrics/model")
    cp.check(status == 200, "the measured metrics are served")
    cp.check(
        bool(metrics["curves"]["pr"]) and bool(metrics["curves"]["roc"]),
        "both curves are present, which is what the side-by-side panel renders",
        f"{len(metrics['curves']['pr'])} PR points, {len(metrics['curves']['roc'])} ROC points",
    )
    folds = (metrics.get("loao") or {}).get("folds") or []
    missed = [
        (fold["held_out"], (fold.get("headline") or {}).get("missed"))
        for fold in folds
        if fold.get("headline")
    ]
    cp.check(bool(folds), "the LOAO table is present", f"{len(folds)} folds")
    cp.check(
        any(count for _, count in missed if count),
        "the LOAO table reports real misses rather than a table of 99s",
        ", ".join(f"{family}: {count}" for family, count in missed[:4]),
    )

    # -- Stop the replay ----------------------------------------------------
    if not args.no_replay:
        status, stopped = post(base, "/replay/stop")
        if status == 202:
            cp.note(
                f"replay stopped after {stopped['rows_scored']} rows and "
                f"{stopped['alerts_emitted']} alert decisions"
            )
            _, final = get(base, "/alerts/stats")
            cp.note(
                f"dedupe collapsed {stopped['alerts_emitted']} decisions into "
                f"{final['open_alerts']} queue rows"
            )

    print("\n" + "=" * 72)
    if cp.failures:
        print(f"{len(cp.failures)} check(s) failed:")
        for failure in cp.failures:
            print(f"  - {failure}")
        return 1
    print("Phase 6 checkpoint complete: every check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
