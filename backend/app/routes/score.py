"""POST /score -- batch flow scoring.

Batch always: accept a list of flow records and score them as a single matrix.
Per-row predict() is roughly 50x slower and makes the replay demo stutter.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import not_implemented
from app.schemas import NotImplementedResponse

router = APIRouter(prefix="/score", tags=["scoring"])

PHASE = "Phase 5 (backend API)"


@router.post(
    "",
    summary="Score a batch of flow records",
    responses={501: {"model": NotImplementedResponse}},
)
def score_flows():
    return not_implemented("POST /score", PHASE)
