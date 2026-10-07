"""Phase 7 -- keeping a sample of scored traffic, so drift can be measured.

The ``alerts`` table cannot answer a drift question. An alert is a row that
crossed a threshold, so a PSI computed from stored alerts measures whether the
*alerts* look unusual -- which is a different question, and a much less useful
one. If a new backup job shifts the shape of ordinary traffic, the alerting
population may not move at all until the shift is large enough to start firing,
which is exactly too late.

So the pipeline keeps a small, bounded, systematic sample of every flow it
scores. Two decisions worth stating:

**Systematic, not random.** Every ``IDS_DRIFT_SAMPLE_STRIDE``-th row by position
in the stream. A replay therefore produces the same sample twice, and a drift
number can be reproduced -- a drift figure nobody can re-derive is an anecdote,
and this project's whole argument is the difference between those two things.
The flows arrive in capture order rather than sorted by any feature, so taking
every Nth is unbiased with respect to the distributions being measured.

**Raw flows, not feature vectors.** The sample stores the record as it arrived
and the nightly job rebuilds the matrix through
``training.features.build_feature_matrix`` -- the same function training used.
Persisting a pre-built vector would freeze today's feature order into the table,
and a retrain that changed it would silently compare the new reference against
old columns.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FlowSample

logger = logging.getLogger(__name__)


def sample_scored_flows(
    session: Session,
    *,
    flows: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    scored_at: datetime,
    source: str,
    start_index: int = 0,
    stride: int | None = None,
) -> int:
    """Persist every ``stride``-th flow of this batch. Returns how many it kept.

    Called for the whole batch, including the rows that raised no alert. Those
    rows are the point: they are the benign baseline, and they are the majority.

    This adds rows to the caller's transaction and does not commit -- the same
    contract ``ingest_batch`` works under, so a batch stays one commit.
    """
    step = settings.drift_sample_stride if stride is None else stride
    if step < 1:
        return 0

    kept = 0
    for offset, flow in enumerate(flows):
        position = start_index + offset
        if position % step != 0:
            continue

        decision = decisions[offset]
        session.add(
            FlowSample(
                scored_at=scored_at,
                # The bundle that produced *this* score, not whatever is loaded
                # when the nightly job runs. A sample scored by a since-replaced
                # champion must stay attributed to it or the drift it shows
                # cannot be told apart from the drift a promotion caused.
                model_version=str(decision.get("model_version") or "unknown"),
                source=source,
                anomaly_score=decision.get("anomaly_score"),
                alerted=decision.get("kind") is not None,
                raw_flow=dict(flow),
            )
        )
        kept += 1

    return kept


def prune_flow_samples(session: Session, keep: int | None = None) -> int:
    """Delete all but the most recent ``keep`` samples. Returns how many went.

    Run by the nightly job rather than on every insert: trimming inside the
    scoring path would put a delete in front of every batch during a 100x
    replay, which is the opposite of what a sampling table is for.

    Ordered by ``(scored_at, id)`` so the cut is deterministic even when a burst
    writes many rows inside one timestamp -- ``scored_at`` alone would leave the
    database free to keep a different subset of a tied group each time.
    """
    limit = settings.drift_sample_keep if keep is None else keep

    total = int(session.execute(select(func.count()).select_from(FlowSample)).scalar_one())
    if total <= limit:
        return 0

    # The oldest row worth keeping, as the same `(scored_at, id)` pair the
    # ordering uses.
    cutoff = session.execute(
        select(FlowSample.scored_at, FlowSample.id)
        .order_by(FlowSample.scored_at.desc(), FlowSample.id.desc())
        .offset(limit - 1)
        .limit(1)
    ).one_or_none()
    if cutoff is None:
        return 0

    # A row-value comparison rather than `id <= cutoff_id`. Ids and timestamps
    # happen to agree on the replay path, and would not if two sources with
    # different clocks ever wrote into this table -- at which point an id cut
    # would delete the wrong rows. SQLite 3.15+ and Postgres both support this.
    removed = session.execute(
        delete(FlowSample).where(
            tuple_(FlowSample.scored_at, FlowSample.id) < tuple_(cutoff[0], cutoff[1])
        )
    ).rowcount
    logger.info("pruned %s flow samples, keeping the most recent %s", removed, limit)
    return int(removed or 0)
