"""The ORM must stay swappable between SQLite and Postgres.

Phase 0 requires Postgres-compatible types only, so that moving off SQLite is
a change to `IDS_DATABASE_URL` and nothing else. A type that only SQLite
understands would not surface until the day of the swap, so it is asserted
here instead.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from app.db import Base
from app.models import Alert, AnalystVerdict, ModelVersion


def test_every_column_type_compiles_for_postgres() -> None:
    pg = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            # Raises UnsupportedCompilationError for SQLite-only types.
            column.type.compile(dialect=pg)


def test_every_column_type_compiles_for_sqlite() -> None:
    lite = sqlite.dialect()

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            column.type.compile(dialect=lite)


def test_primary_keys_are_bigint_on_postgres_and_integer_on_sqlite() -> None:
    """SQLite only auto-assigns rowids for INTEGER PRIMARY KEY.

    A plain BigInteger key therefore fails to autoincrement on SQLite, which
    is why the models use an explicit dialect variant.
    """
    pk = Alert.__table__.c.id.type

    assert pk.compile(dialect=postgresql.dialect()) == "BIGINT"
    assert pk.compile(dialect=sqlite.dialect()) == "INTEGER"


def test_no_native_enum_types_are_used() -> None:
    """Native enums need a dedicated migration dance on Postgres.

    Vocabularies are String + CheckConstraint instead, which both backends
    treat identically.
    """
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            assert column.type.__class__.__name__ != "Enum", (
                f"{table.name}.{column.name} uses a native enum"
            )


def test_no_sqlite_specific_table_options() -> None:
    for table in Base.metadata.sorted_tables:
        sqlite_kwargs = [key for key in table.kwargs if key.startswith("sqlite_")]
        assert not sqlite_kwargs, f"{table.name} carries {sqlite_kwargs}"


def test_constraint_names_are_deterministic() -> None:
    """Unnamed constraints get different names per backend and cannot be
    ALTERed on SQLite, so the naming convention must actually apply."""
    for table in Base.metadata.sorted_tables:
        for constraint in table.constraints:
            assert constraint.name, f"{table.name} has an unnamed constraint"
            assert not str(constraint.name).startswith("_unnamed_")


# ---------------------------------------------------------------------------
# The check constraints are load-bearing, not decoration.
# ---------------------------------------------------------------------------
def _minimal_alert(**overrides: object) -> Alert:
    import datetime as dt

    now = dt.datetime.now(dt.UTC)
    fields: dict[str, object] = {
        "kind": "KNOWN",
        "family": "port_scan",
        "detection_stage": "stage1_supervised",
        "severity": "high",
        "risk_score": 0.91,
        "detected_at": now,
        "src_ip": "10.0.0.5",
        "dst_ip": "10.0.0.9",
        "dedupe_key": "10.0.0.5|port_scan|0",
        "first_seen": now,
        "last_seen": now,
        "model_version": "unloaded",
    }
    fields.update(overrides)
    return Alert(**fields)  # type: ignore[arg-type]


def test_a_well_formed_alert_persists(db_session) -> None:
    db_session.add(_minimal_alert())
    db_session.commit()

    assert db_session.query(Alert).count() == 1


def test_unclassified_anomaly_cannot_carry_a_family(db_session) -> None:
    """Stage 2 alerts have no family label -- that is the entire point."""
    db_session.add(_minimal_alert(kind="UNCLASSIFIED_ANOMALY", family="botnet"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_a_known_alert_must_carry_a_family(db_session) -> None:
    db_session.add(_minimal_alert(kind="KNOWN", family=None))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_severity_vocabulary_is_enforced(db_session) -> None:
    db_session.add(_minimal_alert(severity="catastrophic"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_verdict_vocabulary_is_enforced(db_session) -> None:
    alert = _minimal_alert()
    db_session.add(alert)
    db_session.commit()

    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="MAYBE"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_model_version_is_unique(db_session) -> None:
    db_session.add(ModelVersion(version="rf-1"))
    db_session.commit()

    db_session.add(ModelVersion(version="rf-1"))
    with pytest.raises(IntegrityError):
        db_session.commit()
