"""Aggregate analytics for whoever is not working the queue.

No accuracy hero tile here either. If this screen needs one big number it is
alerts per analyst hour or the unclassified-anomaly rate.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.routes import not_implemented
from app.schemas import NotImplementedResponse

router = APIRouter(prefix="/analytics", tags=["analytics"])

PHASE = "Phase 5 (backend API)"
STUB = {501: {"model": NotImplementedResponse}}


@router.get(
    "/summary",
    summary="Alerts over time, family mix, top hosts and sources, SOC throughput",
    responses=STUB,
)
def analytics_summary(
    range: str = Query("24h", pattern="^(24h|7d|30d|all)$", description="Time range"),
):
    return not_implemented(f"GET /analytics/summary?range={range}", PHASE)


@router.get(
    "/mitre-coverage",
    summary="Technique counts for the coverage heatmap",
    responses=STUB,
)
def mitre_coverage():
    return not_implemented("GET /analytics/mitre-coverage", PHASE)
