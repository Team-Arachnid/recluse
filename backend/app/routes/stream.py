"""GET /stream -- server-sent events for the live alert feed.

SSE rather than WebSockets: the feed is one-directional, EventSource is built
into the browser, FastAPI does SSE in about ten lines, and there is no
reconnect logic to write.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import not_implemented
from app.schemas import NotImplementedResponse

router = APIRouter(prefix="/stream", tags=["stream"])

PHASE = "Phase 5 (backend API)"


@router.get(
    "",
    summary="Live alert feed over server-sent events",
    responses={501: {"model": NotImplementedResponse}},
)
def stream_alerts():
    return not_implemented("GET /stream", PHASE)
