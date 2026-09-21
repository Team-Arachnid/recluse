"""Phase 5 -- alert deduplication.

Key on (src_host, alert_class, floor(ts, dedupe_window_seconds)). On a hit,
increment occurrence_count and update last_seen instead of inserting.

One compromised host emitting 5,000 flows is one incident. Without this the
triage queue is unusable within thirty seconds of starting a replay.

Window length comes from IDS_DEDUPE_WINDOW_SECONDS (default 300s).
"""

from __future__ import annotations

import datetime as dt

from app.config import settings


def dedupe_key(src_host: str, alert_class: str, timestamp: dt.datetime) -> str:
    """Build the dedupe key: source host, class, and the floored time bucket.

    Implemented in Phase 0 because it is pure, cheap to test, and the schema
    column that stores it already exists.
    """
    window = settings.dedupe_window_seconds
    epoch = int(timestamp.timestamp())
    bucket = epoch - (epoch % window)
    return f"{src_host}|{alert_class}|{bucket}"
