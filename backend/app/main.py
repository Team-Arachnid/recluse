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
from app.inference import ModelBundle, load_bundle
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

    # A SchemaHashMismatch raised here is intentionally fatal.
    bundle: ModelBundle = load_bundle(settings.artifacts_path)
    app.state.bundle = bundle

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
