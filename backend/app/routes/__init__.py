"""API route modules.

Phase 0 registers the full v1 surface from BUILD_PROMPT.md Part 8 so the
OpenAPI schema -- and therefore the generated frontend types -- exists from the
start. Only /health is implemented; every other endpoint answers 501 with the
phase that fills it in.

A 501 with a machine-readable body is deliberate: a caller can tell "not built
yet" apart from "built and broken", and no route ever returns invented data.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.schemas import NotImplementedResponse

__all__ = ["api_router", "not_implemented"]


def not_implemented(endpoint: str, phase: str) -> JSONResponse:
    """Return a 501 naming the endpoint and the phase that implements it."""
    body = NotImplementedResponse(
        detail=f"Not implemented yet. Arrives in {phase}.",
        phase=phase,
        endpoint=endpoint,
    )
    return JSONResponse(status_code=501, content=body.model_dump())


# Imported after the helper so the route modules can use it without a cycle.
from app.routes import (  # noqa: E402
    alerts,
    analytics,
    metrics,
    replay,
    score,
    stream,
)

api_router = APIRouter()
api_router.include_router(alerts.router)
api_router.include_router(score.router)
api_router.include_router(metrics.router)
api_router.include_router(analytics.router)
api_router.include_router(replay.router)
api_router.include_router(stream.router)
