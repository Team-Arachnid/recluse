"""Phase 5 checkpoint: start a replay at 10x, read the stream, prove dedupe works.

The brief's checkpoint for this phase is: "start a replay at 10x, watch alerts
arrive over `curl -N localhost:8000/api/v1/stream`, confirm dedup is collapsing
bursts." This script is that, run against a live process so the evidence is
produced by the real serving path rather than by a test double -- and written
down rather than described, because a checkpoint nobody can re-run is a claim
rather than a measurement.

It talks to an already-running API (``make backend`` or uvicorn directly) and
writes a short report to stdout. It does not start the server itself: a
checkpoint that spawns and kills its own process proves less, because the thing
being demonstrated is that the shipped server does this.

    python scripts/phase5_checkpoint.py --seconds 30 --speed 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"


def _post(base: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _get(base: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def read_stream(base: str, seconds: float) -> tuple[list[dict], int]:
    """Read SSE frames for `seconds`, returning the alert events and heartbeats.

    Parses the wire format by hand rather than with a client library, which is
    the point: the frames have to be readable by anything that speaks SSE,
    including `curl -N`.
    """
    alerts: list[dict] = []
    heartbeats = 0
    deadline = time.monotonic() + seconds

    request = urllib.request.Request(f"{base}/stream", headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(request, timeout=seconds + 10) as response:
        event_name = None
        while time.monotonic() < deadline:
            raw = response.readline()
            if not raw:
                break
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
                if event_name == "alert":
                    alerts.append(payload)
                elif event_name == "heartbeat":
                    heartbeats += 1
    return alerts, heartbeats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--speed", type=int, default=10, choices=(1, 10, 100))
    parser.add_argument("--dataset", default="test")
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    status, health = _get(args.base, "/health")
    if status != 200:
        print(f"the API is not answering at {args.base} (health -> {status}).")
        print("start it first: make backend")
        return 2
    print(f"health: {health['status']}, model_version={health['model_version']}")

    # The stream must refuse before a source exists -- that is the documented
    # 503 and it is half of what this checkpoint demonstrates.
    status, body = _get(args.base, "/stream")
    print(f"stream before replay: {status} ({'expected 503' if status == 503 else 'UNEXPECTED'})")

    status, started = _post(
        args.base, "/replay/start", {"speed": args.speed, "dataset": args.dataset}
    )
    if status != 202:
        print(f"replay/start -> {status}: {body or started}")
        return 1
    print(f"replay started: {args.dataset} at {args.speed}x")

    try:
        alerts, heartbeats = read_stream(args.base, args.seconds)
    finally:
        stop_status, stopped = _post(args.base, "/replay/stop")
        print(f"replay stopped: {stop_status}, {stopped.get('rows_scored')} rows scored")

    print(f"\nstream: {len(alerts)} alert events, {heartbeats} heartbeats in {args.seconds:.0f}s")

    # The dedupe claim, measured: distinct alert ids versus events received.
    # One compromised host emitting thousands of flows is one row with a rising
    # occurrence_count, so events > distinct ids is the signal that collapsing
    # is happening at all.
    by_id: dict[int, int] = {}
    for event in alerts:
        by_id[event["id"]] = max(by_id.get(event["id"], 0), event["occurrence_count"])

    collapsed = {alert_id: count for alert_id, count in by_id.items() if count > 1}
    print(f"distinct alert rows: {len(by_id)}")
    print(f"rows with >1 occurrence: {len(collapsed)}")
    if collapsed:
        worst = max(collapsed.items(), key=lambda pair: pair[1])
        print(f"largest burst collapsed into one row: id {worst[0]}, x{worst[1]} occurrences")
        print(f"events received / distinct rows: {len(alerts)} / {len(by_id)}")

    status, queue = _get(args.base, "/alerts?limit=5")
    print(f"\nqueue (top 5 by risk, {status}):")
    for row in queue.get("items", []):
        family = row["family"] or "UNCLASSIFIED_ANOMALY"
        print(
            f"  risk {row['risk_score']:.4f}  {row['severity']:<8} {family:<22}"
            f" {row['src_ip']} -> {row['dst_ip']}:{row['dst_port']}  x{row['occurrence_count']}"
        )

    status, coverage = _get(args.base, "/analytics/mitre-coverage")
    fired = [row for row in coverage.get("techniques", []) if row["count"]]
    print(f"\nmitre coverage: {len(fired)} of {len(coverage.get('techniques', []))} techniques fired")
    print(f"unclassified anomalies (no technique by design): {coverage.get('unclassified_anomalies')}")

    if not alerts:
        print("\nNO ALERTS SEEN -- the checkpoint did not demonstrate anything.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
