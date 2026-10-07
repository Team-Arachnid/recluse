"""Traffic source controls: dataset replay, and live capture in Phase 9.

`/replay/start` and `/replay/stop` drive `app.replay`'s background task;
`/ingest/start` is Phase 9's and still answers 501. All three share the
`traffic` tag because they are one concept -- where flows come from -- even
though they land in different phases.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.events import EventBroker
from app.inference import ModelBundle
from app.replay import (
    ReplayAlreadyRunning,
    ReplayNotRunning,
    ReplayState,
    UnknownDataset,
    replay_datasets,
    start_replay,
    stop_replay,
)
from app.routes import not_implemented
from app.schemas import (
    NotImplementedResponse,
    ReplayDataset,
    ReplayStartRequest,
    ReplayStatus,
)

router = APIRouter(tags=["traffic"])

STUB = {501: {"model": NotImplementedResponse}}


@router.get(
    "/replay/status",
    response_model=ReplayStatus,
    summary="Whether a replay is running, at what speed, and what it has done",
)
def replay_status(request: Request) -> ReplayStatus:
    """Read the current traffic-source state without changing it.

    Start and stop are both 202s whose bodies describe the state at the moment
    they were called, which is no help to a dashboard that was opened after the
    fact: a browser refresh in the middle of a replay would otherwise leave the
    speed control guessing, and a control that shows 1x while the engine runs
    at 100x is worse than one that shows nothing.

    Always 200, including when nothing is running -- `running: false` is the
    answer, not an error. `GET /stream` is the endpoint that distinguishes the
    two with a status code, because there a dead connection and an idle one are
    genuinely different problems.
    """
    state: ReplayState = request.app.state.replay
    return ReplayStatus(**state.as_status())


@router.get(
    "/replay/datasets",
    response_model=list[ReplayDataset],
    summary="The datasets a replay can stream, and which are present here",
)
def replay_dataset_list() -> list[ReplayDataset]:
    """Every name `/replay/start` accepts, with whether its file exists here.

    A container ships the committed demo sample but not the 500MB dataset, so
    the held-out days are listed as unavailable there rather than left for a
    start request to discover with a 422. The picker offers what will run.
    """
    return [ReplayDataset(**entry) for entry in replay_datasets()]


@router.post(
    "/replay/start",
    response_model=ReplayStatus,
    status_code=202,
    summary="Start replaying held-out test flows at 1x, 10x or 100x",
    responses={
        409: {"description": "A replay is already running."},
        422: {"description": "Unknown dataset, or a speed outside 1 / 10 / 100."},
        503: {"description": "No model bundle is loaded, so there is nothing to score with."},
    },
)
async def replay_start(body: ReplayStartRequest, request: Request) -> ReplayStatus:
    """Begin streaming a held-out split, scoring in batches and pushing over SSE.

    **202, not 200.** The work this starts outlives the request: the response
    says the replay has been accepted and is now running, not that it has
    finished. A 200 would claim a completed job.
    """
    bundle: ModelBundle = request.app.state.bundle
    broker: EventBroker = request.app.state.broker
    state: ReplayState = request.app.state.replay

    if not (bundle.stage1_ready or bundle.stage2_ready):
        raise HTTPException(
            status_code=503,
            detail=(
                "no complete model stage is loaded, so a replay would score nothing. "
                "Run the training pipeline first -- a replay that emitted no alerts "
                "would read as 'no attacks in this split', which is a different claim."
            ),
        )

    try:
        status = await start_replay(
            bundle=bundle,
            broker=broker,
            state=state,
            speed=body.speed,
            dataset=body.dataset,
        )
    except ReplayAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (UnknownDataset, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ReplayStatus(**status)


@router.post(
    "/replay/stop",
    response_model=ReplayStatus,
    status_code=202,
    summary="Stop the active replay",
    responses={409: {"description": "No replay is running."}},
)
async def replay_stop(request: Request) -> ReplayStatus:
    """Cancel the running replay and report what it managed before stopping.

    The counters in the response are the run's final totals, which is the
    useful thing to return from a stop -- "it scored 142,000 rows and raised
    318 alerts before you stopped it" rather than an empty acknowledgement.
    """
    broker: EventBroker = request.app.state.broker
    state: ReplayState = request.app.state.replay

    try:
        status = await stop_replay(state=state, broker=broker)
    except ReplayNotRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ReplayStatus(**status)


@router.post(
    "/ingest/start",
    summary="Begin scoring a live capture (authorised networks only)",
    responses=STUB,
)
def ingest_start():
    return not_implemented("POST /ingest/start", "Phase 9 (real traffic)")
