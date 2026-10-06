"""GET /stream -- server-sent events for the live alert feed.

SSE rather than WebSockets: the feed is one-directional, EventSource is built
into the browser, FastAPI does SSE in about ten lines, and there is no
reconnect logic to write.

Ten lines is not an exaggeration and nothing here adds to it beyond what the
protocol needs: a `StreamingResponse` over an async generator with content type
`text/event-stream`, two event names, and an `unsubscribe` in a `finally`. No
SSE library is installed and none is wanted -- `docs/API-Reference.md` spells
out this exact shape.

Alerts reach this stream *after* dedupe, never before. One compromised host
emitting 5,000 flows is one incident and therefore one event with a rising
`occurrence_count`; `app.pipeline.ingest_batch` publishes only once an alert is
persisted, so every `id` on this stream is one `GET /alerts/{id}` can resolve.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.events import EventBroker
from app.schemas import AlertEvent, HeartbeatEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stream", tags=["stream"])


class EventStreamResponse(StreamingResponse):
    """A `StreamingResponse` that tells the schema generator its media type.

    `StreamingResponse.media_type` is None, so FastAPI documents a streaming
    route's body under `application/json` and the frame models never reach
    `components/schemas`. Naming the type here is what lets the 200 below carry
    `AlertEvent | HeartbeatEvent` *as the event-stream payload*, which is in
    turn what puts both shapes in the OpenAPI document -- and therefore in the
    frontend's generated types, instead of hand-written copies that drift from
    `_frame` the first time a field is added.
    """

    media_type = "text/event-stream"


# How long a connection waits for an alert before emitting a heartbeat. The
# heartbeat is what keeps an idle connection from looking dead to a proxy or
# to the browser, and it is emitted per connection rather than by the broker,
# because "quiet" is a property of one reader and not of the hub -- see
# `app.events`' docstring.
HEARTBEAT_SECONDS = 15.0


def _frame(event_name: str, payload: dict) -> str:
    """One SSE frame: an event name, a single-line JSON data field, blank line.

    The blank line is the record separator the protocol requires, and the JSON
    must not contain a raw newline or the parser treats the remainder as a new
    field -- `json.dumps` never emits one, which is why the payload is not
    pretty-printed here.
    """
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


async def _events(broker: EventBroker) -> AsyncIterator[str]:
    """Yield alert frames as they arrive, and a heartbeat when none do.

    The `finally` is the load-bearing part: a browser tab closing cancels this
    generator, and without the `unsubscribe` the broker would keep a queue for
    a reader that no longer exists and keep filling it until it blocked on
    nothing.
    """
    queue = broker.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield _frame("heartbeat", {"ts": dt.datetime.now(dt.UTC).isoformat()})
                continue
            yield _frame("alert", event)
    finally:
        broker.unsubscribe(queue)


@router.get(
    "",
    response_class=EventStreamResponse,
    summary="Live alert feed over server-sent events",
    responses={
        200: {
            # A frame is one or the other, which is exactly what the anyOf
            # says. The union is also what registers both models.
            "model": AlertEvent | HeartbeatEvent,
            "description": "An open `text/event-stream` of `alert` and `heartbeat` events.",
        },
        503: {"description": "No traffic source is running, so there is nothing to stream."},
    },
)
async def stream_alerts(request: Request) -> StreamingResponse:
    """Hold a connection open and push one event per persisted alert.

    Answers **503** when no traffic source is running rather than holding open
    a connection that can never produce an event. An empty stream and a stream
    with nothing to say are indistinguishable from the client's side, and the
    first is a bug while the second is Tuesday -- so the two are given
    different status codes. `POST /replay/start` is what makes a source
    active.
    """
    broker: EventBroker = request.app.state.broker

    if broker.active_source is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "no traffic source is running, so this stream would never emit an "
                "alert. Start one with POST /api/v1/replay/start (or, from Phase 9, "
                "POST /api/v1/ingest/start) and reconnect."
            ),
        )

    return EventStreamResponse(
        _events(broker),
        headers={
            # Without these a reverse proxy may buffer the stream into
            # oblivion -- the feed looks dead while events pile up upstream,
            # which is the same symptom as forgetting `curl -N`.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
