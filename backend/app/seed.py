"""Phase 8 -- ``make seed``: a demo database built by the real pipeline from real flows.

The dashboard should not open empty, and nothing on it may be invented. So the
seed is a replay that has already happened:

1. **The flows are real.** ``backend/release/demo_flows.parquet`` is a committed
   sample of the two held-out CICIDS2017 days -- every 40th flow in capture
   order, plus every flow of the three families too rare to survive that
   (infiltration, the web attacks, the botnet) -- carrying the dataset's own
   labels. No row is synthesised or edited. ``python -m app.seed sample``
   rebuilds it from ``data/processed``, and ``demo_flows.json`` beside it
   records exactly what was kept.
2. **The verdicts are the models'.** Every flow goes through
   ``bundle.score_batch`` and ``app.pipeline.ingest_batch``, the two calls a
   live replay makes, so every alert is explained, deduplicated and ranked by
   the code that will rank tomorrow's.
3. **The clock is the only thing the seed supplies.** The CSV release has no
   timestamps (``app/replay.py`` explains), so a replay synthesises time
   anyway; the seed spreads its batches evenly over the hours before it ran,
   so the trend charts have a day to draw -- and every alert's provenance says
   that its ``detected_at`` came from the seed (``app.topology.CLOCKS``).
4. **Nothing an analyst says is simulated.** The seed writes no verdicts. The
   feedback screen opens on its empty state until somebody judges an alert.

It finishes by running the drift job over what it scored, so the drift screen
opens on a measurement against the training reference rather than a
placeholder.

    python -m app.seed                  # seed an empty database
    python -m app.seed --if-empty       # the container entrypoint: skip if seeded
    python -m app.seed --reset          # delete alerts, verdicts and samples first
    python -m app.seed sample           # rebuild the committed demo sample
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, inspect, select

from app.config import settings
from app.release import DATASET, DEMO_FLOWS_CARD, DEMO_FLOWS_FILE

# Rows per scored batch: the replay's own, so the seed hands the model exactly
# the matrices a replay would.
from app.replay import BATCH_ROWS

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 24

# A denser drift sample than production's one-in-500. The seed scores tens of
# thousands of flows, not a million, and at the production stride the drift job
# would rightly refuse the window as too thin to measure. The stride is
# recorded on the sample rows' count, not hidden: the drift screen reports how
# many rows it read.
SEED_SAMPLE_STRIDE = 25

# The committed sample: every Nth flow of each held-out day, plus every flow of
# the families too rare to survive a 1-in-N cut. Thursday before Friday,
# capture order within each, so the replay walks the week forwards.
SAMPLE_EVERY = 40
SAMPLE_SPLITS: tuple[tuple[str, str], ...] = (("val", "Thursday"), ("test", "Friday"))
RARE_FAMILIES: tuple[str, ...] = ("infiltration", "web_attack", "botnet")

# Tables the seed writes, children first so foreign keys never block a reset.
# The model registry and retrain history are left alone: they are the audit
# trail, and a demo reset is not a reason to forget which models served.
_RESET_ORDER = ("drift_features", "drift_runs", "analyst_verdicts", "alerts", "flow_samples")


class SeedError(RuntimeError):
    """The seed cannot run as asked; the message says what to do instead."""


@dataclass
class SeedSummary:
    dataset: str
    rows: int
    batches: int
    first_at: dt.datetime
    last_at: dt.datetime
    model_version: str
    alerting_flows: int = 0
    queue_rows: int = 0
    by_class: Counter[str] = field(default_factory=Counter)
    novel_truth: Counter[str] = field(default_factory=Counter)
    drift: str = "not run"

    def render(self) -> str:
        lines = [
            f"seeded {self.rows:,} real flows from {self.dataset!r} in {self.batches} batches,",
            f"  stamped {self.first_at:%Y-%m-%d %H:%M} -> {self.last_at:%Y-%m-%d %H:%M} UTC",
            f"model    {self.model_version}",
            f"alerts   {self.queue_rows:,} queue rows from {self.alerting_flows:,} alerting flows",
        ]
        for name, total in self.by_class.most_common():
            lines.append(f"  {name:<24} {total:>6,}")
        if self.novel_truth:
            truth = ", ".join(f"{label} {n:,}" for label, n in self.novel_truth.most_common())
            lines.append(f"  ground truth behind the unclassified anomalies: {truth}")
        lines.append(f"drift    {self.drift}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The committed sample
# ---------------------------------------------------------------------------


def build_sample(
    processed_dir: Path, release_dir: Path, every: int = SAMPLE_EVERY
) -> dict[str, Any]:
    """Write ``demo_flows.parquet`` and its card from the processed held-out days."""
    import numpy as np
    import pandas as pd

    from training.features import LABEL_COLUMN
    from training.labels import map_labels

    frames = []
    splits: dict[str, Any] = {}
    for split, day in SAMPLE_SPLITS:
        path = processed_dir / f"{split}.parquet"
        if not path.exists():
            raise SeedError(f"{path} is missing; run the Phase 1 pipeline (`make data`) first.")
        frame = pd.read_parquet(path)
        families = map_labels(frame[LABEL_COLUMN]).astype(str)
        keep = (np.arange(len(frame)) % every == 0) | families.isin(RARE_FAMILIES).to_numpy()
        kept = frame.loc[keep].reset_index(drop=True)
        frames.append(kept)
        splits[split] = {
            "day": day,
            "source_rows": len(frame),
            "rows": len(kept),
            "labels": {str(k): int(v) for k, v in kept[LABEL_COLUMN].value_counts().items()},
        }

    sample = pd.concat(frames, ignore_index=True)
    release_dir.mkdir(parents=True, exist_ok=True)
    sample.to_parquet(release_dir / DEMO_FLOWS_FILE, compression="zstd", index=False)

    card = {
        "description": (
            "Real CICIDS2017 flows from the two held-out days, for `make seed` and the "
            "demo replay. No row is synthesised or edited; the label column is the "
            "dataset's own."
        ),
        "selection": (
            f"Every {every}th flow of each held-out day in capture order, plus every flow "
            f"of the families too rare to survive that cut ({', '.join(RARE_FAMILIES)}), "
            "so the demo contains them. Thursday first, then Friday."
        ),
        "rare_families_oversampled": list(RARE_FAMILIES),
        "never_trained_on": (
            "Only the validation (Thursday) and test (Friday) days are sampled. Neither "
            "fitted any weights; Thursday chose the thresholds, so its alert rate is the "
            "calibrated one, and Friday is the held-out day the README's numbers describe."
        ),
        "rows": len(sample),
        "splits": splits,
        "columns": list(sample.columns),
        "built_from": [f"data/processed/{split}.parquet" for split, _ in SAMPLE_SPLITS],
        "built_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dataset": DATASET,
    }
    (release_dir / DEMO_FLOWS_CARD).write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return card


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def existing_rows() -> dict[str, int]:
    """Rows already present in the tables the seed would write."""
    from app.db import engine, session_scope
    from app.models import Alert, AnalystVerdict, DriftRun, FlowSample

    if not inspect(engine).has_table("alerts"):
        raise SeedError("the database has no schema yet; run `make migrate` first.")
    with session_scope() as session:
        return {
            "alerts": session.scalar(select(func.count()).select_from(Alert)) or 0,
            "verdicts": session.scalar(select(func.count()).select_from(AnalystVerdict)) or 0,
            "flow samples": session.scalar(select(func.count()).select_from(FlowSample)) or 0,
            "drift runs": session.scalar(select(func.count()).select_from(DriftRun)) or 0,
        }


def reset_tables() -> None:
    from app.db import Base, session_scope

    with session_scope() as session:
        for name in _RESET_ORDER:
            session.execute(delete(Base.metadata.tables[name]))


def seed_database(
    *,
    bundle: Any,
    flows: list[dict[str, Any]],
    labels: list[str | None],
    dataset: str,
    hours: float = DEFAULT_HOURS,
    now: dt.datetime | None = None,
    stride: int = SEED_SAMPLE_STRIDE,
    batch_rows: int = BATCH_ROWS,
) -> SeedSummary:
    """Score `flows` through the real pipeline, batch by batch, across `hours`.

    Batch ``k`` of ``n`` is stamped ``now - hours + hours * (k + 1) / n``: the
    last lands on ``now``, so the queue's "last hour" figure describes the end
    of the seeded day rather than nothing.
    """
    from app.db import session_scope
    from app.pipeline import ingest_batch

    if not flows:
        raise SeedError(f"{dataset!r} has no rows to seed from.")
    now = now or dt.datetime.now(dt.UTC)
    span = dt.timedelta(hours=hours)
    batches = math.ceil(len(flows) / batch_rows)
    summary = SeedSummary(
        dataset=dataset,
        rows=len(flows),
        batches=batches,
        first_at=now - span + span / batches,
        last_at=now,
        model_version=bundle.version,
    )

    queue_ids: set[int] = set()
    for k, offset in enumerate(range(0, len(flows), batch_rows)):
        batch = flows[offset : offset + batch_rows]
        truth = labels[offset : offset + batch_rows]
        detected_at = now - span + span * (k + 1) / batches
        decisions = bundle.score_batch(batch)
        with session_scope() as session:
            alerts = ingest_batch(
                session,
                flows=batch,
                decisions=decisions,
                bundle=bundle,
                detected_at=detected_at,
                source="replay",
                ground_truth=truth,
                start_index=offset,
                sample_stride=stride,
                clock="seed_clock",
            )
            for alert in alerts:
                summary.alerting_flows += 1
                if alert.id not in queue_ids:
                    queue_ids.add(alert.id)
                    summary.by_class[alert.family or alert.kind] += 1
                    if alert.kind == "UNCLASSIFIED_ANOMALY":
                        summary.novel_truth[alert.ground_truth_label or "unlabelled"] += 1
    summary.queue_rows = len(queue_ids)
    return summary


def run_drift(hours: float) -> str:
    """Measure drift over the seeded window, as the nightly job would."""
    from training.drift_job import DriftJobError, run_drift_job

    try:
        run, report = run_drift_job(window_hours=math.ceil(hours) + 1, dry_run=False, prune=True)
    except DriftJobError as exc:
        return f"declined: {exc}"
    if not report.features:
        return f"{report.rows_observed:,} rows observed; too few to score"
    counts = report.counts
    stored = f"run {run.id}: " if run is not None else ""
    return (
        f"{stored}{report.rows_observed:,} sampled rows, {len(report.features)} features, "
        f"{counts['significant']} significant, max PSI {report.max_psi:.3f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed",
        description="Seed the demo database through the real pipeline, from real flows.",
    )
    parser.add_argument("command", nargs="?", default="run", choices=("run", "sample"))
    parser.add_argument(
        "--dataset",
        default="demo",
        help="demo (the committed sample, the default), or test / val from data/processed",
    )
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS)
    parser.add_argument("--rows", type=int, default=None, help="seed only the first N flows")
    parser.add_argument("--reset", action="store_true", help="delete existing demo data first")
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="exit quietly when the database already holds alerts (container start)",
    )
    parser.add_argument("--every", type=int, default=SAMPLE_EVERY, help="sample: keep every Nth")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(name)s | %(message)s")

    try:
        if args.command == "sample":
            card = build_sample(settings.data_path / "processed", settings.release_path, args.every)
            print(f"wrote {card['rows']:,} flows to {settings.release_path / DEMO_FLOWS_FILE}")
            for split, entry in card["splits"].items():
                kept, total = entry["rows"], entry["source_rows"]
                print(f"  {split:<5} {entry['day']:<9} {kept:>7,} of {total:,}")
            return 0

        present = existing_rows()
        if any(present.values()):
            held = ", ".join(f"{n:,} {name}" for name, n in present.items() if n)
            if args.if_empty:
                print(f"seed skipped: the database already holds {held}")
                return 0
            if not args.reset:
                raise SeedError(
                    f"the database already holds {held}. The seed does not mix a demo "
                    "into existing data; `python -m app.seed --reset` deletes alerts, "
                    "verdicts, flow samples and drift runs first (the model registry and "
                    "retrain history are kept)."
                )
            reset_tables()
            print(f"reset: deleted {held}")

        from app.inference import load_bundle
        from app.replay import UnknownDataset, load_replay_rows

        bundle = load_bundle(settings.artifacts_path)
        if not bundle.is_loaded:
            raise SeedError(
                f"no model is serving from {settings.artifacts_path}. `make models` installs "
                "the committed release; training your own works too."
            )
        try:
            flows, labels = load_replay_rows(args.dataset)
        except UnknownDataset as exc:
            raise SeedError(str(exc)) from exc
        if args.rows is not None:
            flows, labels = flows[: args.rows], labels[: args.rows]

        summary = seed_database(
            bundle=bundle, flows=flows, labels=labels, dataset=args.dataset, hours=args.hours
        )
        summary.drift = run_drift(args.hours)
        print(summary.render())
        return 0
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
