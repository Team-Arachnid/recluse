"""Phase 5 -- the SSE broker (`app/events.py`).

`EventBroker` is in-process pub/sub, built on plain `asyncio.Queue` objects
used only through their non-async `put_nowait`/`get_nowait`/`full`/`empty`
surface -- the same surface `publish` itself uses, which is exactly what lets
these tests run as ordinary synchronous functions with no event loop and no
`pytest-asyncio` (not a project dependency). That mirrors the real call site:
the scoring loop that calls `publish` is not itself a coroutine either.
"""

from __future__ import annotations

from app.events import MAX_QUEUED_EVENTS, EventBroker

# ---------------------------------------------------------------------------
# Fan-out and unsubscribe
# ---------------------------------------------------------------------------


def test_two_subscribers_both_receive_a_published_event() -> None:
    broker = EventBroker()
    first = broker.subscribe()
    second = broker.subscribe()

    broker.publish({"id": 1})

    assert first.get_nowait() == {"id": 1}
    assert second.get_nowait() == {"id": 1}


def test_an_unsubscribed_queue_does_not_receive_a_published_event() -> None:
    broker = EventBroker()
    unsubscribed = broker.subscribe()
    still_listening = broker.subscribe()

    broker.unsubscribe(unsubscribed)
    broker.publish({"id": 1})

    assert still_listening.get_nowait() == {"id": 1}
    assert unsubscribed.empty()


def test_unsubscribe_twice_does_not_raise() -> None:
    """A disconnecting client's `finally` can race the queue already having
    been dropped through some other path; neither call may raise."""
    broker = EventBroker()
    queue = broker.subscribe()

    broker.unsubscribe(queue)
    broker.unsubscribe(queue)


def test_unsubscribing_a_queue_that_was_never_subscribed_does_not_raise() -> None:
    broker = EventBroker()
    stray_queue = EventBroker().subscribe()

    broker.unsubscribe(stray_queue)


def test_subscribers_counts_current_subscriptions() -> None:
    broker = EventBroker()
    assert broker.subscribers == 0

    first = broker.subscribe()
    broker.subscribe()
    assert broker.subscribers == 2

    broker.unsubscribe(first)
    assert broker.subscribers == 1


# ---------------------------------------------------------------------------
# The bounded queue and its drop semantics
# ---------------------------------------------------------------------------


def test_a_full_queue_drops_rather_than_raising_or_blocking() -> None:
    broker = EventBroker()
    queue = broker.subscribe()
    for n in range(MAX_QUEUED_EVENTS):
        broker.publish({"n": n})
    assert queue.full()
    assert broker.dropped_events == 0

    broker.publish({"n": "one too many"})  # must not raise

    assert broker.dropped_events == 1
    # The dropped event never displaced anything already queued.
    assert queue.get_nowait() == {"n": 0}


def test_drop_count_rises_only_for_the_subscriber_that_is_actually_full() -> None:
    broker = EventBroker()
    saturated = broker.subscribe()
    for _ in range(MAX_QUEUED_EVENTS):
        broker.publish({})
    fresh = broker.subscribe()

    broker.publish({"marker": True})

    assert saturated.full()
    assert broker.dropped_events == 1
    assert fresh.get_nowait() == {"marker": True}


def test_repeated_drops_from_one_subscriber_keep_accumulating() -> None:
    broker = EventBroker()
    broker.subscribe()
    for _ in range(MAX_QUEUED_EVENTS):
        broker.publish({})

    broker.publish({})
    broker.publish({})
    broker.publish({})

    assert broker.dropped_events == 3


# ---------------------------------------------------------------------------
# active_source
# ---------------------------------------------------------------------------


def test_active_source_is_none_until_started_and_none_again_after_stopped() -> None:
    broker = EventBroker()
    assert broker.active_source is None

    broker.source_started("replay:tuesday")
    assert broker.active_source == "replay:tuesday"

    broker.source_stopped()
    assert broker.active_source is None


def test_source_started_reports_what_is_feeding_the_broker_not_just_that_something_is() -> None:
    broker = EventBroker()

    broker.source_started("replay:friday")

    assert broker.active_source == "replay:friday"
