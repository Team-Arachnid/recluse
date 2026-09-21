"""Alert queue, detail, verdicts and host correlation."""

from __future__ import annotations

from fastapi import APIRouter

from app.routes import not_implemented
from app.schemas import NotImplementedResponse

router = APIRouter(prefix="/alerts", tags=["alerts"])

PHASE = "Phase 5 (backend API)"
STUB = {501: {"model": NotImplementedResponse}}


@router.get("", summary="Filter, sort and cursor-paginate the alert queue", responses=STUB)
def list_alerts():
    return not_implemented("GET /alerts", PHASE)


@router.get(
    "/{alert_id}",
    summary="Alert detail: explanation, narrative, remediation, raw flow",
    responses=STUB,
)
def get_alert(alert_id: int):
    return not_implemented(f"GET /alerts/{alert_id}", PHASE)


@router.post(
    "/{alert_id}/verdict",
    summary="Record an analyst verdict (TP / FP / UNSURE) with an optional note",
    responses=STUB,
)
def submit_verdict(alert_id: int):
    return not_implemented(f"POST /alerts/{alert_id}/verdict", PHASE)


@router.get(
    "/{alert_id}/related",
    summary="Other alerts from the same source host in a 24h window",
    responses=STUB,
)
def related_alerts(alert_id: int):
    return not_implemented(f"GET /alerts/{alert_id}/related", PHASE)
