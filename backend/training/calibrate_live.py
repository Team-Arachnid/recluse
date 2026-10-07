"""Phase 9 -- cut a local tau_anom from a shadow-mode burn-in.

The shipped `tau_anom` is the 99.5th percentile of reconstruction error on a
2017 lab's benign Thursday. Pointed at a different network, that number is a
statement about the wrong traffic: the domain shift Phase 3 already measured
between two days of the *same* lab (a 10.8-fold rise in false positives) is a
lower bound on what a real network does to it. So before a live alert may
reach the queue, the network's own traffic is scored in shadow mode
(`app.live_capture`) and the threshold is cut again, at the same percentile,
from that network's own errors. Both thresholds are kept and reported, because
the gap between them is a result in its own right.

    python -m training.calibrate_live                  # every shadow row for the serving model
    python -m training.calibrate_live --session ID     # one burn-in (repeatable)
    python -m training.calibrate_live --dry-run        # measure and print; write nothing

A job rather than an endpoint, like the drift job and the retrain: it reads a
table and writes an artifact, and the API only ever loads the result.

**What a burn-in assumes.** Every flow in it is taken as normal. That is the
operator's statement to make, not the code's: run the burn-in when the network
is doing what it usually does and nothing else, because whatever traffic is in
the window becomes "normal" to the threshold -- including an attack that
happened to be running. The report lists which hosts and services the window
was made of, so that statement can be checked afterwards.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict
from typing import Any

import numpy as np
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.inference import load_bundle
from app.live_capture import LOCAL_THRESHOLD_FILE
from app.models import ShadowScore
from training.metrics import error_histogram

logger = logging.getLogger(__name__)

REPORT_FILENAME = "phase9_live.md"
PERCENTILES = (50.0, 90.0, 99.0, 99.5, 99.9)


class CalibrationError(RuntimeError):
    """The burn-in cannot support a threshold; the message says why."""


def _utc(moment: dt.datetime) -> dt.datetime:
    """SQLite hands timestamps back naive; they were written as UTC, so say so."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=dt.UTC)


def load_shadow_rows(stage2_version: str, sessions: list[str] | None = None) -> list[ShadowScore]:
    """Shadow rows scored by the serving Stage 2 model, optionally from given runs.

    The served version is `stage1+stage2`; only the Stage 2 half decides which
    rows belong, because the threshold is a percentile of Stage 2's errors.
    """
    with session_scope() as session:
        query = select(ShadowScore).where(ShadowScore.model_version.like(f"%+{stage2_version}"))
        if sessions:
            query = query.where(ShadowScore.session_id.in_(sessions))
        return list(session.scalars(query.order_by(ShadowScore.captured_at)))


def calibrate(
    rows: list[ShadowScore],
    *,
    stage2_version: str,
    tau_dataset: float,
    dataset_percentiles: dict[str, float],
    percentile: float,
    min_flows: int,
    dataset_edges: list[float] | None = None,
) -> dict[str, Any]:
    """The local threshold, and what the dataset one would have cost here.

    `dataset_edges` are the bins of the shipped benign error histogram. The
    burn-in's errors are binned on the same edges and kept beside the threshold,
    because a live alert's risk is its headroom past the local threshold in this
    distribution (`app.risk.stage2_base`), the way a replayed one's is measured
    in the 2017 lab's -- and on shared bins the two can be read side by side.
    """
    if len(rows) < min_flows:
        raise CalibrationError(
            f"{len(rows):,} shadow-scored flow(s) for {stage2_version}; at least "
            f"{min_flows:,} are needed (IDS_LIVE_CALIBRATION_MIN_FLOWS). A percentile "
            "cut from a handful of flows describes the handful, not the network."
        )
    errors = np.array([row.stage2_error for row in rows], dtype="float64")
    local = {f"p{p:g}": float(np.percentile(errors, p)) for p in PERCENTILES}
    tau_local = float(np.percentile(errors, percentile))
    ports = Counter(f"{row.protocol}/{row.dst_port}" for row in rows)
    hosts = Counter(row.src_ip for row in rows)
    return {
        "tau_anom_local": tau_local,
        "tau_anom_dataset": tau_dataset,
        "percentile": percentile,
        "flows": len(rows),
        "computed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "stage2_version": stage2_version,
        "dataset_threshold_alert_rate": float(np.mean(errors >= tau_dataset)),
        "local_threshold_alert_rate": float(np.mean(errors >= tau_local)),
        "stage1_alert_rate": float(np.mean([row.would_alert == "KNOWN" for row in rows])),
        "capture_sources": sorted({row.capture_source for row in rows}),
        "sessions": sorted({row.session_id for row in rows}),
        "window_start": _utc(rows[0].captured_at).isoformat(timespec="seconds"),
        "window_end": _utc(rows[-1].captured_at).isoformat(timespec="seconds"),
        "local_percentiles": local,
        "dataset_percentiles": {
            key: dataset_percentiles[key] for key in local if key in dataset_percentiles
        },
        "top_services": ports.most_common(10),
        "top_sources": hosts.most_common(10),
        "error_histogram": None
        if not dataset_edges
        else asdict(error_histogram(errors, dataset_edges)),
    }


def render_report(result: dict[str, Any]) -> str:
    """The Phase 9 write-up: both thresholds, the gap, and what the window held."""
    ratio = result["tau_anom_local"] / result["tau_anom_dataset"]
    lines = [
        "# Phase 9 -- the shadow-mode burn-in",
        "",
        f"Measured {result['computed_at']} over {result['flows']:,} live flows "
        f"({result['window_start']} to {result['window_end']}), scored in shadow mode by "
        f"`{result['stage2_version']}`: every flow scored, no alert raised.",
        "",
        "## Two thresholds",
        "",
        "```",
        f"tau_anom, CICIDS2017   {result['tau_anom_dataset']:.6g}   99.5th percentile of a 2017 "
        "lab's benign Thursday",
        f"tau_anom, local        {result['tau_anom_local']:.6g}   {result['percentile']:g}th "
        "percentile of this burn-in",
        f"ratio                  {ratio:.2f}x",
        "```",
        "",
        "| Flagged on this network's own traffic | Share of the burn-in |",
        "| --- | --- |",
        "| Stage 2 at the CICIDS2017 threshold | "
        f"**{result['dataset_threshold_alert_rate']:.2%}** |",
        f"| Stage 2 at the local threshold | {result['local_threshold_alert_rate']:.2%} |",
        f"| Stage 1 at `tau_sup` | {result['stage1_alert_rate']:.2%} |",
        "",
        "The first row is the domain shift, priced: the share of this network's "
        "ordinary traffic the shipped Stage 2 threshold would have put in front of an "
        "analyst. The local threshold flags its percentile's complement by "
        "construction, which is the point -- it is a statement about this network's "
        "normal, made from this network.",
        "",
        "## Where the errors sit",
        "",
        "| Percentile | CICIDS2017 benign (validation day) | This network |",
        "| --- | --- | --- |",
    ]
    for key, value in result["local_percentiles"].items():
        dataset = result["dataset_percentiles"].get(key)
        lines.append(f"| {key} | {'--' if dataset is None else f'{dataset:.4g}'} | {value:.4g} |")
    lines += [
        "",
        "## What the window was made of",
        "",
        "A burn-in teaches the threshold that whatever it saw is normal, so the "
        "traffic it was cut from is listed here to be checked rather than assumed.",
        "",
        "| Service (protocol/destination port) | Flows |",
        "| --- | --- |",
        *[f"| `{service}` | {count:,} |" for service, count in result["top_services"]],
        "",
        "| Source host | Flows |",
        "| --- | --- |",
        *[f"| `{host}` | {count:,} |" for host, count in result["top_sources"]],
        "",
        f"Capture sources: {', '.join(f'`{s}`' for s in result['capture_sources'])}. "
        f"Burn-in runs: {len(result['sessions'])}.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", action="append", default=None, help="one burn-in run's id")
    parser.add_argument("--dry-run", action="store_true", help="measure and print; write nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    bundle = load_bundle(settings.artifacts_path)
    if not bundle.stage2_ready or bundle.stage2_version is None:
        print("calibration needs a loaded Stage 2 model", file=sys.stderr)
        return 2
    card = bundle.model_card.get("stage2") or {}
    dataset_histogram = card.get("benign_error_histogram") or {}
    dataset_percentiles = dataset_histogram.get("percentiles") or {}

    try:
        result = calibrate(
            load_shadow_rows(bundle.stage2_version, args.session),
            stage2_version=bundle.stage2_version,
            tau_dataset=float(bundle.tau_anom),
            dataset_percentiles={str(k): float(v) for k, v in dataset_percentiles.items()},
            percentile=settings.live_calibration_percentile,
            min_flows=settings.live_calibration_min_flows,
            dataset_edges=dataset_histogram.get("edges"),
        )
    except CalibrationError as exc:
        print(f"calibration refused: {exc}", file=sys.stderr)
        return 2

    report = render_report(result)
    print(report)
    if args.dry_run:
        return 0
    path = settings.artifacts_path / LOCAL_THRESHOLD_FILE
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    settings.reports_path.mkdir(parents=True, exist_ok=True)
    (settings.reports_path / REPORT_FILENAME).write_text(report, encoding="utf-8")
    print(f"\nwritten to {path}\n           {settings.reports_path / REPORT_FILENAME}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
