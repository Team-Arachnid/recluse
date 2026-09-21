"""Traffic source controls: dataset replay, and live capture in Phase 9."""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import not_implemented
from app.schemas import NotImplementedResponse

router = APIRouter(tags=["traffic"])

STUB = {501: {"model": NotImplementedResponse}}


@router.post(
    "/replay/start",
    summary="Start replaying held-out test flows at 1x, 10x or 100x",
    responses=STUB,
)
def replay_start():
    return not_implemented("POST /replay/start", "Phase 5 (backend API)")


@router.post("/replay/stop", summary="Stop the active replay", responses=STUB)
def replay_stop():
    return not_implemented("POST /replay/stop", "Phase 5 (backend API)")


@router.post(
    "/ingest/start",
    summary="Begin scoring a live capture (authorised networks only)",
    responses=STUB,
)
def ingest_start():
    return not_implemented("POST /ingest/start", "Phase 9 (real traffic)")
