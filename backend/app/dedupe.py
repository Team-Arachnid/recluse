"""Phase 5 -- alert deduplication.

Key on (src_host, alert_class, floor(ts, dedupe_window_seconds)). On a hit,
increment occurrence_count and update last_seen instead of inserting.

One compromised host emitting 5,000 flows is one incident. Without this the
triage queue is unusable within thirty seconds of starting a replay.

Window length comes from IDS_DEDUPE_WINDOW_SECONDS (default 300s).

`upsert_alert` below is Part 2 of Task 6: the select-then-insert-or-update
that turns a `dedupe_key` into the one-row-per-incident behaviour the module
docstring promises.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Alert
from app.risk import severity


def dedupe_key(src_host: str, alert_class: str, timestamp: dt.datetime) -> str:
    """Build the dedupe key: source host, class, and the floored time bucket.

    Implemented in Phase 0 because it is pure, cheap to test, and the schema
    column that stores it already exists.
    """
    window = settings.dedupe_window_seconds
    epoch = int(timestamp.timestamp())
    bucket = epoch - (epoch % window)
    return f"{src_host}|{alert_class}|{bucket}"


def ensure_aware(value: dt.datetime) -> dt.datetime:
    """Treat a naive datetime as UTC rather than let it blow up a comparison.

    `app.models` documents that SQLite strips tzinfo on the round trip, so a
    row re-read in a fresh session (a later replay batch, its own
    `session_scope()`) comes back naive even though the application layer
    only ever wrote tz-aware UTC values. Comparing a naive and an aware
    datetime with `max()` raises `TypeError`, which would take a whole batch
    down over a timestamp that is only naive because SQLite does not keep
    the timezone, not because anyone actually meant local time.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def upsert_alert(session: Session, candidate: dict[str, Any]) -> tuple[Alert, bool]:
    """Insert `candidate` as a new alert, or merge it into the matching open bucket.

    `candidate` is a fully-populated prospective `Alert` as a dict of column
    values, including its `dedupe_key` -- everything `app.pipeline.ingest_batch`
    already knows about this flow except the three dedupe-bookkeeping columns
    (`occurrence_count`, `first_seen`, `last_seen`), which this function owns
    outright and computes from `candidate["detected_at"]` rather than reading
    off the dict, so there is exactly one place that decides what a fresh
    bucket's bookkeeping looks like.

    Looked up by `dedupe_key` alone -- **no time filter is added, and adding
    one would be a bug**: `dedupe_key` already encodes the floored window
    bucket, so two rows sharing a key are by definition in the same window,
    and a stale row from an older bucket necessarily has a different key
    (the bucket is baked into the string). `ix_alerts_dedupe_key_last_seen`
    exists to serve exactly this lookup.

    On a miss: insert, with `occurrence_count = 1` and
    `first_seen = last_seen = candidate["detected_at"]`. The new row is
    flushed immediately -- not left for the caller's end-of-batch flush --
    for a reason that only shows up under a second alert in the *same*
    batch sharing this exact key: `app.db.SessionLocal` is built with
    `autoflush=False`, so without an explicit flush here, a later row in
    the same uncommitted batch would run its lookup against a database that
    does not yet contain this insert and would wrongly insert a duplicate
    instead of merging into it. Flushing also means the row's id -- needed
    for the SSE event `app.pipeline.ingest_batch` publishes right after this
    call returns -- is always real by the time the caller reads it.

    On a hit: `occurrence_count += 1`, `last_seen = max(existing, candidate)`,
    and `risk_score = max(existing, candidate)` with `severity` recomputed
    from whichever score wins, so the two columns can never disagree. Keeping
    the maximum rather than the latest is deliberate: a burst of 5,000 flows
    is one incident, and the analyst should see it ranked by its worst flow,
    not by whichever one happened to arrive last. No flush is needed on this
    branch -- `dedupe_key` never changes, so a later lookup in the same batch
    finds this row by key regardless of flush state, and SQLAlchemy's
    identity map hands back this same, already-mutated object rather than a
    stale read.

    One more column moves on a hit, and only on a replay: `ground_truth_counts`
    tallies the hit's dataset label, so a bucket's ground truth describes all
    of its flows rather than only the first (`app.models.Alert` explains why
    the first alone misleads). A demo-only field; live capture carries no
    label and leaves it null.

    Every other column -- `explanation`, `narrative`, `raw_flow`, and
    everything not named above (`confidence`, `anomaly_score`,
    `mitre_technique`, `asset_criticality`, `status`, ...) -- is left exactly
    as the first occurrence set it. The explanation/narrative/raw_flow part
    is explicit in the brief: the first flow's evidence is what the Alert
    Detail drawer shows, and replacing it per flow would make the panel
    flicker through a burst while costing a write every time. The same
    reasoning extends to the rest: touching more columns on a hit only adds
    writes without anything to show for them, and `status` in particular
    must survive untouched or a repeat hit would silently undo an analyst's
    triage work on an alert they already looked at.

    Single-writer invariant: Phase 5 has exactly one producer (the replay
    task), so this select-then-insert inside one transaction is sufficient
    and there is deliberately no unique index on `dedupe_key` to lean on
    instead. If a second producer ever appears (Phase 9's live capture
    running alongside a replay, say), this needs a unique constraint on
    `dedupe_key` plus an `INSERT ... ON CONFLICT` upsert -- two producers
    racing this read-then-write can otherwise both miss and both insert.
    """
    fields = {
        key: value
        for key, value in candidate.items()
        if key not in ("occurrence_count", "first_seen", "last_seen")
    }
    detected_at = fields["detected_at"]

    existing = (
        session.execute(select(Alert).where(Alert.dedupe_key == fields["dedupe_key"]))
        .scalars()
        .first()
    )

    label = fields.get("ground_truth_label")
    if existing is None:
        if label is not None:
            fields["ground_truth_counts"] = {label: 1}
        alert = Alert(occurrence_count=1, first_seen=detected_at, last_seen=detected_at, **fields)
        session.add(alert)
        session.flush()
        return alert, True

    existing.occurrence_count += 1
    existing.last_seen = max(ensure_aware(existing.last_seen), ensure_aware(detected_at))
    existing.risk_score = max(existing.risk_score, fields["risk_score"])
    existing.severity = severity(existing.risk_score)
    if label is not None:
        # A new dict, not an in-place increment: SQLAlchemy does not see
        # mutations inside a plain JSON value, and the update would be lost.
        counts = dict(existing.ground_truth_counts or {})
        counts[label] = counts.get(label, 0) + 1
        existing.ground_truth_counts = counts
    return existing, False
