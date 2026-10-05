"""Pydantic wire contracts.

These are the source of truth for the frontend's TypeScript types, which are
generated from this app's OpenAPI schema (``npm run gen:types``) rather than
hand-written, so the two cannot silently drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, RootModel

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


# ---------------------------------------------------------------------------
# Phase 5 -- POST /api/v1/score
# ---------------------------------------------------------------------------


def _reject_string_feature_value(value: Any) -> float:
    """Coerce a JSON number or boolean to float; refuse a string outright.

    Pydantic's default coercion for a plain ``float`` field happily turns the
    JSON string ``"7.0"`` into ``7.0``, which would hide exactly the failure
    this schema exists to catch. ``ModelBundle._verify_payload``
    (app/inference.py) refuses a string-valued feature because the pandas
    pipeline that fills *absent* columns with 0.0 runs after
    ``select_dtypes`` has already dropped every non-numeric column -- so a
    feature the caller actually sent, as a string, is silently replaced by
    zero and the model scores a different flow than the one that arrived.
    Refusing every string here, before a DataFrame is even built, turns that
    into a 422 naming the field instead of a confidently wrong score.
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"expected a number or boolean, got {type(value).__name__} {value!r}")


FeatureValue = Annotated[float, BeforeValidator(_reject_string_feature_value)]


class FlowRecord(RootModel[dict[str, FeatureValue]]):
    """One flow to score -- POST /api/v1/score's request body is a list of these.

    An open mapping rather than a fixed-field model: the feature list belongs
    to the persisted artifact bundle's ``feature_order``
    (training/features.py), not to this code, and is not stable across a
    retrain. Values are coerced to float; a string value is refused by
    ``FeatureValue`` above rather than silently coerced or zeroed downstream.
    """


class ScoredFlow(BaseModel):
    """One row of POST /api/v1/score's response, in input order.

    ``detection_stage`` is nullable even though the "Planned" table in
    docs/API-Reference.md does not mark it so -- a documentation gap, not a
    design choice. ``training/fusion.py`` sets ``kind``, ``family`` and
    ``stage`` all to ``None`` for a row that raised no alert, and the
    response is one result per input row *including* those rows, which are
    the denominator of every rate on the dashboard.
    """

    model_config = ConfigDict(protected_namespaces=())

    kind: AlertKind | None = Field(
        description="Fusion outcome; null for a row that raised no alert."
    )
    family: AlertFamily | None = Field(
        description="Stage 1 label; always null for UNCLASSIFIED_ANOMALY."
    )
    confidence: float | None = Field(description="Stage 1 max attack-class probability.")
    anomaly_score: float | None = Field(description="Stage 2 mean reconstruction error.")
    detection_stage: DetectionStage | None = Field(
        description="Which stage fired; null if neither did."
    )
    model_version: str = Field(description="The bundle version that produced this score.")


class ScoreResponse(BaseModel):
    """POST /api/v1/score's response.

    ``scored`` and ``alerts`` are surfaced rather than left for the caller to
    recompute from ``results``: both are already known from the records
    ``ModelBundle.score_batch`` returns (the same information
    ``FusionDecisions.counts()`` carries), so re-deriving them client-side
    would just be a second place to get the denominator wrong.
    """

    results: list[ScoredFlow]
    scored: int = Field(description="Number of flow records scored; len(results).")
    alerts: int = Field(description="Number of results whose kind is not null.")


# ---------------------------------------------------------------------------
# Phase 5 -- the alert domain (GET/POST /api/v1/alerts...)
# ---------------------------------------------------------------------------


class Contributor(BaseModel):
    """One ranked feature behind an alert's score.

    ``value``/``contribution`` are TreeSHAP's (Stage 1); ``error`` is the
    reconstruction-error explainer's (Stage 2). Both groups are optional on
    one model, rather than two models the UI has to discriminate, because the
    Alert Detail drawer renders one signed bar chart either way (see
    app/explain.py).
    """

    feature: str
    share: float = Field(description="This contributor's share of the total attribution.")
    value: float | None = Field(
        default=None, description="The row's scaled feature value. TreeSHAP only."
    )
    contribution: float | None = Field(
        default=None, description="Signed SHAP contribution. TreeSHAP only."
    )
    error: float | None = Field(
        default=None, description="Per-feature reconstruction error. Stage 2 only."
    )


class AlertExplanation(BaseModel):
    """``explanation`` on AlertDetail -- app/explain.py's per-alert attribution.

    ``base_value`` is TreeSHAP's expected value for the predicted class and is
    absent for the reconstruction-error explainer, which has no such baseline.
    """

    explainer: Literal["treeshap", "reconstruction_error"]
    base_value: float | None = Field(
        default=None, description="TreeSHAP's expected value. Absent for Stage 2."
    )
    contributors: list[Contributor]


class Technique(BaseModel):
    """A MITRE ATT&CK technique, as app/mitre.py's static lookup carries it."""

    technique_id: str
    name: str
    url: str
    means: str = Field(description="One plain-English line true of this traffic.")


class RecommendedActions(BaseModel):
    """``recommended_actions`` on AlertDetail.

    The shape app/remediation.py's ``advice_for`` returns. ``technique: None``
    together with ``has_playbook: False`` is the honest unclassified-anomaly
    panel -- there is no technique and no playbook, and that combination has
    to be expressible rather than optioned away.
    """

    technique: Technique | None
    has_playbook: bool
    summary: str
    actions: list[str]


class AlertSummary(BaseModel):
    """One row of the triage queue -- GET /api/v1/alerts.

    Columns fixed by docs/Frontend-Screens.md section 1 (Triage Queue).
    ``latest_verdict`` is derived, not a column: ``analyst_verdicts`` is
    one-to-many, and this is where "the" verdict of an alert -- the most
    recent one by ``created_at`` -- is defined, so every endpoint that
    returns a summary agrees on what it means.
    """

    model_config = ConfigDict(protected_namespaces=())

    id: int
    kind: AlertKind
    family: AlertFamily | None = Field(
        description="Stage 1 label; always null for UNCLASSIFIED_ANOMALY."
    )
    severity: Severity
    risk_score: float = Field(description="Queue ordering key; never the timestamp.")
    confidence: float | None = Field(description="Stage 1 max attack-class probability.")
    anomaly_score: float | None = Field(description="Stage 2 mean reconstruction error.")
    detected_at: datetime
    src_ip: str
    src_port: int | None
    dst_ip: str
    dst_port: int | None
    protocol: str | None
    asset_criticality: str | None = Field(description="From enrichment; see app/topology.py.")
    status: AlertStatus
    occurrence_count: int = Field(description="The dedupe count; 5,000 flows can be one row.")
    first_seen: datetime
    last_seen: datetime
    mitre_technique: str | None = Field(
        description="Null for UNCLASSIFIED_ANOMALY, which maps to no technique."
    )
    detection_stage: DetectionStage
    model_version: str
    source: AlertSource
    latest_verdict: Verdict | None = Field(
        description="Most recent analyst_verdicts.verdict by created_at; null until judged."
    )


class AlertPage(BaseModel):
    """GET /api/v1/alerts' response: a page of the triage queue.

    No ``total``, deliberately: counting the whole filtered set on every page
    is the query that makes a triage queue slow, and nothing in
    docs/Frontend-Screens.md asks for one. Do not add it reflexively.
    """

    items: list[AlertSummary]
    next_cursor: str | None = Field(description="Opaque cursor for the next page; null if none.")
    limit: int


class AlertDetail(AlertSummary):
    """GET /api/v1/alerts/{alert_id}'s response.

    Everything a summary has, plus why it fired, what it likely is, and how
    to fix it. A superset of AlertSummary rather than an independent model,
    because the detail response genuinely is one and two separate models
    would drift apart.
    """

    explanation: AlertExplanation
    narrative: str = Field(description="The templated English sentence; app/explain.py::narrate.")
    recommended_actions: RecommendedActions
    raw_flow: dict[str, Any] = Field(
        description="The flow as it arrived; carries the _provenance key from app/topology.py."
    )
    ground_truth_label: str | None = Field(
        description="Replay only; always null for live capture, badged demo-only on the frontend."
    )
    host_prior_alert_count: int = Field(
        description="Other alerts from this source host; see GET /alerts/{id}/related."
    )


class VerdictRequest(BaseModel):
    """POST /api/v1/alerts/{alert_id}/verdict's request body."""

    verdict: Verdict
    note: str | None = Field(default=None, description="Free text.")
    analyst: str | None = Field(default=None, description="Who judged it.")


class VerdictResponse(BaseModel):
    """POST /api/v1/alerts/{alert_id}/verdict's 201 response: the created row.

    ``model_version`` is captured at verdict time -- fixed to whatever
    produced the alert being judged -- so the label stays auditable against
    that model even after a later retrain changes what is currently serving.
    """

    model_config = ConfigDict(protected_namespaces=())

    id: int
    alert_id: int
    verdict: Verdict
    note: str | None
    analyst: str | None
    model_version: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Phase 5 -- stream events (GET /api/v1/stream)
# ---------------------------------------------------------------------------


class AlertEvent(BaseModel):
    """Payload of the ``alert`` SSE event (API-Reference.md "Planned event shape").

    SSE responses are ``text/event-stream``, so FastAPI never attaches this
    as a ``response_model`` -- it exists purely so the frontend's
    ``EventSource`` handler has a generated type instead of a hand-written
    one that can silently drift from it. Deliberately narrower than
    AlertSummary: the ticker renders one line and the drawer fetches the
    rest when clicked.
    """

    model_config = ConfigDict(protected_namespaces=())

    id: int
    kind: AlertKind
    family: AlertFamily | None
    severity: Severity
    risk_score: float
    anomaly_score: float | None
    detected_at: datetime
    src_ip: str
    dst_ip: str
    occurrence_count: int
    model_version: str


class HeartbeatEvent(BaseModel):
    """Payload of the ``heartbeat`` SSE event.

    Sent so a client that has not seen an alert in a while can still tell the
    connection is alive rather than stalled.
    """

    ts: datetime


# ---------------------------------------------------------------------------
# Phase 5 -- replay control (POST /api/v1/replay/start, /replay/stop)
# ---------------------------------------------------------------------------


class ReplayStartRequest(BaseModel):
    """POST /api/v1/replay/start's request body.

    ``speed`` is a ``Literal`` of exactly the documented allowed values, so an
    unlisted speed is a 422 from the validation layer rather than something
    the replay engine has to police.
    """

    speed: Literal[1, 10, 100] = Field(description="Time acceleration factor.")
    dataset: str = Field(description="Held-out split name to replay.")


class ReplayStatus(BaseModel):
    """What POST /api/v1/replay/start and /replay/stop return on 202."""

    running: bool
    speed: int | None = Field(description="Null when no replay is running.")
    dataset: str | None = Field(description="Null when no replay is running.")
    started_at: datetime | None = Field(description="Null when no replay is running.")
    rows_scored: int
    alerts_emitted: int
