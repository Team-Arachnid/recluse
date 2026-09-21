"""Model metrics, threshold what-ifs, drift, and the model registry.

Headline metric is PR-AUC. Accuracy may appear in a table but never as a
headline number: on 99% benign traffic, always answering benign scores 99%.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.routes import not_implemented
from app.schemas import NotImplementedResponse

router = APIRouter(tags=["metrics"])

STUB = {501: {"model": NotImplementedResponse}}


@router.get(
    "/metrics/model",
    summary="Per-class metrics, PR and ROC curves, and the LOAO table",
    responses=STUB,
)
def model_metrics():
    return not_implemented("GET /metrics/model", "Phase 5 (backend API)")


@router.get(
    "/metrics/threshold",
    summary="Recompute projected alert volume at a candidate threshold",
    responses=STUB,
)
def threshold_what_if(t: float = Query(..., ge=0.0, le=1.0, description="Candidate threshold")):
    return not_implemented(f"GET /metrics/threshold?t={t}", "Phase 5 (backend API)")


@router.get("/metrics/drift", summary="PSI per feature over time", responses=STUB)
def drift_metrics():
    return not_implemented("GET /metrics/drift", "Phase 7 (drift and active learning)")


@router.get(
    "/models",
    summary="Model registry with champion and challenger versions",
    responses=STUB,
)
def list_models():
    return not_implemented("GET /models", "Phase 7 (drift and active learning)")
