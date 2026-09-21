"""Phase 5 -- replay engine.

An asyncio background task streaming held-out test rows at accelerated time
(1x / 10x / 100x), scoring in batches and pushing alerts over SSE.

This is honest: real flows with real ground-truth labels, so the dashboard can
show predictions against truth. Ground truth is surfaced in the UI badged as
demo-only, and never for live capture.

Batch scoring, always. Per-row predict() in this loop is roughly 50x slower
and makes the demo stutter.

Optionally accepts a pcap upload, runs CICFlowMeter over it, and scores the
resulting flows through the same features module.
"""

from __future__ import annotations

from typing import Any


async def start_replay(*_: Any, **__: Any) -> None:
    raise NotImplementedError("The replay engine is implemented in Phase 5 (backend API).")


async def stop_replay(*_: Any, **__: Any) -> None:
    raise NotImplementedError("The replay engine is implemented in Phase 5 (backend API).")
