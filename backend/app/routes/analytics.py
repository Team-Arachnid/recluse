"""Aggregate analytics for whoever is not working the queue.

No accuracy hero tile here either. If this screen needs one big number it is
alerts per analyst hour or the unclassified-anomaly rate.

Everything on this screen is computed from the `alerts` and
`analyst_verdicts` tables -- it describes what this deployment has actually
seen, which is the opposite of `/metrics/model`, where every number comes from
the offline evaluation and must never move when a replay runs.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.mitre import coverage_vocabulary
from app.models import Alert, AnalystVerdict
from app.schemas import (
    AnalyticsSummary,
    CountedPair,
    MitreCoverage,
    MitreCoverageRow,
    ThroughputStats,
    TimeBucket,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Range name -> (lookback, bucket width). `all` has no lookback; its bucket is
# a day, because a chart of every alert ever seen is read by shape rather than
# by hour.
RANGES: dict[str, tuple[dt.timedelta | None, dt.timedelta]] = {
    "24h": (dt.timedelta(hours=24), dt.timedelta(hours=1)),
    "7d": (dt.timedelta(days=7), dt.timedelta(days=1)),
    "30d": (dt.timedelta(days=30), dt.timedelta(days=1)),
    "all": (None, dt.timedelta(days=1)),
}

TOP_N = 10


def _floor(moment: dt.datetime, width: dt.timedelta) -> dt.datetime:
    """Floor a timestamp into its bucket.

    Done in Python rather than in SQL on purpose: the date functions differ
    between SQLite and Postgres, and this project's whole persistence story is
    that pointing `IDS_DATABASE_URL` at Postgres is a configuration change and
    nothing more. A `strftime` here would quietly break that.
    """
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    seconds = int((moment - epoch).total_seconds())
    step = int(width.total_seconds())
    return epoch + dt.timedelta(seconds=seconds - (seconds % step))


def _top(session: Session, column, since: dt.datetime | None) -> list[CountedPair]:
    """The `TOP_N` most frequent values of one column, with counts."""
    statement = select(column, func.count()).group_by(column)
    if since is not None:
        statement = statement.where(Alert.detected_at >= since)
    rows = session.execute(statement.order_by(func.count().desc()).limit(TOP_N)).all()
    return [
        CountedPair(value=str(value), count=int(count))
        for value, count in rows
        if value is not None
    ]


@router.get(
    "/summary",
    response_model=AnalyticsSummary,
    summary="Alerts over time, family mix, top hosts and sources, SOC throughput",
    responses={422: {"description": "An unrecognised range."}},
)
def analytics_summary(
    session: Session = Depends(get_session),
    range: str = Query("24h", pattern="^(24h|7d|30d|all)$", description="Time range"),
) -> AnalyticsSummary:
    """Everything on the analytics screen except the heatmap.

    The alerts-over-time series is stacked by known family versus
    `UNCLASSIFIED_ANOMALY`, because that split is the novel-detection headline
    -- the unclassified line is the one a reviewer asks about if it spikes, and
    burying it inside a total would hide exactly the claim this project is
    making.

    Buckets with no alerts are emitted as zeros rather than omitted. A chart
    that skips empty hours draws a continuous line through a gap and makes an
    outage look like steady traffic.
    """
    lookback, width = RANGES[range]
    now = dt.datetime.now(dt.UTC)
    since = None if lookback is None else now - lookback

    rows = session.execute(
        select(Alert.detected_at, Alert.kind, Alert.family).where(
            *([] if since is None else [Alert.detected_at >= since])
        )
    ).all()

    buckets: dict[dt.datetime, dict[str, int]] = {}
    families: dict[str, int] = {}
    for detected_at, kind, family in rows:
        slot = buckets.setdefault(_floor(detected_at, width), {"known": 0, "unclassified": 0})
        if kind == "UNCLASSIFIED_ANOMALY":
            slot["unclassified"] += 1
        else:
            slot["known"] += 1
        if family is not None:
            families[family] = families.get(family, 0) + 1

    # Fill the gaps, so the series is continuous over the requested range.
    if buckets:
        start = _floor(since, width) if since is not None else min(buckets)
        end = _floor(now, width)
        cursor = start
        while cursor <= end:
            buckets.setdefault(cursor, {"known": 0, "unclassified": 0})
            cursor += width

    series = [
        TimeBucket(
            bucket=moment,
            known=counts["known"],
            unclassified=counts["unclassified"],
        )
        for moment, counts in sorted(buckets.items())
    ]

    total = len(rows)
    unclassified = sum(bucket.unclassified for bucket in series)

    return AnalyticsSummary(
        range=range,
        generated_at=now,
        total_alerts=total,
        unclassified_alerts=unclassified,
        unclassified_rate=(unclassified / total) if total else 0.0,
        series=series,
        families=[
            CountedPair(value=name, count=count)
            for name, count in sorted(families.items(), key=lambda pair: -pair[1])
        ],
        top_destination_hosts=_top(session, Alert.dst_ip, since),
        top_destination_ports=_top(session, Alert.dst_port, since),
        top_source_hosts=_top(session, Alert.src_ip, since),
        throughput=_throughput(session, since),
    )


def _throughput(session: Session, since: dt.datetime | None) -> ThroughputStats:
    """Opened vs resolved, time-to-verdict, and the TP/FP split.

    This is the panel that argues for the project's existence: it should trend
    the right way as the feedback loop does its job, and a flat line here is a
    finding rather than a blank chart.
    """
    scope = [] if since is None else [Alert.detected_at >= since]

    opened = session.execute(select(func.count()).select_from(Alert).where(*scope)).scalar_one()
    resolved = session.execute(
        select(func.count())
        .select_from(Alert)
        .where(Alert.status.in_(("closed", "dismissed")), *scope)
    ).scalar_one()

    verdict_rows = session.execute(
        select(AnalystVerdict.verdict, AnalystVerdict.created_at, Alert.detected_at)
        .join(Alert, Alert.id == AnalystVerdict.alert_id)
        .where(*scope)
    ).all()

    counts: dict[str, int] = {}
    deltas: list[float] = []
    for verdict, created_at, detected_at in verdict_rows:
        counts[verdict] = counts.get(verdict, 0) + 1
        if created_at is None or detected_at is None:
            continue
        # SQLite strips tzinfo on the round trip; both sides are written as
        # tz-aware UTC, so re-attaching UTC is a restoration and not a guess.
        created = created_at if created_at.tzinfo else created_at.replace(tzinfo=dt.UTC)
        detected = detected_at if detected_at.tzinfo else detected_at.replace(tzinfo=dt.UTC)
        deltas.append((created - detected).total_seconds())

    judged = sum(counts.values())
    true_positives = counts.get("TP", 0)
    false_positives = counts.get("FP", 0)
    decided = true_positives + false_positives

    return ThroughputStats(
        opened=int(opened),
        resolved=int(resolved),
        verdicts=judged,
        true_positives=true_positives,
        false_positives=false_positives,
        unsure=counts.get("UNSURE", 0),
        # Of the alerts an analyst actually decided -- UNSURE is excluded from
        # the denominator because it is not a judgement that the alert was
        # right or wrong, and folding it in would drag the rate toward zero
        # every time someone was honest about not knowing.
        true_positive_rate=(true_positives / decided) if decided else None,
        mean_seconds_to_verdict=(sum(deltas) / len(deltas)) if deltas else None,
    )


@router.get(
    "/mitre-coverage",
    response_model=MitreCoverage,
    summary="Technique counts for the coverage heatmap",
)
def mitre_coverage(session: Session = Depends(get_session)) -> MitreCoverage:
    """One row per technique the lookup knows, with how often it has fired.

    **The axis comes from `app.mitre.coverage_vocabulary()`, not from the
    alerts that have fired**, so a technique with no hits is a visible zero
    rather than a missing row. "We have never seen this" and "we cannot see
    this" look identical when the axis is built from the data, and telling
    them apart is the entire point of a coverage heatmap.

    `UNCLASSIFIED_ANOMALY` maps to no technique by design, so it is reported
    alongside the table as its own count rather than folded into it. Hiding it
    would understate exactly the detections this project is proudest of;
    giving it a technique id would be a fabrication.
    """
    counts = dict(
        session.execute(
            select(Alert.mitre_technique, func.count())
            .where(Alert.mitre_technique.is_not(None))
            .group_by(Alert.mitre_technique)
        ).all()
    )

    unclassified = session.execute(
        select(func.count()).select_from(Alert).where(Alert.kind == "UNCLASSIFIED_ANOMALY")
    ).scalar_one()

    return MitreCoverage(
        techniques=[
            MitreCoverageRow(
                family=entry["family"],
                technique_id=entry["technique_id"],
                name=entry["name"],
                url=entry["url"],
                means=entry["means"],
                count=int(counts.get(entry["technique_id"], 0)),
            )
            for entry in coverage_vocabulary()
        ],
        unclassified_anomalies=int(unclassified),
    )
