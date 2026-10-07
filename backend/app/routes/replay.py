"""Traffic source controls: dataset replay, and live capture (Phase 9).

`/replay/*` drives `app.replay`'s background task and `/ingest/*` drives
`app.live_capture`'s. They share the `traffic` tag because they are one
concept -- where flows come from -- and they never run at once: one writer is
what keeps the dedupe upsert's single-writer invariant.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.events import EventBroker
from app.inference import ModelBundle
from app.live_capture import (
    CaptureNotAuthorised,
    IngestAlreadyRunning,
    IngestNotRunning,
    IngestState,
    NotCalibrated,
    UnknownPcap,
    available_pcaps,
    start_ingest,
    stop_ingest,
    usable_local_threshold,
)
from app.replay import (
    ReplayAlreadyRunning,
    ReplayNotRunning,
    ReplayState,
    UnknownDataset,
    replay_datasets,
    start_replay,
    stop_replay,
)
from app.schemas import (
    IngestStartRequest,
    IngestStatus,
    LocalCalibration,
    ReplayDataset,
    ReplayStartRequest,
    ReplayStatus,
)

router = APIRouter(tags=["traffic"])


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

    ingest: IngestState = request.app.state.ingest
    if ingest.running:
        raise HTTPException(
            status_code=409,
            detail=f"a live capture of {ingest.source} is running; replay and capture never "
            "write at once. Stop it with POST /ingest/stop first.",
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


def _ingest_status(request: Request) -> IngestStatus:
    state: IngestState = request.app.state.ingest
    bundle: ModelBundle = request.app.state.bundle
    calibration = usable_local_threshold(bundle)
    return IngestStatus(
        **state.as_status(),
        allowed_interfaces=settings.live_interface_list,
        pcaps=available_pcaps(),
        tau_anom_dataset=bundle.tau_anom,
        calibration=None if calibration is None else LocalCalibration(**calibration),
    )


@router.get(
    "/ingest/status",
    response_model=IngestStatus,
    summary="Whether a live capture is running, in which mode, and both thresholds",
)
def ingest_status(request: Request) -> IngestStatus:
    """The capture's counters, where it may run, and the two Stage 2 thresholds.

    Always 200. `calibration` is the local threshold a shadow burn-in produced,
    and null until one exists for the Stage 2 model that is serving -- which is
    also exactly when alert mode is refused.
    """
    return _ingest_status(request)


@router.post(
    "/ingest/start",
    response_model=IngestStatus,
    status_code=202,
    summary="Begin scoring a live capture (authorised networks only)",
    responses={
        403: {"description": "The interface is not in IDS_LIVE_INTERFACES."},
        404: {"description": "No such pcap in IDS_LIVE_PCAP_DIR."},
        409: {
            "description": "A capture or replay is running, or alert mode was asked for "
            "before a shadow burn-in calibrated a local threshold."
        },
        422: {"description": "An invalid source, mode, or a missing interface or pcap name."},
        503: {"description": "Stage 2 is not loaded."},
    },
)
async def ingest_start(body: IngestStartRequest, request: Request) -> IngestStatus:
    """Start a shadow burn-in or a live alerting capture.

    **Where it runs is configuration, not a parameter.** The interface must be
    one the operator listed in `IDS_LIVE_INTERFACES` -- capture is lawful only on
    networks you own or are authorised to monitor -- and a pcap must be a file in
    `IDS_LIVE_PCAP_DIR`. **Shadow before alert:** alert mode needs a local
    `tau_anom` from `python -m training.calibrate_live`, because the CICIDS2017
    threshold on a real network floods the queue with its ordinary traffic.
    """
    bundle: ModelBundle = request.app.state.bundle
    if not bundle.stage2_ready:
        raise HTTPException(status_code=503, detail="Stage 2 is not loaded.")
    try:
        await start_ingest(
            bundle=bundle,
            broker=request.app.state.broker,
            state=request.app.state.ingest,
            replay_running=request.app.state.replay.running,
            source=body.source,
            interface=body.interface,
            pcap=body.pcap,
            mode=body.mode,
        )
    except CaptureNotAuthorised as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except UnknownPcap as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (IngestAlreadyRunning, NotCalibrated) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _ingest_status(request)


@router.post(
    "/ingest/stop",
    response_model=IngestStatus,
    status_code=202,
    summary="Stop the live capture after scoring what it has already metered",
    responses={409: {"description": "No capture is running."}},
)
async def ingest_stop(request: Request) -> IngestStatus:
    """Stop capturing; flows still open are finished and scored before this returns."""
    try:
        await stop_ingest(state=request.app.state.ingest)
    except IngestNotRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _ingest_status(request)
