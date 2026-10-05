"""POST /score -- stateless batch flow scoring.

Batch always: accept a list of flow records and score them as a single
matrix. Per-row ``predict()`` is roughly 50x slower and makes the replay demo
stutter.

Stateless by design, and that is a controller ruling rather than an
oversight: this endpoint scores and returns, and persists no alert. The
documented response carries no ``id``, and an ``alerts`` row needs a source
address (``alerts.src_ip`` is ``NOT NULL``) that an arbitrary API caller does
not supply. The dedupe/enrich/persist/push pipeline that turns a decision
into a stored, deduplicated alert runs on the replay path (Tasks 6 and 7),
and on the live-capture path in Phase 9 -- both know where a flow came from.
This route never will.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.inference import ModelBundle
from app.schemas import FlowRecord, ScoredFlow, ScoreResponse

router = APIRouter(prefix="/score", tags=["scoring"])


@router.post(
    "",
    response_model=ScoreResponse,
    summary="Score a batch of flow records",
    responses={
        422: {"description": "A flow record is malformed (unrecognised or non-numeric features)."},
        503: {"description": "No model bundle is loaded."},
    },
)
def score_flows(flows: list[FlowRecord], request: Request) -> ScoreResponse:
    """Score ``flows`` through the loaded bundle and count the alerts.

    ``ModelBundle.score_batch`` already returns one record per flow, in
    input order, with ``model_version`` attached -- this handler maps those
    records onto the wire model and counts the alerts rather than
    re-deriving either from scratch.
    """
    bundle: ModelBundle = request.app.state.bundle

    try:
        records = bundle.score_batch([flow.root for flow in flows])
    except RuntimeError as exc:
        # No complete stage is loaded. The message was written to be read by
        # whoever sent the batch, so it passes through rather than being
        # replaced by a generic one.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        # `_verify_payload` naming a malformed or unrecognised feature.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    results = [ScoredFlow(**record) for record in records]
    alerts = sum(1 for result in results if result.kind is not None)
    return ScoreResponse(results=results, scored=len(results), alerts=alerts)
