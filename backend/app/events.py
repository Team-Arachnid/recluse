"""Phase 5 -- the SSE broker: in-process pub/sub for the alert stream.

One producer -- today Task 7's replay engine, later Phase 9's live capture --
calls `publish` once per persisted alert. Any number of `GET /stream`
connections (Task 7) each hold their own subscription and read from it
independently. This module is only the broker: it has no opinion about HTTP,
`text/event-stream` framing, or reconnection, all of which belong to the
stream route.

Design points that belong here rather than at the call site:

* **One bounded `asyncio.Queue` per subscriber, and `publish` drops rather
  than blocks or raises when a subscriber's queue is full.** At 100x replay
  speed the feed is a live ticker, not a delivery guarantee: a browser tab
  that stops reading (backgrounded, a slow link) must not be able to make
  `publish` block and stall the scoring loop that calls it, and must not be
  able to make its own queue grow without bound either. Dropping the newest
  event for *that* subscriber is the correct loss. A silent drop would be a
  lie about what the ticker showed, so every drop is counted in
  `dropped_events` instead, which the stream route can report.
* **`publish` is synchronous and non-async.** The scoring loop that calls it
  is not a coroutine this module can `await` inside, and `asyncio.Queue`'s
  `put_nowait` is exactly the primitive that lets a synchronous caller push
  into an async queue without one.
* **`active_source` is a nullable name, not a boolean.** `GET /stream`
  consults it to choose between 200 and the documented 503 ("no active
  traffic source"), and modelling it as *what* is feeding the broker --
  e.g. `"replay:tuesday"` -- rather than merely *whether* something is lets
  the stream's status line say which. `source_started`/`source_stopped` are
  the pair Task 7's replay engine calls, and Phase 9's live capture will call
  the same pair.
* **No heartbeat timer lives here.** The documented `heartbeat` SSE event is
  a property of one connection that has gone quiet -- Task 7's stream route
  emits it on a timer when its own queue read times out -- not a property of
  the broker, which has no notion of "quiet" and no per-connection timer to
  attach one to. Do not add a broker-side heartbeat: it would have nothing
  to fire into but whatever the subscriber count happens to be, including
  zero.
* **Unsubscribing an already-removed queue must not raise.** A disconnecting
  client's stream-route `finally` can race the broker having already dropped
  the queue through some other path, so `unsubscribe` is built on
  `set.discard`, not `remove`.
"""

from __future__ import annotations

import asyncio

# One bounded queue per subscriber. Bounded because a live ticker's job is to
# show what is happening now, not to guarantee eventual delivery of
# everything that ever happened -- see the module docstring for why an
# unbounded queue (or a blocking `put`) is the wrong trade here.
MAX_QUEUED_EVENTS = 1000


class EventBroker:
    """In-process pub/sub between one traffic source and many stream readers.

    Holds no state about alerts themselves -- it only fans out whatever
    dict `publish` is given. `app.pipeline.ingest_batch` is the only
    intended caller of `publish`, and only after an alert is already
    persisted (see its docstring for why).
    """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict]] = set()
        self._active_source: str | None = None
        self._dropped_events = 0

    def subscribe(self) -> asyncio.Queue[dict]:
        """Register a new consumer and hand back its queue.

        Bounded at `MAX_QUEUED_EVENTS` -- see the module constant.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUED_EVENTS)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        """Drop a consumer's queue. A missing queue is not an error -- see
        the module docstring's note on the disconnect race."""
        self._queues.discard(queue)

    def publish(self, event: dict) -> None:
        """Fan `event` out to every subscriber, dropping rather than blocking.

        A subscriber whose queue is full has a consumer that is not keeping
        up. The correct response is to drop the newest event for *that*
        subscriber -- counted in `dropped_events` -- never to raise and
        never to block every other subscriber, or the caller, while this one
        catches up.
        """
        for queue in self._queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped_events += 1

    def source_started(self, name: str) -> None:
        """Record that a traffic source is now feeding the broker.

        `name` rather than a boolean so `GET /stream` can report *what* is
        live (e.g. `"replay:tuesday"`), not just that something is.
        """
        self._active_source = name

    def source_stopped(self) -> None:
        """Record that no traffic source is feeding the broker any more."""
        self._active_source = None

    @property
    def active_source(self) -> str | None:
        """The running source's name, or `None` if nothing is feeding the broker.

        `GET /stream` consults exactly this to choose between 200 and the
        documented 503 (no active traffic source).
        """
        return self._active_source

    @property
    def subscribers(self) -> int:
        """How many consumers are currently subscribed."""
        return len(self._queues)

    @property
    def dropped_events(self) -> int:
        """Total events dropped across all subscribers since this broker was created."""
        return self._dropped_events
