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
* **`publish` is synchronous, non-async, and safe to call from another
  thread.** The scoring loop that calls it is not a coroutine this module can
  `await` inside, so `put_nowait` is the primitive that lets a synchronous
  caller push into an async queue. But it is not enough on its own:
  `app.replay` runs each batch's scoring through `asyncio.to_thread`, because
  scoring and explaining are CPU-bound and must not block the event loop
  serving `/stream`. So `publish` is genuinely called from a worker thread
  while every subscriber's queue belongs to the event loop -- and
  `asyncio.Queue` is explicitly *not* thread-safe. A bare cross-thread
  `put_nowait` can enqueue the event without ever waking the coroutine
  awaiting `get()`, which looks exactly like a feed that silently stopped.
  `publish` therefore hands the work to the subscriber's own loop with
  `call_soon_threadsafe` whenever it is called from off-loop, and only takes
  the direct path when it is already running on that loop.
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


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """The loop this call is running on, or ``None`` when called from a thread.

    ``asyncio.get_running_loop`` raises rather than returning ``None`` outside
    a coroutine, and "outside a coroutine" is the ordinary case for
    ``publish`` -- so the raise is absorbed here instead of at every call
    site.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


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
        # The loop the subscribers' queues belong to, captured at the first
        # `subscribe`. `subscribe` is only ever called from a request handler,
        # so it always runs on the serving loop; `publish` may not, which is
        # the whole reason this is recorded.
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue[dict]:
        """Register a new consumer and hand back its queue.

        Bounded at `MAX_QUEUED_EVENTS` -- see the module constant.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUED_EVENTS)
        # `_running_loop`, not `get_running_loop`: a unit test may subscribe
        # from plain synchronous code to check the fan-out, and there is no
        # loop to record there. Leaving `_loop` as None makes `publish` take
        # the direct path, which is correct when no loop owns the queue.
        self._loop = _running_loop() or self._loop
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

        Callable from any thread: see the module docstring on why the
        cross-thread case is the normal one here rather than the exotic one.
        """
        if not self._queues:
            return

        loop = self._loop
        if loop is not None and loop is not _running_loop():
            # Off-loop caller -- the replay engine's worker thread. Hand the
            # enqueue to the loop that owns the queues; doing it here would
            # mutate loop-owned state from another thread and could leave a
            # waiting `get()` unwoken.
            loop.call_soon_threadsafe(self._deliver, event)
            return

        self._deliver(event)

    def _deliver(self, event: dict) -> None:
        """The actual enqueue, always running on the subscribers' own loop.

        Iterates a snapshot: `call_soon_threadsafe` defers this, so a
        subscriber can disconnect between the publish and the delivery, and
        mutating the live set during iteration would raise.
        """
        for queue in tuple(self._queues):
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
