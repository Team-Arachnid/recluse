"""FastAPI application factory and the health endpoint.

Models are loaded exactly once, in the lifespan context, and parked on
``app.state``. No request handler ever trains, fits, or reloads a model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.db import session_scope
from app.events import EventBroker
from app.inference import ModelBundle, load_bundle
from app.metrics_store import load_metrics
from app.registry import register_champion
from app.replay import ReplayState
from app.routes import api_router
from app.schemas import HealthResponse

logger = logging.getLogger("recluse")

API_DESCRIPTION = """
Two-stage network intrusion detection.

* **Stage 1** — supervised classifier: names the attack families it was trained on.
* **Stage 2** — benign-only autoencoder: flags traffic that does not look like
  normal, including families Stage 1 has never seen.

The system alerts, ranks and explains. It never blocks traffic.
"""


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown.

    Artifact loading happens here so that a bundle whose schema hash
    disagrees with the feature contract takes the process down at boot rather
    than producing silently wrong scores for every request afterwards.
    """
    _configure_logging()
    settings.ensure_directories()

    app.state.started_at = time.monotonic()

    # The SSE broker: one process-wide pub/sub hub between whatever traffic
    # source is running (Task 7's replay engine today, Phase 9's live capture
    # later) and any number of GET /stream consumers. Created unconditionally
    # here, not lazily on first use, so it exists before the first request
    # even when no traffic source has started yet -- GET /stream reads
    # app.state.broker.active_source to choose between 200 and 503, not
    # whether the attribute is present.
    app.state.broker = EventBroker()

    # Replay bookkeeping, created once here for the same reason: the
    # /replay/* routes report a status object rather than probing for an
    # attribute, so a status read before any replay has started answers
    # "running: false" instead of raising.
    app.state.replay = ReplayState()

    # A SchemaHashMismatch raised here is intentionally fatal.
    bundle: ModelBundle = load_bundle(settings.artifacts_path)
    app.state.bundle = bundle

    # The offline evaluation artifacts, read once beside the model they
    # describe. Per request would eventually let /metrics/* answer from a
    # newer file than the model actually serving -- a card describing a
    # champion that was replaced an hour ago.
    app.state.metrics = load_metrics(settings.artifacts_path)

    # Record what is actually loaded as the active champion (Phase 7).
    #
    # Written here rather than by the training run, because the training run
    # knows what it produced and only this process knows what it loaded -- and
    # those are the same thing right up until somebody copies a file by hand.
    # Idempotent, so a restart updates the row instead of growing the registry.
    #
    # Deliberately non-fatal. A registry write failing is a lost audit row, which
    # matters; refusing to serve over it would turn a bookkeeping problem into an
    # outage, and the audit trail itself (`alerts.model_version`) is written on
    # the alert path and is unaffected.
    try:
        with session_scope() as session:
            register_champion(session, bundle)
    except Exception:  # noqa: BLE001 - see above
        logger.exception("could not register %s in the model registry", bundle.version)

    logger.info(
        "%s ready | env=%s db=%s model_version=%s",
        settings.app_name,
        settings.env,
        settings.sqlalchemy_url.split("://", 1)[0],
        bundle.version,
    )
    logger.info(
        "false-positive budget: %d alerts/day over %d flows/day -> target FPR %.2e",
        settings.max_alerts_per_day,
        settings.expected_daily_flow_volume,
        settings.target_fpr,
    )

    try:
        yield
    finally:
        logger.info("%s shutting down", settings.app_name)


health_router = APIRouter(tags=["system"])


@health_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness, loaded model version, and process uptime",
)
def health(request: Request) -> HealthResponse:
    """Report what is actually loaded, not a hardcoded version string.

    ``model_version`` is ``"unloaded"`` until Phase 2 produces an artifact
    bundle. That is the honest answer for a scaffold, and it is what the
    dashboard renders.
    """
    bundle: ModelBundle = request.app.state.bundle
    started_at: float = request.app.state.started_at
    return HealthResponse(
        status=bundle.status,
        model_version=bundle.version,
        uptime_s=round(time.monotonic() - started_at, 3),
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        description=API_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )

    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
