"""SQLAlchemy models.

Portability rules enforced here (Phase 0: "Postgres-compatible types only"):

* Autoincrement keys are ``BigInteger`` with an ``Integer`` variant for
  SQLite, because SQLite only auto-assigns rowids for ``INTEGER PRIMARY KEY``.
* Enumerations are ``String`` + ``CheckConstraint``, not native DB enums --
  portable, and changing the allowed set stays an ordinary migration.
* Timestamps are ``DateTime(timezone=True)``. SQLite stores them naively, so
  the application layer is responsible for always handing over tz-aware
  values.
* Structured payloads use the generic ``JSON`` type, which maps to ``json`` on
  Postgres and ``TEXT`` on SQLite.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Portable autoincrementing primary key type.
PK = BigInteger().with_variant(Integer, "sqlite")

# ---------------------------------------------------------------------------
# Controlled vocabularies. Mirrored in app/schemas.py for the wire contract.
# ---------------------------------------------------------------------------
ALERT_KINDS = ("KNOWN", "UNCLASSIFIED_ANOMALY")
ALERT_FAMILIES = (
    "dos",
    "ddos",
    "brute_force",
    "port_scan",
    "web_attack",
    "botnet",
    "infiltration",
)
SEVERITIES = ("low", "medium", "high", "critical")
ALERT_STATUSES = ("open", "in_review", "closed", "dismissed")
VERDICTS = ("TP", "FP", "UNSURE")
MODEL_STAGES = ("champion", "challenger", "archived")
ALERT_SOURCES = ("replay", "live", "api")
DETECTION_STAGES = ("stage1_supervised", "stage2_anomaly")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    """Render a portable ``col IN (...)`` check expression."""
    rendered = ", ".join("'" + value + "'" for value in values)
    return column + " IN (" + rendered + ")"


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Alert(TimestampMixin, Base):
    """One deduplicated detection.

    A burst of 5,000 flows from one compromised host collapses into a single
    row here, with ``occurrence_count`` incremented instead of a new insert
    (see ``app/dedupe.py``).
    """

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)

    # ---- Detection -----------------------------------------------------
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detection_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)

    # Queue ordering key. Never the timestamp -- analysts work by risk.
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    # Stage 1 max attack-class probability; null for pure anomaly alerts.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Stage 2 mean reconstruction error; null for confident known attacks.
    anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- Flow identity -------------------------------------------------
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    src_ip: Mapped[str] = mapped_column(String(45), nullable=False)  # INET6 text length
    src_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dst_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    dst_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ---- Enrichment ----------------------------------------------------
    asset_criticality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    host_prior_alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mitre_technique: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ---- Explanation ---------------------------------------------------
    # Top-5 TreeSHAP contributors (Stage 1) or top-5 per-feature
    # reconstruction errors (Stage 2). An alert with a score and no reason is
    # an alert an analyst ignores, so the pipeline always populates this.
    explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_actions: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_flow: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # ---- Dedupe --------------------------------------------------------
    # (src_host, alert_class, floor(ts, dedupe_window_seconds))
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # ---- Triage state --------------------------------------------------
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")

    # ---- Provenance / audit --------------------------------------------
    # Which model version produced this alert. Non-negotiable for anything
    # security-adjacent (Phase 7 audit trail).
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="replay")

    # Ground truth exists only for replayed dataset rows, and the UI badges it
    # as demo-only. Always null for live capture.
    ground_truth_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    verdicts: Mapped[list[AnalystVerdict]] = relationship(
        back_populates="alert",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(_one_of("kind", ALERT_KINDS), name="kind_valid"),
        CheckConstraint(_one_of("severity", SEVERITIES), name="severity_valid"),
        CheckConstraint(_one_of("status", ALERT_STATUSES), name="status_valid"),
        CheckConstraint(_one_of("source", ALERT_SOURCES), name="source_valid"),
        CheckConstraint(
            _one_of("detection_stage", DETECTION_STAGES),
            name="detection_stage_valid",
        ),
        CheckConstraint("occurrence_count >= 1", name="occurrence_count_positive"),
        # An UNCLASSIFIED_ANOMALY has, by definition, no family label.
        CheckConstraint(
            "(kind = 'UNCLASSIFIED_ANOMALY' AND family IS NULL)"
            " OR (kind = 'KNOWN' AND family IS NOT NULL)",
            name="family_matches_kind",
        ),
        # Dedupe lookup: one row per key inside the active window.
        Index("ix_alerts_dedupe_key_last_seen", "dedupe_key", "last_seen"),
        # The triage queue: open alerts, highest risk first.
        Index("ix_alerts_status_risk_score", "status", "risk_score"),
        # "Other alerts from this source in the last 24h".
        Index("ix_alerts_src_ip_detected_at", "src_ip", "detected_at"),
        # The one-click UNCLASSIFIED_ANOMALY filter chip.
        Index("ix_alerts_kind_detected_at", "kind", "detected_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Alert id={self.id} kind={self.kind} family={self.family} risk={self.risk_score}>"


class AnalystVerdict(Base):
    """An analyst's judgement on an alert -- the input to active learning."""

    __tablename__ = "analyst_verdicts"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(
        PK, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyst: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Audit: the model version being judged, captured at verdict time.
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Set once a retrain has consumed this label, so the feedback screen can
    # count "new labels since last retrain".
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    alert: Mapped[Alert] = relationship(back_populates="verdicts")

    __table_args__ = (
        CheckConstraint(_one_of("verdict", VERDICTS), name="verdict_valid"),
        Index("ix_analyst_verdicts_created_at_verdict", "created_at", "verdict"),
    )


class ModelVersion(TimestampMixin, Base):
    """Model registry: what was trained, when, and what it scored.

    Backs ``GET /api/v1/models`` and champion/challenger promotion in Phase 7.
    Thresholds are recorded here as well as in the artifact bundle, so an old
    alert can be re-read against the exact threshold that produced it.
    """

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="challenger")

    supervised_algorithm: Mapped[str | None] = mapped_column(String(64), nullable=True)
    anomaly_algorithm: Mapped[str | None] = mapped_column(String(64), nullable=True)

    trained_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trained_on: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Guards train/serve skew: the API refuses to start when the artifact
    # bundle's hash disagrees with the feature module's.
    schema_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)

    tau_sup: Mapped[float | None] = mapped_column(Float, nullable=True)
    tau_anom: Mapped[float | None] = mapped_column(Float, nullable=True)

    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (CheckConstraint(_one_of("stage", MODEL_STAGES), name="stage_valid"),)
