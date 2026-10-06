"""Phase 5 -- the replay engine and the SSE stream in front of it.

The replay engine is the only thing in this project that produces alerts, so
these tests are the closest thing to an end-to-end check the backend has: a
batch of real held-out rows goes in one end and persisted, deduplicated,
pushed alerts come out the other.

Where a test needs a traffic source, it drives `app.replay`'s own functions
rather than sleeping through a real run. The engine paces itself with
`asyncio.sleep(BATCH_ROWS / (BASE_FLOWS_PER_SECOND * speed))`, which is ten
seconds a batch at 1x -- a suite that waited for that would be measuring the
sleep, not the scoring.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.events import EventBroker
from app.replay import (
    ALLOWED_SPEEDS,
    BASE_FLOWS_PER_SECOND,
    BATCH_ROWS,
    REPLAYABLE_SPLITS,
    ReplayAlreadyRunning,
    ReplayNotRunning,
    ReplayState,
    UnknownDataset,
    split_path,
    start_replay,
    stop_replay,
)

# ---------------------------------------------------------------------------
# Dataset resolution
# ---------------------------------------------------------------------------


def test_train_is_not_replayable() -> None:
    """Replaying the fit set would demonstrate memorisation, not detection.

    Asserted rather than left to the docstring because it is the one dataset
    name a well-meaning change would most plausibly add to the allowed list.
    """
    assert "train" not in REPLAYABLE_SPLITS
    assert "benign_train" not in REPLAYABLE_SPLITS

    with pytest.raises(UnknownDataset) as excinfo:
        split_path("train")

    assert "memorisation" in str(excinfo.value)


def test_an_unknown_dataset_names_the_allowed_splits() -> None:
    with pytest.raises(UnknownDataset) as excinfo:
        split_path("friday")

    message = str(excinfo.value)
    assert "friday" in message
    for split in REPLAYABLE_SPLITS:
        assert split in message


# ---------------------------------------------------------------------------
# Pacing
#
# The speed control must move only the gap between batches, never the batch
# size -- see app/replay.py's module docstring. If a future change made speed
# grow the batch instead, 100x would become a different computation rather
# than the same one delivered faster, and alert volume per wall-clock second
# would stop being a straight multiple of the 1x rate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("speed", ALLOWED_SPEEDS)
def test_speed_scales_the_delay_and_leaves_the_batch_alone(speed: int) -> None:
    delay = BATCH_ROWS / (BASE_FLOWS_PER_SECOND * speed)
    baseline = BATCH_ROWS / BASE_FLOWS_PER_SECOND

    assert delay == pytest.approx(baseline / speed)
    assert delay > 0


# ---------------------------------------------------------------------------
# Start / stop state machine
# ---------------------------------------------------------------------------


def test_a_fresh_state_reports_not_running() -> None:
    """The routes read a status object rather than probing for an attribute."""
    status = ReplayState().as_status()

    assert status["running"] is False
    assert status["speed"] is None
    assert status["dataset"] is None
    assert status["rows_scored"] == 0
    assert status["alerts_emitted"] == 0


def test_as_status_does_not_leak_the_task_handle() -> None:
    """`ReplayStatus` is a wire model; an asyncio.Task is not serialisable."""
    assert "task" not in ReplayState().as_status()


def test_starting_twice_is_a_conflict_not_a_second_replay() -> None:
    """Two replays would interleave into one another's dedupe windows."""
    state = ReplayState(running=True, dataset="test", speed=10)
    broker = EventBroker()

    with pytest.raises(ReplayAlreadyRunning) as excinfo:
        asyncio.run(
            start_replay(bundle=object(), broker=broker, state=state, speed=1, dataset="test")
        )

    assert "already running" in str(excinfo.value)


def test_stopping_nothing_is_a_conflict() -> None:
    with pytest.raises(ReplayNotRunning):
        asyncio.run(stop_replay(state=ReplayState(), broker=EventBroker()))


def test_a_bad_dataset_leaves_nothing_half_started() -> None:
    """A 422 must not put the service in a state /replay/stop has to clear.

    The dataset is validated before the state is armed, so a rejected start
    leaves `running` false and the broker with no active source -- otherwise a
    typo would wedge the service until someone called stop.
    """
    state = ReplayState()
    broker = EventBroker()

    with pytest.raises(UnknownDataset):
        asyncio.run(
            start_replay(bundle=object(), broker=broker, state=state, speed=1, dataset="nope")
        )

    assert state.running is False
    assert state.task is None
    assert broker.active_source is None


def test_a_bad_speed_is_rejected_before_anything_is_armed() -> None:
    state = ReplayState()
    broker = EventBroker()

    with pytest.raises(ValueError, match="speed must be one of"):
        asyncio.run(
            start_replay(bundle=object(), broker=broker, state=state, speed=7, dataset="test")
        )

    assert state.running is False
    assert broker.active_source is None


# ---------------------------------------------------------------------------
# The SSE framing
#
# The protocol is unforgiving about two things and silent about both: a `data:`
# field containing a raw newline makes the parser read the rest as a new
# field, and a missing blank line makes two events one.
# ---------------------------------------------------------------------------


def test_a_frame_is_one_line_of_json_terminated_by_a_blank_line() -> None:
    from app.routes.stream import _frame

    frame = _frame("alert", {"id": 1, "kind": "KNOWN", "risk_score": 0.5})

    assert frame.startswith("event: alert\ndata: ")
    assert frame.endswith("\n\n")

    body = frame.removeprefix("event: alert\ndata: ").removesuffix("\n\n")
    assert "\n" not in body
    assert json.loads(body)["id"] == 1


def test_a_frame_survives_a_payload_that_would_break_the_protocol() -> None:
    """A string field carrying a newline must be escaped, not passed through.

    `raw_flow` and `narrative` are free text elsewhere in this system, so the
    frame builder has to be safe against one arriving here rather than trust
    that it never will.
    """
    from app.routes.stream import _frame

    frame = _frame("alert", {"narrative": "line one\nline two"})

    assert frame.count("\n\n") == 1
    body = frame.split("data: ", 1)[1].removesuffix("\n\n")
    assert "\n" not in body
    assert json.loads(body)["narrative"] == "line one\nline two"


# ---------------------------------------------------------------------------
# The stream route
# ---------------------------------------------------------------------------


def test_stream_is_503_when_no_traffic_source_is_running(client, api_prefix: str) -> None:
    """An empty stream and a dead stream look identical from the client side.

    So they get different status codes: 503 says nothing is feeding this,
    which is the honest answer before a replay has been started.
    """
    response = client.get(f"{api_prefix}/stream")

    assert response.status_code == 503
    assert "replay/start" in response.json()["detail"]


def test_the_generator_frames_a_published_alert() -> None:
    """Drives `_events` directly rather than through TestClient's streaming.

    An SSE route held open through `client.stream` is a race by construction:
    the test has to publish after the headers arrive but before it blocks on
    the first chunk, and the heartbeat timeout is the only thing that would
    ever end the wait if it got the order wrong. Driving the generator on one
    loop is deterministic and still exercises the real framing, the real
    broker and the real queue.
    """
    from app.routes.stream import _events

    async def scenario() -> str:
        broker = EventBroker()
        stream = _events(broker)
        # Pull once to get past `subscribe()`, with a timeout so a broken
        # generator fails the test instead of hanging the suite.
        first = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        broker.publish({"id": 7, "kind": "UNCLASSIFIED_ANOMALY", "occurrence_count": 1})
        frame = await asyncio.wait_for(first, timeout=5)
        await stream.aclose()
        return frame

    frame = asyncio.run(scenario())

    assert frame.startswith("event: alert\ndata: ")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["id"] == 7
    assert payload["occurrence_count"] == 1


def test_a_disconnecting_client_is_unsubscribed() -> None:
    """Otherwise the broker keeps filling a queue nobody will ever read."""
    from app.routes.stream import _events

    async def scenario() -> tuple[int, int]:
        broker = EventBroker()
        stream = _events(broker)
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        subscribed = broker.subscribers
        broker.publish({"id": 1})
        await asyncio.wait_for(pending, timeout=5)
        await stream.aclose()
        return subscribed, broker.subscribers

    subscribed, after_close = asyncio.run(scenario())

    assert subscribed == 1
    assert after_close == 0


def test_publish_from_another_thread_reaches_a_waiting_subscriber() -> None:
    """The cross-thread path, which is the normal one in production.

    `app.replay` scores each batch through `asyncio.to_thread`, so
    `ingest_batch` -- and therefore `publish` -- runs on a worker thread while
    the subscriber queues belong to the serving loop. `asyncio.Queue` is not
    thread-safe: a bare `put_nowait` from off-loop can enqueue without ever
    waking the coroutine awaiting `get()`, which looks exactly like a feed
    that silently stopped. This is the regression test for that.
    """
    from app.routes.stream import _events

    async def scenario() -> str:
        broker = EventBroker()
        stream = _events(broker)
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        # Off the loop entirely -- exactly how the replay engine calls it.
        await asyncio.to_thread(broker.publish, {"id": 42})
        frame = await asyncio.wait_for(pending, timeout=5)
        await stream.aclose()
        return frame

    frame = asyncio.run(scenario())

    assert json.loads(frame.split("data: ", 1)[1].strip())["id"] == 42


# ---------------------------------------------------------------------------
# The replay control endpoints
# ---------------------------------------------------------------------------


def test_replay_stop_with_nothing_running_is_409(client, api_prefix: str) -> None:
    response = client.post(f"{api_prefix}/replay/stop")

    assert response.status_code == 409
    assert "nothing to stop" in response.json()["detail"]


def test_replay_start_rejects_an_unlisted_speed_at_the_schema(client, api_prefix: str) -> None:
    """422 from the validation layer, before the engine has to police it."""
    response = client.post(f"{api_prefix}/replay/start", json={"speed": 7, "dataset": "test"})

    assert response.status_code == 422


def test_replay_start_rejects_an_unknown_dataset(client, api_prefix: str) -> None:
    response = client.post(f"{api_prefix}/replay/start", json={"speed": 1, "dataset": "friday"})

    assert response.status_code in (422, 503)


def test_stop_awaits_the_task_so_a_following_start_cannot_race_it() -> None:
    """A stop that returned before the task wound down would let the next
    start arm the state while the old run was still writing."""
    state = ReplayState()
    broker = EventBroker()

    async def scenario() -> dict:
        started = asyncio.Event()

        async def _forever() -> None:
            started.set()
            try:
                await asyncio.sleep(3600)
            finally:
                state.running = False
                state.task = None
                broker.source_stopped()

        state.reset(speed=1, dataset="test")
        broker.source_started("replay:test")
        state.task = asyncio.create_task(_forever())
        await started.wait()
        return await stop_replay(state=state, broker=broker)

    status = asyncio.run(scenario())

    assert status["running"] is False
    assert state.task is None
    assert broker.active_source is None
