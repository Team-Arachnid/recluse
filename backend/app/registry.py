"""Phase 7 -- the model registry, and the audit trail it makes readable.

"Which model version scored which alert" is non-negotiable for anything
security-adjacent, and the answer has existed since Phase 5: ``alerts.model_version``
and ``analyst_verdicts.model_version`` are both populated on write. What was
missing is a place to ask the question. A column nobody can query is not an audit
trail; it is a column.

So this module keeps ``model_versions`` in step with what is actually on disk and
joins it to the alert and verdict counts. The registry answers three things a
reviewer asks in order:

1. What is serving right now, and since when.
2. What was serving before it, and why it was replaced.
3. How many decisions each version is responsible for -- which is the number that
   matters when a model turns out to have been wrong.

The registry is written at startup from the loaded bundle, not by the training
run. The training run knows what it produced; only the serving process knows what
is actually loaded, and those are the same thing right up until somebody copies a
file by hand.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Alert, AnalystVerdict, ModelVersion

logger = logging.getLogger(__name__)

STAGE_CHAMPION = "champion"
STAGE_CHALLENGER = "challenger"
STAGE_ARCHIVED = "archived"


def _parse_trained_at(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def register_champion(session: Session, bundle: Any) -> ModelVersion | None:
    """Record the loaded bundle as the active champion. Idempotent.

    Called once per process start. Re-registering the same version updates its
    row rather than inserting a second one, so a restart does not grow the
    registry -- and a version that genuinely is new demotes whatever was champion
    to ``archived`` rather than leaving two rows claiming to be serving.

    Returns None when nothing is loaded. An unloaded process has no champion, and
    inventing a registry row for it would make the audit trail claim a model was
    serving when none was.
    """
    version = getattr(bundle, "version", None)
    if not version or version == "unloaded":
        return None

    card: dict[str, Any] = getattr(bundle, "model_card", None) or {}
    thresholds = card.get("thresholds") or {}

    existing = session.execute(
        select(ModelVersion).where(ModelVersion.version == version)
    ).scalar_one_or_none()

    record = existing or ModelVersion(version=version)
    record.stage = STAGE_CHAMPION
    record.is_active = True
    record.supervised_algorithm = card.get("algorithm")
    record.anomaly_algorithm = "autoencoder" if getattr(bundle, "stage2_ready", False) else None
    record.trained_at = _parse_trained_at(card.get("trained_at"))
    record.trained_on = card.get("trained_on")
    record.schema_hash = card.get("schema_hash") or getattr(bundle, "schema_hash", None)
    record.tau_sup = thresholds.get("tau_sup", getattr(bundle, "tau_sup", None))
    record.tau_anom = thresholds.get("tau_anom", getattr(bundle, "tau_anom", None))
    record.metrics = card.get("validation") or {}
    record.notes = card.get("promoted_by") or None

    if existing is None:
        session.add(record)

    # Anything else claiming to be the active champion is now the previous one.
    # Two active champions would make "which model scored this alert" answerable
    # two different ways, which is the one thing an audit trail may not do.
    demoted = 0
    for other in session.execute(
        select(ModelVersion).where(ModelVersion.version != version)
    ).scalars():
        if other.is_active or other.stage == STAGE_CHAMPION:
            other.is_active = False
            other.stage = STAGE_ARCHIVED
            demoted += 1

    session.flush()
    if demoted:
        logger.info("registered champion %s, archived %s previous version(s)", version, demoted)
    return record


@dataclass
class RegistryEntry:
    """One registry row, with the decisions it is responsible for."""

    version: str
    stage: str
    is_active: bool
    supervised_algorithm: str | None
    anomaly_algorithm: str | None
    trained_at: dt.datetime | None
    trained_on: str | None
    schema_hash: str | None
    tau_sup: float | None
    tau_anom: float | None
    metrics: dict[str, Any]
    notes: str | None
    alerts_scored: int
    verdicts_recorded: int
    first_alert_at: dt.datetime | None
    last_alert_at: dt.datetime | None


def read_registry(session: Session) -> list[RegistryEntry]:
    """Every known version, champion first, with its alert and verdict counts.

    The counts come from a group-by on the alert and verdict tables rather than
    from a column kept in step by hand, because a denormalised counter is a
    counter that eventually disagrees with the rows it counts -- and this is the
    table somebody reads after an incident.

    A version that appears on alerts but has no registry row is still listed. That
    happens when a bundle scored traffic before this module existed, or when
    somebody swapped a file without restarting, and the honest answer to "what
    scored these alerts" is the version string the rows carry.
    """
    counts = {
        str(version): (int(total), earliest, latest)
        for version, total, earliest, latest in session.execute(
            select(
                Alert.model_version,
                func.count(),
                func.min(Alert.detected_at),
                func.max(Alert.detected_at),
            ).group_by(Alert.model_version)
        ).all()
    }
    verdicts = {
        str(version): int(total)
        for version, total in session.execute(
            select(AnalystVerdict.model_version, func.count())
            .where(AnalystVerdict.model_version.is_not(None))
            .group_by(AnalystVerdict.model_version)
        ).all()
    }

    rows = list(session.execute(select(ModelVersion)).scalars().all())
    known = {row.version for row in rows}

    entries = [
        RegistryEntry(
            version=row.version,
            stage=row.stage,
            is_active=bool(row.is_active),
            supervised_algorithm=row.supervised_algorithm,
            anomaly_algorithm=row.anomaly_algorithm,
            trained_at=row.trained_at,
            trained_on=row.trained_on,
            schema_hash=row.schema_hash,
            tau_sup=row.tau_sup,
            tau_anom=row.tau_anom,
            metrics=row.metrics or {},
            notes=row.notes,
            alerts_scored=counts.get(row.version, (0, None, None))[0],
            verdicts_recorded=verdicts.get(row.version, 0),
            first_alert_at=counts.get(row.version, (0, None, None))[1],
            last_alert_at=counts.get(row.version, (0, None, None))[2],
        )
        for row in rows
    ]

    for version, (total, earliest, latest) in counts.items():
        if version in known:
            continue
        entries.append(
            RegistryEntry(
                version=version,
                stage=STAGE_ARCHIVED,
                is_active=False,
                supervised_algorithm=None,
                anomaly_algorithm=None,
                trained_at=None,
                trained_on=None,
                schema_hash=None,
                tau_sup=None,
                tau_anom=None,
                metrics={},
                notes=(
                    "Scored alerts but has no registry row -- it was serving before "
                    "the registry existed, or an artifact was swapped without a restart."
                ),
                alerts_scored=total,
                verdicts_recorded=verdicts.get(version, 0),
                first_alert_at=earliest,
                last_alert_at=latest,
            )
        )

    # Champion first, then the most recent scorer first, then by name so the
    # order is total rather than merely mostly-defined. A version that never
    # scored anything sorts after every one that did. The timestamp is compared
    # as a UTC instant rather than as text: SQLite hands these back naive, and a
    # naive and an aware rendering of the same moment do not sort as equals.
    def order(entry: RegistryEntry) -> tuple[int, int, float, str]:
        last = entry.last_alert_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=dt.UTC)
        return (
            0 if entry.is_active else 1,
            0 if last is not None else 1,
            -last.timestamp() if last is not None else 0.0,
            entry.version,
        )

    return sorted(entries, key=order)
