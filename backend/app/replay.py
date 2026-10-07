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

What "accelerated time" means here, given the data
---------------------------------------------------

The published MachineLearningCSV release ships no ``Timestamp`` column --
``training/features.py`` documents the removal, and the capture day survives
only in the file name. So there are no inter-arrival gaps to play back faster:
there is nothing to accelerate *from*. Replay time is therefore **synthesised**
rather than reconstructed, and saying so is the point.

One nominal rate stands in for the capture's real one, and ``speed``
multiplies it:

    sleep_between_batches = BATCH_ROWS / (BASE_FLOWS_PER_SECOND * speed)

``BATCH_ROWS`` stays fixed so that the batch handed to ``score_batch`` --
and therefore the matrix the model sees, and the cost of one explanation
pass -- does not change with the speed control. Only the gap between batches
moves. A speed control that grew the batch instead would make 100x a
different *computation* rather than the same one delivered faster, and the
alert volume per wall-clock second would stop being a straight multiple of
the 1x rate.

``detected_at`` is replay wall-clock, which is the honest answer: a replay
genuinely detects at replay time. ``app.topology.provenance()`` records that
alongside the derived addresses, so every alert carries the caveat with it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.db import session_scope
from app.pipeline import ingest_batch
from app.release import DEMO_FLOWS_FILE
from training.features import LABEL_COLUMN

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.events import EventBroker
    from app.inference import ModelBundle

logger = logging.getLogger(__name__)

# The splits a replay may draw from. `train` is deliberately absent: replaying
# the rows Stage 1 was fitted on would show the model recognising traffic it
# memorised, which is the one demo that proves nothing. `benign_train` is
# absent for the same reason on the Stage 2 side. `demo` is the committed
# sample of the two held-out days (`app.seed` builds it), so a clean clone or a
# container without the 500MB dataset can still replay real flows.
REPLAYABLE_SPLITS: tuple[str, ...] = ("test", "val", "demo")

# What each name is, for the dashboard's dataset picker: a label, and one line
# saying which traffic it holds and what it was used for.
DATASET_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "test": (
        "Held-out test day",
        "Friday in full: the held-out day the README's numbers describe.",
    ),
    "val": (
        "Validation day",
        "Thursday in full: the day both thresholds were cut on.",
    ),
    "demo": (
        "Demo sample",
        "Real flows from both held-out days, committed with the release, so a "
        "clean clone replays without the dataset. backend/release/demo_flows.json "
        "says exactly which.",
    ),
}

# The nominal 1x flow rate. See the module docstring: the release carries no
# timestamps, so this stands in for a rate the data cannot supply rather than
# measuring one.
BASE_FLOWS_PER_SECOND = 50

# Rows per scored batch, fixed across every speed -- see the module docstring.
BATCH_ROWS = 500

ALLOWED_SPEEDS: tuple[int, ...] = (1, 10, 100)


class ReplayAlreadyRunning(RuntimeError):
    """A replay was requested while one was already streaming."""


class ReplayNotRunning(RuntimeError):
    """A stop was requested with nothing streaming."""


class UnknownDataset(ValueError):
    """A dataset name that is not a replayable split, or whose file is absent."""


@dataclass
class ReplayState:
    """What the running replay has done so far, as ``/replay/*`` reports it.

    Lives on ``app.state.replay`` and is created once in the lifespan, so the
    routes read a status object that exists before any replay has started
    rather than probing for an attribute.
    """

    running: bool = False
    speed: int | None = None
    dataset: str | None = None
    started_at: dt.datetime | None = None
    rows_scored: int = 0
    alerts_emitted: int = 0
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def as_status(self) -> dict[str, Any]:
        """The ``ReplayStatus`` payload, without the task handle."""
        return {
            "running": self.running,
            "speed": self.speed,
            "dataset": self.dataset,
            "started_at": self.started_at,
            "rows_scored": self.rows_scored,
            "alerts_emitted": self.alerts_emitted,
        }

    def reset(self, *, speed: int, dataset: str) -> None:
        """Arm the state for a fresh run, zeroing the previous run's counters."""
        self.running = True
        self.speed = speed
        self.dataset = dataset
        self.started_at = dt.datetime.now(dt.UTC)
        self.rows_scored = 0
        self.alerts_emitted = 0


def split_path(dataset: str) -> Path:
    """Resolve a replayable split name to its parquet file, or raise.

    Raises rather than falling back to another split: a replay that silently
    streamed a different day than the one asked for would make every number on
    the dashboard describe traffic nobody selected.
    """
    if dataset not in REPLAYABLE_SPLITS:
        raise UnknownDataset(
            f"{dataset!r} is not a replayable split. Choose one of "
            f"{list(REPLAYABLE_SPLITS)} -- 'train' and 'benign_train' are "
            "excluded on purpose, because replaying the rows a model was "
            "fitted on demonstrates memorisation rather than detection."
        )
    if dataset == "demo":
        path = settings.release_path / DEMO_FLOWS_FILE
        if not path.exists():
            raise UnknownDataset(
                f"the demo sample is committed at {path}, and this checkout does not "
                "have it. `python -m app.seed sample` rebuilds it from data/processed."
            )
        return path
    path = settings.data_path / "processed" / f"{dataset}.parquet"
    if not path.exists():
        raise UnknownDataset(
            f"{dataset!r} has no processed split at {path}. Run the Phase 1 "
            "pipeline (`make data`) before starting a replay, or replay 'demo', "
            "the committed sample, which needs no download."
        )
    return path


def replay_datasets() -> list[dict[str, Any]]:
    """Every replayable name, whether its file is present, and how many rows it holds.

    The row count comes from the Parquet footer, so listing costs a metadata
    read rather than loading a few hundred thousand rows.
    """
    import pyarrow.parquet as pq

    listing = []
    for name in REPLAYABLE_SPLITS:
        label, description = DATASET_DESCRIPTIONS[name]
        try:
            path = split_path(name)
        except UnknownDataset as exc:
            listing.append(
                {
                    "name": name,
                    "label": label,
                    "description": description,
                    "available": False,
                    "rows": None,
                    "reason": str(exc),
                }
            )
            continue
        listing.append(
            {
                "name": name,
                "label": label,
                "description": description,
                "available": True,
                "rows": pq.read_metadata(path).num_rows,
                "reason": None,
            }
        )
    return listing


def load_replay_rows(dataset: str) -> tuple[list[dict[str, Any]], list[str | None]]:
    """Read one split into flow dicts plus their published ground-truth labels.

    The label is split out rather than left in the flow dicts: it is the
    dataset's own answer, it must never reach the feature matrix, and it is
    carried separately so ``ingest_batch`` can store it as
    ``alerts.ground_truth_label`` while the model never sees it.

    The whole split is read once, up front, rather than streamed from disk per
    batch. These are a few hundred thousand rows of float64 -- cheap to hold --
    and a reader that re-opened the file per batch would put disk latency
    inside the loop whose timing is the thing being controlled.
    """
    import pandas as pd

    frame = pd.read_parquet(split_path(dataset))
    labels: list[str | None] = (
        [str(value) for value in frame[LABEL_COLUMN]] if LABEL_COLUMN in frame.columns else []
    )
    features = frame.drop(columns=[LABEL_COLUMN], errors="ignore")
    flows = features.to_dict(orient="records")
    if not labels:
        labels = [None] * len(flows)
    return flows, labels


async def _run(
    *,
    bundle: ModelBundle,
    broker: EventBroker,
    state: ReplayState,
    dataset: str,
    speed: int,
) -> None:
    """The background task: score the split in batches, pacing between them.

    Each batch gets its own ``session_scope()`` so one commit covers one batch
    -- a 5,000-flow burst is one transaction, and a batch that fails rolls back
    alone instead of taking the whole run's writes with it.
    """
    flows, labels = await asyncio.to_thread(load_replay_rows, dataset)
    delay = BATCH_ROWS / (BASE_FLOWS_PER_SECOND * speed)
    logger.info(
        "replay starting: %s, %d rows, speed %dx (%d rows/batch, %.3fs between batches)",
        dataset,
        len(flows),
        speed,
        BATCH_ROWS,
        delay,
    )

    try:
        for start in range(0, len(flows), BATCH_ROWS):
            batch = flows[start : start + BATCH_ROWS]
            truth = labels[start : start + BATCH_ROWS]

            # Scoring and explaining are synchronous, CPU-bound and can take
            # tens of milliseconds; off-thread so the event loop keeps serving
            # /stream and /alerts while a batch is in flight.
            alerts = await asyncio.to_thread(
                _score_and_ingest,
                bundle=bundle,
                broker=broker,
                batch=batch,
                truth=truth,
                start_index=start,
            )

            state.rows_scored += len(batch)
            state.alerts_emitted += alerts

            await asyncio.sleep(delay)

        logger.info(
            "replay finished: %s, %d rows scored, %d alerts",
            dataset,
            state.rows_scored,
            state.alerts_emitted,
        )
    except asyncio.CancelledError:
        logger.info(
            "replay stopped: %s, %d rows scored, %d alerts",
            dataset,
            state.rows_scored,
            state.alerts_emitted,
        )
        raise
    finally:
        # Runs on normal completion, on cancellation, and on an unhandled
        # error alike. Without it a run that ended on its own would leave
        # `running` true and `active_source` set, so /stream would keep
        # claiming a live feed and /replay/start would keep answering 409.
        state.running = False
        state.task = None
        broker.source_stopped()


def _score_and_ingest(
    *,
    bundle: ModelBundle,
    broker: EventBroker,
    batch: list[dict[str, Any]],
    truth: list[str | None],
    start_index: int,
) -> int:
    """Score one batch and run it through the alert pipeline. Returns alert count.

    Synchronous on purpose: it is called through ``asyncio.to_thread`` and
    holds no event-loop state. ``detected_at`` is taken once per batch rather
    than per row, so every alert in a batch shares the instant the batch was
    scored -- which is what the dedupe window is bucketing on anyway.
    """
    decisions = bundle.score_batch(batch)
    detected_at = dt.datetime.now(dt.UTC)
    with session_scope() as session:
        alerts = ingest_batch(
            session,
            flows=batch,
            decisions=decisions,
            bundle=bundle,
            detected_at=detected_at,
            source="replay",
            ground_truth=truth,
            broker=broker,
            start_index=start_index,
        )
        return len(alerts)


async def start_replay(
    *,
    bundle: ModelBundle,
    broker: EventBroker,
    state: ReplayState,
    speed: int,
    dataset: str,
) -> dict[str, Any]:
    """Start a replay, or raise if one is already running.

    Validates the dataset *before* arming the state, so a bad dataset name
    leaves nothing half-started: a 422 from here must not put the service in a
    condition where /replay/stop is needed to clear it.
    """
    if state.running:
        raise ReplayAlreadyRunning(
            f"a replay of {state.dataset!r} is already running at {state.speed}x. "
            "Stop it before starting another -- two replays would interleave "
            "into one another's dedupe windows and the queue could not be read "
            "as either run."
        )
    if speed not in ALLOWED_SPEEDS:
        raise ValueError(f"speed must be one of {list(ALLOWED_SPEEDS)}; got {speed!r}.")

    split_path(dataset)  # raises UnknownDataset before anything is armed

    state.reset(speed=speed, dataset=dataset)
    broker.source_started(f"replay:{dataset}")
    state.task = asyncio.create_task(
        _run(bundle=bundle, broker=broker, state=state, dataset=dataset, speed=speed)
    )
    return state.as_status()


async def stop_replay(*, state: ReplayState, broker: EventBroker) -> dict[str, Any]:
    """Cancel the running replay, or raise if there is nothing to stop.

    Awaits the cancelled task so that by the time this returns, ``_run``'s
    ``finally`` has cleared ``running`` and the broker's active source. A stop
    that returned before the task actually wound down would let an immediately
    following /replay/start race it.
    """
    if not state.running or state.task is None:
        raise ReplayNotRunning(
            "no replay is running, so there is nothing to stop. POST /replay/start begins one."
        )

    task = state.task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    state.running = False
    state.task = None
    broker.source_stopped()
    return state.as_status()
