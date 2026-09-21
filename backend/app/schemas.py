"""Pydantic wire contracts.

These are the source of truth for the frontend's TypeScript types, which are
generated from this app's OpenAPI schema (``npm run gen:types``) rather than
hand-written, so the two cannot silently drift.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared vocabularies. Kept in step with the CheckConstraints in app/models.py.
# ---------------------------------------------------------------------------
AlertKind = Literal["KNOWN", "UNCLASSIFIED_ANOMALY"]
AlertFamily = Literal[
    "dos",
    "ddos",
    "brute_force",
    "port_scan",
    "web_attack",
    "botnet",
    "infiltration",
]
Severity = Literal["low", "medium", "high", "critical"]
AlertStatus = Literal["open", "in_review", "closed", "dismissed"]
Verdict = Literal["TP", "FP", "UNSURE"]
DetectionStage = Literal["stage1_supervised", "stage2_anomaly"]
AlertSource = Literal["replay", "live", "api"]

HealthStatus = Literal["ok", "degraded"]


class HealthResponse(BaseModel):
    """``GET /api/v1/health``.

    The contract is exactly three fields, per Phase 0. ``model_version`` is
    ``"unloaded"`` until Phase 2 writes a real artifact bundle -- the endpoint
    reports what is actually loaded rather than a hardcoded string.
    """

    # `model_` is a protected prefix in Pydantic v2; this field name is part
    # of the agreed contract, so the namespace guard is lifted here.
    model_config = ConfigDict(protected_namespaces=())

    status: HealthStatus = Field(
        description="'ok' once the process is serving; 'degraded' if a loaded "
        "artifact bundle is present but unusable."
    )
    model_version: str = Field(
        description="Version of the currently loaded model bundle, or 'unloaded'.",
        examples=["unloaded", "rf-20260921-1"],
    )
    uptime_s: float = Field(
        ge=0,
        description="Seconds since the FastAPI lifespan started.",
        examples=[12.482],
    )


class NotImplementedResponse(BaseModel):
    """Body returned by route stubs that a later phase fills in.

    Explicit and machine-readable, so a caller can tell "not built yet" apart
    from "built and broken". No stub returns invented data.
    """

    detail: str
    phase: str = Field(description="Build phase that implements this endpoint.")
    endpoint: str
