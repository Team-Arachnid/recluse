"""Phase 7 -- the nightly drift job.

Reads the sampled flows of the last window, rebuilds the feature matrix through
the same function training used, scores PSI per feature against the persisted
reference, and stores a snapshot. Also bins the observed reconstruction errors on
the training baseline's own edges, so the drift screen can overlay the two curves
on one axis.

A job rather than an endpoint, and a job rather than an in-process timer. The
API's business is serving; a scheduler inside it is more state in the process
that must not fall over, and a thread doing a full-table scan at 3am is a thread
competing with a replay for the same event loop. Schedule it the way the
deployment schedules things:

    # cron
    0 3 * * *  cd /app/backend && python -m training.drift_job

    # docker compose, as a one-shot service with a restart policy
    make drift

Three refusals worth naming, because each one is a case where reporting
*something* would be worse than reporting nothing:

- No reference on disk: the job stops. Scoring against bins invented here would
  produce a number with no baseline behind it.
- A reference whose schema hash disagrees with the loaded bundle's: the job
  stops. Bins cut from a different feature order describe different features
  under the same names, which yields a confident, meaningless PSI.
- Fewer than ``IDS_DRIFT_MIN_ROWS`` samples in the window: the job records the
  run and scores nothing. PSI over a handful of rows is noise, and a retrain
  banner raised by noise is worse than no banner at all.

    python -m training.drift_job
    python -m training.drift_job --window-hours 168 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.drift import DriftReport, bin_shares, measure_drift
from app.metrics_store import load_metrics
from app.models import DriftFeature, DriftRun, FlowSample
from app.sampling import prune_flow_samples
from training.drift_reference import load_reference
from training.features import build_feature_matrix, load_preprocessing_bundle

logger = logging.getLogger(__name__)

PREPROCESSING_FILE = "preprocessing.pkl"

# Which persisted benign distribution the observed scores are compared against.
# The validation day's, because that is the one `tau_anom` was cut from -- the
# test day's is a second observation rather than the baseline.
BASELINE_DISTRIBUTION = "validation_benign"


class DriftJobError(RuntimeError):
    """The job cannot produce a trustworthy number and declines to produce one."""


def _as_utc(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.UTC)


def observed_window(
    session: Any, *, window_hours: int, now: dt.datetime | None = None
) -> tuple[list[FlowSample], dt.datetime, dt.datetime]:
    """The samples inside the window, with the window's actual bounds.

    The bounds returned are the observed ones rather than the requested ones --
    the first and last sample actually found. A snapshot claiming to cover
    twenty-four hours when the replay ran for four minutes would make every
    comparison between snapshots meaningless.
    """
    end = now or dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(hours=window_hours)

    rows = list(
        session.execute(
            select(FlowSample)
            .where(FlowSample.scored_at >= start, FlowSample.scored_at <= end)
            .order_by(FlowSample.scored_at, FlowSample.id)
        )
        .scalars()
        .all()
    )
    if not rows:
        return [], start, end

    return rows, (_as_utc(rows[0].scored_at) or start), (_as_utc(rows[-1].scored_at) or end)


def score_histogram(samples: list[FlowSample], edges: list[float] | None) -> dict[str, Any] | None:
    """Bin the observed reconstruction errors on the training baseline's edges.

    Shared edges or nothing. Binning the observation independently would put the
    two curves on different axes, and the whole point of the overlay is that when
    they separate the baseline has moved -- two curves on two axes separate for
    reasons that have nothing to do with drift.
    """
    if not edges or len(edges) < 2:
        return None

    scores = [float(sample.anomaly_score) for sample in samples if sample.anomaly_score is not None]
    if not scores:
        return None

    return {
        "edges": list(edges),
        "shares": bin_shares(scores, edges),
        "rows": len(scores),
        "min": min(scores),
        "max": max(scores),
    }


def replay_caveat(samples: list[FlowSample]) -> str | None:
    """Say so when the whole observation came from a dataset replay.

    This is not a disclaimer bolted on afterwards -- it is the difference between
    two readings of the same number. Pointed at a replay of the held-out day, the
    job correctly reports that most features have moved, because the temporal
    split means Friday genuinely is a different population from Tuesday and
    Wednesday: a different attack mix, different hosts, different hours. That is
    the same fact the leave-one-attack-out table is built on, seen from the other
    side.

    A reader who does not know the source is a replay would read the same number
    as "production has drifted and the model is stale". So the run says which it
    was, from the data rather than from a setting.
    """
    sources = {sample.source for sample in samples}
    if sources != {"replay"}:
        return None
    return (
        "Every sampled flow in this window came from a dataset replay, not live "
        "capture. A replay of the held-out day is a different capture day from the "
        "one the reference was cut on -- a different attack mix, different hosts, "
        "different hours -- so most features moving is the expected and correct "
        "reading, and it is the same fact the leave-one-attack-out table is built "
        "on. Read this as a working drift monitor rather than as a stale model."
    )


def run_drift_job(
    *,
    window_hours: int | None = None,
    artifacts_dir: Path | None = None,
    now: dt.datetime | None = None,
    dry_run: bool = False,
    prune: bool = True,
) -> tuple[DriftRun | None, DriftReport]:
    """Compute and store one drift snapshot. Returns the stored run and report.

    In ``dry_run`` the measurement happens and nothing is written, which is how
    the job is checked against a window before it is put on a schedule.
    """
    artifacts = artifacts_dir or settings.artifacts_path
    hours = settings.drift_window_hours if window_hours is None else window_hours

    bundle_path = artifacts / PREPROCESSING_FILE
    if not bundle_path.exists():
        raise DriftJobError(
            f"no preprocessing bundle at {bundle_path}. Without the scaler and the "
            "feature order there is no matrix to measure."
        )
    preprocessing = load_preprocessing_bundle(bundle_path)

    reference = load_reference(artifacts)
    if reference is None:
        raise DriftJobError(
            "no drift reference on disk. Run `python -m training.drift_reference` "
            "first -- bins invented here would be a baseline with nothing behind it."
        )

    if reference.get("schema_hash") != preprocessing["schema_hash"]:
        raise DriftJobError(
            "the drift reference was cut against schema hash "
            f"{reference.get('schema_hash')} but the loaded bundle is "
            f"{preprocessing['schema_hash']}. Bins from a different feature order "
            "describe different features under the same names, so this would "
            "produce a confident, meaningless PSI. Rebuild the reference."
        )

    metrics = load_metrics(artifacts)
    baseline = metrics.error_histogram(BASELINE_DISTRIBUTION) or {}
    baseline_edges = [float(edge) for edge in baseline.get("edges") or []]

    with session_scope() as session:
        samples, observed_from, observed_to = observed_window(session, window_hours=hours, now=now)

        # The version that scored the most recent sample, not the alphabetically
        # last one: version strings sort by name, and a window spanning a
        # promotion would otherwise be attributed to whichever name sorts last.
        model_versions = {sample.model_version for sample in samples}
        model_version = samples[-1].model_version if samples else "unloaded"
        mixed = (
            f"The window spans {len(model_versions)} model versions "
            f"({', '.join(sorted(model_versions))}); a promotion inside it moves the "
            "score distribution for reasons that are not drift in the traffic."
            if len(model_versions) > 1
            else None
        )

        if len(samples) < settings.drift_min_rows:
            note = (
                f"{len(samples)} sampled flow(s) in the last {hours}h, under the "
                f"{settings.drift_min_rows}-row floor. Nothing scored: PSI over a "
                "handful of rows is noise, and a retrain banner raised by noise is "
                "worse than no banner."
            )
            logger.warning(note)
            report = DriftReport(rows_observed=len(samples))
            if dry_run:
                return None, report
            run = DriftRun(
                model_version=model_version,
                observed_from=observed_from,
                observed_to=observed_to,
                rows_observed=len(samples),
                reference=str(reference.get("source") or ""),
                reference_rows=int(reference.get("rows") or 0),
                features_scored=0,
                max_psi=0.0,
                moderate_count=0,
                significant_count=0,
                retrain_recommended=False,
                score_histogram=score_histogram(samples, baseline_edges),
                notes=note,
            )
            session.add(run)
            session.flush()
            return run, report

        frame = pd.DataFrame(
            [
                {
                    key: value
                    for key, value in (sample.raw_flow or {}).items()
                    if key != "_provenance"
                }
                for sample in samples
            ]
        )
        matrix = build_feature_matrix(frame, preprocessing)

        observed = {
            feature: [float(value) for value in matrix[feature].to_numpy()]
            for feature in matrix.columns
        }
        report = measure_drift(reference["references"], observed, rows_observed=len(samples))

        counts = report.counts
        logger.info(
            "drift over %s rows: max PSI %.4f, %s moderate, %s significant",
            len(samples),
            report.max_psi,
            counts["moderate"],
            counts["significant"],
        )

        note = " ".join(part for part in (replay_caveat(samples), mixed) if part) or None

        if dry_run:
            return None, report

        run = DriftRun(
            model_version=model_version,
            observed_from=observed_from,
            observed_to=observed_to,
            rows_observed=len(samples),
            reference=str(reference.get("source") or ""),
            reference_rows=int(reference.get("rows") or 0),
            features_scored=len(report.features),
            max_psi=report.max_psi,
            moderate_count=counts["moderate"],
            significant_count=counts["significant"],
            retrain_recommended=report.retrain_recommended,
            score_histogram=score_histogram(samples, baseline_edges),
            notes=note,
        )
        session.add(run)
        session.flush()

        for entry in report.features:
            session.add(
                DriftFeature(
                    run_id=run.id,
                    feature=entry.feature,
                    psi=entry.psi,
                    band=entry.band,
                    expected=entry.expected,
                    actual=entry.actual,
                )
            )

        if prune:
            prune_flow_samples(session)

        session.flush()
        return run, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-hours", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Measure and print, store nothing.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Leave the sample table untrimmed.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    try:
        run, report = run_drift_job(
            window_hours=args.window_hours,
            dry_run=args.dry_run,
            prune=not args.no_prune,
        )
    except DriftJobError as error:
        print(f"drift job declined to run: {error}")
        return 2

    print(f"rows observed     {report.rows_observed:,}")
    print(f"features scored   {len(report.features)}")
    if report.features:
        counts = report.counts
        print(f"max PSI           {report.max_psi:.4f}")
        print(
            f"bands             {counts['stable']} stable, "
            f"{counts['moderate']} moderate, {counts['significant']} significant"
        )
        print(f"retrain advised   {report.retrain_recommended}")
        print("")
        print("WORST FEATURES")
        for entry in report.worst(10):
            print(f"  {entry.feature:<34} {entry.psi:>8.4f}  {entry.band}")
    if run is not None:
        print("")
        print(f"stored as drift run {run.id}")
    elif args.dry_run:
        print("")
        print("dry run -- nothing stored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
