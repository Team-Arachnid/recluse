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
# The queue's verdict filter. Wider than `Verdict` by one value: "none" is the
# unjudged case, which is the one an analyst starting a shift actually wants and
# which no member of `Verdict` can express.
VerdictFilter = Literal["TP", "FP", "UNSURE", "none"]
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
    ground_truth_counts: dict[str, int] | None = Field(
        default=None,
        description=(
            "Replay only: every dataset label this alert's bucket absorbed, counted. "
            "ground_truth_label is the first flow's; this is all of them. Null for live "
            "capture and for alerts stored before it existed."
        ),
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


class ReplayDataset(BaseModel):
    """One entry of GET /api/v1/replay/datasets: a name /replay/start accepts."""

    name: str = Field(description="The value to send as ReplayStartRequest.dataset.")
    label: str
    description: str = Field(description="Which traffic it holds and what it was used for.")
    available: bool = Field(description="False when its file is absent from this deployment.")
    rows: int | None = Field(description="Flows in the file; null when it is absent.")
    reason: str | None = Field(description="Why it is unavailable; null when it is available.")


class IngestStartRequest(BaseModel):
    """POST /api/v1/ingest/start's request body (Phase 9).

    Where a capture may run is not chosen here: `interface` must be one the
    operator listed in IDS_LIVE_INTERFACES, and `pcap` a file in
    IDS_LIVE_PCAP_DIR. Anything else is refused with 403 or 404.
    """

    source: Literal["interface", "pcap"]
    interface: str | None = Field(
        default=None, description="An interface listed in IDS_LIVE_INTERFACES."
    )
    pcap: str | None = Field(default=None, description="A file name in IDS_LIVE_PCAP_DIR.")
    mode: Literal["shadow", "alert"] = Field(
        default="shadow",
        description=(
            "shadow scores and alerts no one (the burn-in); alert needs a local "
            "tau_anom calibrated for the serving Stage 2 model."
        ),
    )


class LocalCalibration(BaseModel):
    """The local tau_anom a shadow burn-in produced (training.calibrate_live)."""

    tau_anom_local: float
    tau_anom_dataset: float
    percentile: float
    flows: int = Field(description="Shadow-scored flows the percentile was cut from.")
    computed_at: str
    stage2_version: str
    dataset_threshold_alert_rate: float = Field(
        description="Share of the burn-in the CICIDS2017 tau_anom would have flagged."
    )
    stage1_alert_rate: float = Field(
        description="Share of the burn-in Stage 1 would have named at tau_sup."
    )
    capture_sources: list[str]
    window_start: str
    window_end: str


class IngestStatus(BaseModel):
    """GET /api/v1/ingest/status, and what /ingest/start and /ingest/stop return."""

    running: bool
    mode: str | None
    source: str | None = Field(description="interface:<name> or pcap:<file>; null if never run.")
    session_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    packets: int
    undecoded: int = Field(description="Frames that were not TCP or UDP over IP.")
    flows: int = Field(description="Flows the meter finished.")
    unscoreable: int = Field(
        description="Zero-duration and single-packet flows: infinite rates, never seen in training."
    )
    scored: int
    shadow_rows: int
    alerts: int = Field(description="Flows that raised an alert, before dedupe folds them.")
    tau_anom: float | None = Field(description="The Stage 2 threshold this capture decides at.")
    error: str | None
    allowed_interfaces: list[str] = Field(description="IDS_LIVE_INTERFACES, as configured.")
    pcaps: list[str] = Field(description="Files in IDS_LIVE_PCAP_DIR.")
    tau_anom_dataset: float | None
    calibration: LocalCalibration | None = Field(
        description="The usable local calibration, or null if none matches the serving model."
    )


class ReplayStatus(BaseModel):
    """What POST /api/v1/replay/start and /replay/stop return on 202."""

    running: bool
    speed: int | None = Field(description="Null when no replay is running.")
    dataset: str | None = Field(description="Null when no replay is running.")
    started_at: datetime | None = Field(description="Null when no replay is running.")
    rows_scored: int
    alerts_emitted: int


# ---------------------------------------------------------------------------
# Metrics (GET /api/v1/metrics/*)
#
# Every number in ModelMetrics comes from an offline evaluation artifact and
# none is recomputed from the database -- these describe the held-out day the
# model was measured on, and they must not move when a replay runs.
# ---------------------------------------------------------------------------


class CurvePair(BaseModel):
    """The PR and ROC point series, rendered side by side.

    Both are lists of two-element points: PR is ``(recall, precision)`` and
    ROC is ``(fpr, tpr)``. Neither carries a threshold column, which is why
    GET /metrics/threshold cannot be answered from them and reads the error
    histograms instead.
    """

    pr: list[list[float]] = Field(description="(recall, precision) points.")
    roc: list[list[float]] = Field(description="(fpr, tpr) points.")


class ClassMetrics(BaseModel):
    """Precision, recall, F1 and support for one family."""

    precision: float
    recall: float
    f1: float
    support: int


class ModelMetrics(BaseModel):
    """GET /api/v1/metrics/model.

    ``accuracy`` is present and is deliberately not the headline: on 99%
    benign traffic, always answering benign scores 99%. PR-AUC is the headline
    and the Model Performance screen is required to caption the difference.
    """

    per_class: dict[str, ClassMetrics]
    labels: list[str] = Field(description="Row/column order of the confusion matrix.")
    confusion_matrix: list[list[int]]
    curves: CurvePair
    pr_auc: float | None = Field(description="The headline metric.")
    roc_auc: float | None
    accuracy: float | None = Field(description="Reported, never a headline. See the docstring.")
    tau_sup: float | None = Field(description="The Stage 1 operating threshold.")
    fpr_at_threshold: float | None = Field(description="Measured FPR at tau_sup.")
    alerts_per_analyst_hour: float | None = Field(description="Projected volume at tau_sup.")
    budget: dict[str, Any] = Field(description="The FP budget the thresholds were cut against.")
    stage1_family_recall: dict[str, Any] = Field(description="Per-family recall, Stage 1 alone.")
    stage2_family_recall: dict[str, Any] = Field(description="Per-family recall, Stage 2 alone.")
    stage2_pr_auc: float | None
    loao: dict[str, Any] = Field(
        description="The leave-one-attack-out table: per held-out family, what each stage caught."
    )


class ThresholdProjection(BaseModel):
    """GET /api/v1/metrics/threshold.

    ``t`` is Stage 2's anomaly threshold, not a Stage 1 probability -- see the
    endpoint docstring for why a Stage 1 answer is not computable from what is
    persisted.
    """

    t: float
    fpr: float = Field(description="Benign rows at or above t, over all benign rows.")
    recall: float | None = Field(description="Attack rows at or above t; null without that split.")
    benign_rows: int
    benign_above: int
    attack_rows: int | None
    attack_above: int | None
    false_alerts_per_day: float = Field(description="fpr x IDS_EXPECTED_DAILY_FLOW_VOLUME.")
    alerts_per_analyst_hour: float = Field(description="Per analyst hour: divided by shift length.")
    budget_per_day: int
    target_fpr: float
    within_budget: bool
    covers_distribution: bool = Field(
        description="False when t sits above the histogram's range, so recall is not meaningful."
    )


# ---------------------------------------------------------------------------
# Analytics (GET /api/v1/analytics/*)
#
# Unlike the metrics above, everything here IS computed from the database: it
# describes what this deployment has seen.
# ---------------------------------------------------------------------------


class CountedPair(BaseModel):
    """One ranked row: a value and how often it appears."""

    value: str
    count: int


class TimeBucket(BaseModel):
    """One point of the alerts-over-time series.

    Split into known versus unclassified rather than a single total, because
    the unclassified line is the novel-detection headline and folding it into
    a total would hide the claim this project is making.
    """

    bucket: datetime
    known: int
    unclassified: int


class ThroughputStats(BaseModel):
    """SOC throughput: the panel that argues for the project's existence."""

    opened: int
    resolved: int
    verdicts: int
    true_positives: int
    false_positives: int
    unsure: int
    true_positive_rate: float | None = Field(
        description="TP / (TP + FP). UNSURE is excluded from the denominator, not counted as wrong."
    )
    mean_seconds_to_verdict: float | None


class AnalyticsSummary(BaseModel):
    """GET /api/v1/analytics/summary. No accuracy tile, by design."""

    range: str
    generated_at: datetime
    total_alerts: int
    unclassified_alerts: int
    unclassified_rate: float = Field(description="A candidate hero number; accuracy is not.")
    series: list[TimeBucket]
    families: list[CountedPair]
    top_destination_hosts: list[CountedPair]
    top_destination_ports: list[CountedPair]
    top_source_hosts: list[CountedPair]
    throughput: ThroughputStats


class MitreCoverageRow(BaseModel):
    """One technique on the coverage heatmap, with its hit count."""

    family: str
    technique_id: str
    name: str
    url: str
    means: str = Field(description="The plain-English line an analyst reads instead of a tab.")
    count: int = Field(description="Zero is a real, visible value here -- see the endpoint.")


class MitreCoverage(BaseModel):
    """GET /api/v1/analytics/mitre-coverage.

    ``unclassified_anomalies`` sits beside the table rather than inside it:
    Stage 2 maps to no technique by design, so it has no row, but omitting the
    count would understate exactly the detections this project is proudest of.
    """

    techniques: list[MitreCoverageRow]
    unclassified_anomalies: int


# ---------------------------------------------------------------------------
# Phase 6 -- the contracts the seven screens need that Phase 5 did not carry.
#
# Each one exists because a screen would otherwise have to either fabricate a
# number or reconstruct it from a series of requests that cannot answer it. The
# alternative to adding them was mock data on the dashboard, which is the one
# thing the brief rules out outright.
# ---------------------------------------------------------------------------


class QueueStats(BaseModel):
    """GET /api/v1/alerts/stats -- the thin strip above the triage queue.

    Measured from the database: this is what *this* deployment has seen, not
    what the offline evaluation measured. The projected alert volume and the
    operating threshold beside it on the strip come from
    ``GET /metrics/threshold`` instead, because a projection at a candidate
    threshold is a property of the model, not of the queue.

    There is no accuracy field and there is not going to be one. On traffic
    that is 99% benign, always answering benign scores 99%, and a strip is
    exactly where a number gets read without its caveat.
    """

    generated_at: datetime
    open_alerts: int = Field(description="Alerts still in 'open'; the size of the queue.")
    alerts_last_hour: int = Field(description="Alerts whose detected_at is inside the last hour.")
    observed_alerts_per_hour: float | None = Field(
        description="Alerts divided by the hours actually observed; null before the first alert."
    )
    observed_window_hours: float | None = Field(
        description="Hours between the first and last alert, or null when none exist."
    )
    hosts_affected: int = Field(description="Distinct destination addresses across open alerts.")
    sources_seen: int = Field(description="Distinct source addresses across open alerts.")
    unclassified_open: int = Field(
        description="Open UNCLASSIFIED_ANOMALY alerts -- the detections Stage 1 could not name."
    )
    unjudged_open: int = Field(description="Open alerts with no analyst verdict yet.")


class AlertStatusUpdate(BaseModel):
    """PATCH /api/v1/alerts/status' request body -- the bulk triage action.

    A list rather than one id per request because the queue's action is "bulk
    select, then dismiss": fifty single requests would be fifty transactions
    and fifty chances to half-apply the analyst's one decision.

    ``status`` is the full ``AlertStatus`` vocabulary and not just
    ``dismissed``. Moving a row to ``in_review`` or ``closed`` is the same
    operation on the same column, and an endpoint that only allowed dismissal
    would have to be replaced the first time the queue grew a second action.
    """

    alert_ids: list[int] = Field(min_length=1, max_length=500)
    status: AlertStatus


class AlertStatusResult(BaseModel):
    """What PATCH /api/v1/alerts/status returns.

    ``missing`` is reported rather than raising: a bulk action against a queue
    the replay is still writing to can legitimately name a row that has since
    been deleted, and failing the whole batch for one stale id would throw away
    the forty-nine decisions that were fine. The caller is told exactly which
    ids did not land.
    """

    status: AlertStatus
    updated: list[int]
    missing: list[int]


class ErrorDistribution(BaseModel):
    """One of Stage 2's persisted reconstruction-error distributions."""

    name: str = Field(description="validation_benign, test_benign or test_attack.")
    rows: int
    counts: list[int] = Field(description="One count per bin; len(edges) - 1 of them.")
    percentiles: dict[str, float] = Field(description="Reference quantiles for the axis labels.")


class AnomalyHistogram(BaseModel):
    """GET /api/v1/metrics/anomaly-histogram -- the axis the slider is drawn on.

    The Live Traffic Monitor draws the threshold as a draggable line *across a
    histogram*, so it needs the bins themselves and not only a projection at
    one point. ``GET /metrics/threshold`` answers "what happens at t"; it
    cannot answer "what does the distribution look like", and reconstructing
    the shape from sixty calls to it would be sixty requests to redraw one
    chart.

    Every distribution here shares ``edges``, which is what makes the benign
    and attack curves comparable on one axis -- see
    ``MetricsStore.error_histogram``.
    """

    edges: list[float]
    spacing: str = Field(description="'log' or 'linear'; the axis scale the bins were cut on.")
    tau_anom: float | None = Field(description="The operating threshold the line starts at.")
    budget_tau: float | None = Field(
        description="Where the threshold would sit if cut to the false-positive budget instead."
    )
    distributions: list[ErrorDistribution]


class FeedbackVersionRow(BaseModel):
    """Verdicts recorded against one model version."""

    model_config = ConfigDict(protected_namespaces=())

    model_version: str | None
    verdicts: int
    true_positives: int
    false_positives: int
    unsure: int


class FeedbackLoop(BaseModel):
    """GET /api/v1/analytics/feedback -- the Feedback Loop screen.

    ``labels_pending_retrain`` counts ``analyst_verdicts`` rows whose
    ``consumed_at`` is still null. That column exists for exactly this
    question, which is why "since the last retrain" is answerable without a
    second table.

    ``disagreement_rate`` is FP / (TP + FP): the share of judged alerts where
    the analyst overruled the model. UNSURE is outside the denominator, the
    same way it is in ``ThroughputStats`` -- it is not a judgement that the
    model was wrong, and counting it as one would drag the rate up every time
    someone was honest about not knowing.
    """

    model_config = ConfigDict(protected_namespaces=())

    generated_at: datetime
    serving_model_version: str = Field(description="The bundle currently loaded and scoring.")
    total_alerts: int
    judged_alerts: int = Field(description="Distinct alerts carrying at least one verdict.")
    judged_share: float = Field(description="judged_alerts / total_alerts; 0.0 with no alerts.")
    labels_total: int
    labels_pending_retrain: int = Field(description="Verdicts with consumed_at IS NULL.")
    labels_consumed: int
    true_positives: int
    false_positives: int
    unsure: int
    disagreement_rate: float | None = Field(description="FP / (TP + FP); null with no decision.")
    mean_seconds_to_verdict: float | None
    by_model_version: list[FeedbackVersionRow]
    retrain_available: bool = Field(
        description="False until Phase 7 ships the challenger pipeline; the button says so."
    )
    retrain_phase: str = Field(description="The phase that makes retraining callable.")


# ---------------------------------------------------------------------------
# Phase 7 -- drift, the model registry, and retraining
# ---------------------------------------------------------------------------
DriftBand = Literal["stable", "moderate", "significant"]
RetrainStatus = Literal["requested", "running", "completed", "failed", "cancelled"]
ModelStage = Literal["champion", "challenger", "archived"]


class DriftFeatureScore(BaseModel):
    """One feature's PSI inside one snapshot."""

    feature: str
    psi: float
    band: DriftBand
    expected: list[float] | None = Field(
        default=None, description="The reference share per bin. The 'expected' half of the PSI."
    )
    actual: list[float] | None = Field(
        default=None, description="The observed share per bin, on the reference's own edges."
    )


class DriftSnapshot(BaseModel):
    """One nightly run: what it observed and what it concluded.

    ``features`` is empty on a run that declined to score -- when the window held
    fewer rows than ``IDS_DRIFT_MIN_ROWS``. That is reported as a run with a note
    rather than omitted, because "we looked and there was not enough traffic" and
    "we did not look" are different facts and the screen has to be able to tell
    them apart.
    """

    model_config = ConfigDict(protected_namespaces=())

    id: int
    computed_at: datetime
    model_version: str
    observed_from: datetime
    observed_to: datetime
    rows_observed: int
    reference: str | None
    reference_rows: int | None
    features_scored: int
    max_psi: float
    moderate_count: int
    significant_count: int
    retrain_recommended: bool = Field(
        description="True once any single feature crosses 0.25. Any feature, not the mean."
    )
    score_histogram: dict[str, Any] | None = Field(
        description="Observed reconstruction errors, binned on the training baseline's edges."
    )
    notes: str | None
    features: list[DriftFeatureScore]


class DriftSeriesPoint(BaseModel):
    """One feature's PSI at one point in time."""

    computed_at: datetime
    psi: float
    band: DriftBand


class DriftSeries(BaseModel):
    """One feature's PSI across every snapshot -- what the screen plots."""

    feature: str
    worst_psi: float
    latest_psi: float
    latest_band: DriftBand
    points: list[DriftSeriesPoint]


class DriftResponse(BaseModel):
    """GET /api/v1/metrics/drift.

    Carries the latest snapshot in full plus a per-feature series across
    snapshots, because the screen needs both: the bands say what is true now, and
    the series says whether it has been true for a week or started last night.

    ``baseline`` is the training benign score distribution the observed one is
    overlaid on. Both are on the same bin edges -- two curves on two axes separate
    for reasons that have nothing to do with drift.
    """

    latest: DriftSnapshot | None = Field(description="Null until the first run.")
    series: list[DriftSeries] = Field(description="Worst feature first.")
    snapshots: int = Field(description="How many runs are stored.")
    baseline: dict[str, Any] | None = Field(
        description="The training benign error distribution, on shared edges."
    )
    moderate_threshold: float
    significant_threshold: float
    sampled_rows: int = Field(description="Rows currently in the sample table.")
    sample_stride: int = Field(description="One flow in this many is kept for drift.")


class RegistryEntryResponse(BaseModel):
    """One model version, with the decisions it is responsible for.

    ``alerts_scored`` is the audit number: it is what somebody needs after an
    incident, when the question is how many decisions a model that turned out to
    be wrong was behind.
    """

    model_config = ConfigDict(protected_namespaces=())

    version: str
    stage: ModelStage
    is_active: bool
    supervised_algorithm: str | None
    anomaly_algorithm: str | None
    trained_at: datetime | None
    trained_on: str | None
    schema_hash: str | None
    tau_sup: float | None
    tau_anom: float | None
    metrics: dict[str, Any]
    notes: str | None
    alerts_scored: int
    verdicts_recorded: int
    first_alert_at: datetime | None
    last_alert_at: datetime | None


class ModelRegistry(BaseModel):
    """GET /api/v1/models. Champion first."""

    model_config = ConfigDict(protected_namespaces=())

    serving: str = Field(description="The version this process has loaded.")
    versions: list[RegistryEntryResponse]


class RetrainRequest(BaseModel):
    """POST /api/v1/retrain's body."""

    requested_by: str | None = Field(default=None, description="Who asked.")


class Stage2RefitSummary(BaseModel):
    """What a retrain did to the autoencoder's benign baseline.

    ``attempted`` false is the guard working, not a failure: the pool of
    analyst-confirmed false positives was refused (too few rows, or too much of
    it from one host), and ``decision`` says which. When it was attempted, both
    PR-AUCs are measured on one held-out set -- the validation day plus a slice
    of the pool withheld from the fit -- each model at its own threshold.
    """

    model_config = ConfigDict(protected_namespaces=())

    attempted: bool
    promoted: bool
    decision: str
    pool_admitted: int = 0
    pool_candidates: int = 0
    pool_hosts: int = 0
    pool_refused_by_cap: int = 0
    champion_version: str | None = None
    challenger_version: str | None = None
    champion_pr_auc: float | None = None
    challenger_pr_auc: float | None = None
    champion_pool_fpr: float | None = Field(
        default=None, description="Held-out confirmed-benign rows the champion still flags."
    )
    challenger_pool_fpr: float | None = None
    champion_attack_recall: float | None = None
    challenger_attack_recall: float | None = None


class RetrainRunResponse(BaseModel):
    """One retraining run, requested or finished.

    A run that was not promoted is the more interesting row of the two: it is the
    evidence that the gate works. ``champion_pr_auc`` and ``challenger_pr_auc``
    are both measured on ``held_out_split`` inside the same run, so the comparison
    is between two models rather than between two evaluations.
    """

    model_config = ConfigDict(protected_namespaces=())

    id: int
    status: RetrainStatus
    requested_by: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    labels_consumed: int
    false_positives_consumed: int
    true_positives_consumed: int
    champion_version: str | None
    challenger_version: str | None
    champion_pr_auc: float | None
    challenger_pr_auc: float | None
    held_out_split: str | None
    promoted: bool
    decision: str | None
    error: str | None
    # Stage 2's half of the run; null for a run that has not finished or that
    # predates the refit.
    stage2: Stage2RefitSummary | None = None


class RetrainStatusResponse(BaseModel):
    """GET /api/v1/retrain. What has been asked for and what came of it."""

    runs: list[RetrainRunResponse]
    pending: int = Field(description="Requests no worker has claimed yet.")
    worker_hint: str = Field(
        description="How a pending request gets executed. The API never fits a model."
    )
