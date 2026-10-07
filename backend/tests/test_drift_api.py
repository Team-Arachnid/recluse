"""Phase 7 -- GET /metrics/drift, GET /models, and the retrain request endpoints.

These assert the three things the endpoints are for, beyond returning data: that
an empty drift history is reported as empty rather than as zero, that the registry
carries the alert counts that make the audit trail answerable, and that the API
never fits a model.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import create_app
from app.models import (
    Alert,
    AnalystVerdict,
    DriftFeature,
    DriftRun,
    FlowSample,
    ModelVersion,
    RetrainRun,
)
from app.registry import read_registry, register_champion

NOW = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Thread-shareable in-memory database -- see tests/test_alerts.py."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def api(db_session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _alert(session, *, index: int, version: str = "v1") -> Alert:
    alert = Alert(
        kind="KNOWN",
        family="dos",
        detection_stage="stage1_supervised",
        severity="high",
        risk_score=0.5,
        confidence=0.9,
        anomaly_score=None,
        detected_at=NOW + dt.timedelta(seconds=index),
        src_ip="10.0.0.1",
        dst_ip="192.168.10.50",
        dst_port=80,
        asset_criticality="high",
        host_prior_alert_count=0,
        mitre_technique="T1499",
        explanation={"explainer": "treeshap", "contributors": []},
        narrative="A sentence.",
        recommended_actions={
            "technique": None,
            "has_playbook": True,
            "summary": "s",
            "actions": [],
        },
        raw_flow={"flow_duration": float(index)},
        dedupe_key=f"key-{index}-{version}",
        occurrence_count=1,
        first_seen=NOW,
        last_seen=NOW,
        status="open",
        model_version=version,
        source="replay",
    )
    session.add(alert)
    session.commit()
    return alert


def _run(session, *, psi: float, band: str, when: dt.datetime) -> DriftRun:
    run = DriftRun(
        computed_at=when,
        model_version="v1",
        observed_from=when - dt.timedelta(hours=1),
        observed_to=when,
        rows_observed=500,
        reference="train.parquet",
        reference_rows=60_000,
        features_scored=2,
        max_psi=psi,
        moderate_count=0 if band != "moderate" else 1,
        significant_count=1 if band == "significant" else 0,
        retrain_recommended=band == "significant",
        score_histogram={"edges": [0.1, 1.0], "shares": [1.0], "rows": 500},
    )
    session.add(run)
    session.flush()
    session.add(
        DriftFeature(
            run_id=run.id,
            feature="flow_iat_std",
            psi=psi,
            band=band,
            expected=[0.5, 0.5],
            actual=[0.9, 0.1],
        )
    )
    session.add(
        DriftFeature(
            run_id=run.id, feature="idle_min", psi=0.01, band="stable", expected=None, actual=None
        )
    )
    session.commit()
    return run


# ---------------------------------------------------------------------------
# GET /metrics/drift
# ---------------------------------------------------------------------------


def test_drift_is_no_longer_501(api, api_prefix: str) -> None:
    response = api.get(f"{api_prefix}/metrics/drift")

    assert response.status_code == 200, response.text


def test_no_snapshot_is_reported_as_none_not_as_zero(api, api_prefix: str) -> None:
    """The one lie this endpoint must not tell.

    A drift response with a zeroed snapshot would read as "measured, and nothing
    has moved", which is the opposite of "not measured".
    """
    body = api.get(f"{api_prefix}/metrics/drift").json()

    assert body["latest"] is None
    assert body["series"] == []
    assert body["snapshots"] == 0
    assert body["moderate_threshold"] == 0.1
    assert body["significant_threshold"] == 0.25


def test_the_latest_snapshot_is_served_worst_feature_first(
    api, db_session, api_prefix: str
) -> None:
    _run(db_session, psi=1.5, band="significant", when=NOW)

    body = api.get(f"{api_prefix}/metrics/drift").json()

    assert body["snapshots"] == 1
    assert body["latest"]["retrain_recommended"] is True
    assert [entry["feature"] for entry in body["latest"]["features"]] == [
        "flow_iat_std",
        "idle_min",
    ]
    # Both share vectors travel with the score, so a reader can see why it is what
    # it is. A PSI with no bins behind it is a number nobody can act on.
    assert body["latest"]["features"][0]["expected"] == [0.5, 0.5]
    assert body["latest"]["features"][0]["actual"] == [0.9, 0.1]


def test_the_series_is_oldest_first_so_a_chart_reads_left_to_right(
    api, db_session, api_prefix: str
) -> None:
    _run(db_session, psi=0.4, band="significant", when=NOW - dt.timedelta(days=1))
    _run(db_session, psi=1.5, band="significant", when=NOW)

    body = api.get(f"{api_prefix}/metrics/drift").json()

    worst = body["series"][0]
    assert worst["feature"] == "flow_iat_std"
    assert [point["psi"] for point in worst["points"]] == [0.4, 1.5]
    assert worst["latest_psi"] == 1.5
    assert worst["worst_psi"] == 1.5


def test_the_series_is_ranked_by_the_worst_a_feature_ever_scored(
    api, db_session, api_prefix: str
) -> None:
    """Ranked on the worst rather than the latest, so a feature that spiked and
    settled does not drop off the chart that is meant to show it spiked."""
    _run(db_session, psi=2.0, band="significant", when=NOW)

    features = [
        entry["feature"] for entry in api.get(f"{api_prefix}/metrics/drift").json()["series"]
    ]

    assert features == ["flow_iat_std", "idle_min"]


def test_the_sample_table_size_is_reported(api, db_session, api_prefix: str) -> None:
    """So an empty drift screen can say whether there is traffic to measure yet."""
    for index in range(3):
        db_session.add(
            FlowSample(
                scored_at=NOW,
                model_version="v1",
                source="replay",
                anomaly_score=0.1,
                alerted=False,
                raw_flow={"flow_duration": float(index)},
            )
        )
    db_session.commit()

    body = api.get(f"{api_prefix}/metrics/drift").json()

    assert body["sampled_rows"] == 3
    assert body["sample_stride"] >= 1


def test_the_snapshot_limit_is_honoured(api, db_session, api_prefix: str) -> None:
    for day in range(4):
        _run(db_session, psi=0.3, band="significant", when=NOW - dt.timedelta(days=day))

    body = api.get(f"{api_prefix}/metrics/drift", params={"snapshots": 2}).json()

    assert body["snapshots"] == 4, "the total is reported even when the series is trimmed"
    assert len(body["series"][0]["points"]) == 2


# ---------------------------------------------------------------------------
# GET /models
# ---------------------------------------------------------------------------


def test_the_registry_is_no_longer_501(api, api_prefix: str) -> None:
    response = api.get(f"{api_prefix}/models")

    assert response.status_code == 200, response.text
    assert "serving" in response.json()


def test_the_registry_counts_the_alerts_each_version_scored(
    api, db_session, api_prefix: str
) -> None:
    """The audit column. This is the number somebody needs when a model turns out
    to have been wrong: how many decisions it was behind."""
    for index in range(3):
        _alert(db_session, index=index, version="v1")
    judged = _alert(db_session, index=9, version="v2")
    db_session.add(AnalystVerdict(alert_id=judged.id, verdict="TP", model_version="v2"))
    db_session.commit()

    versions = {
        entry["version"]: entry for entry in api.get(f"{api_prefix}/models").json()["versions"]
    }

    assert versions["v1"]["alerts_scored"] == 3
    assert versions["v2"]["alerts_scored"] == 1
    assert versions["v2"]["verdicts_recorded"] == 1


def test_a_version_that_scored_alerts_but_has_no_row_is_still_listed(
    api, db_session, api_prefix: str
) -> None:
    """ "What scored these alerts" has an answer even when the registry does not
    know the version -- somebody swapped an artifact, or it predates the registry.
    The honest answer is the string the rows carry."""
    _alert(db_session, index=1, version="mystery-model")

    entry = next(
        row
        for row in api.get(f"{api_prefix}/models").json()["versions"]
        if row["version"] == "mystery-model"
    )

    assert entry["alerts_scored"] == 1
    assert entry["stage"] == "archived"
    assert "no registry row" in (entry["notes"] or "")


def test_registering_a_champion_twice_does_not_grow_the_registry(db_session) -> None:
    class Bundle:
        version = "v9"
        schema_hash = "sha256:abc"
        tau_sup = 0.4
        tau_anom = 0.1
        stage2_ready = True
        model_card = {"algorithm": "lgbm", "trained_at": "2026-09-28T14:10:01+00:00"}

    register_champion(db_session, Bundle())
    register_champion(db_session, Bundle())
    db_session.commit()

    rows = list(db_session.execute(select(ModelVersion)).scalars().all())
    assert len(rows) == 1
    assert rows[0].stage == "champion"
    assert rows[0].is_active is True


def test_a_new_champion_archives_the_previous_one(db_session) -> None:
    """Two active champions would make "which model scored this alert" answerable
    two different ways, which is the one thing an audit trail may not do."""

    class Bundle:
        def __init__(self, version: str) -> None:
            self.version = version
            self.schema_hash = "sha256:abc"
            self.tau_sup = 0.4
            self.tau_anom = 0.1
            self.stage2_ready = True
            self.model_card = {"algorithm": "lgbm"}

    register_champion(db_session, Bundle("v1"))
    register_champion(db_session, Bundle("v2"))
    db_session.commit()

    rows = {row.version: row for row in db_session.execute(select(ModelVersion)).scalars()}
    assert rows["v2"].is_active is True
    assert rows["v1"].is_active is False
    assert rows["v1"].stage == "archived"
    assert sum(1 for row in rows.values() if row.is_active) == 1


def test_archived_versions_are_listed_most_recent_scorer_first(db_session) -> None:
    """Champion first, then whichever archived version scored most recently --
    the one somebody investigating "what was serving last week" wants on top. A
    version that never scored anything goes last rather than first."""
    for version, hours in (("old", 48), ("newer", 2), ("never", None)):
        db_session.add(ModelVersion(version=version, stage="archived", is_active=False))
        if hours is not None:
            alert = _alert(db_session, index=hours, version=version)
            alert.detected_at = NOW - dt.timedelta(hours=hours)
    db_session.add(ModelVersion(version="serving", stage="champion", is_active=True))
    db_session.commit()

    order = [entry.version for entry in read_registry(db_session)]

    assert order == ["serving", "newer", "old", "never"]


def test_an_unloaded_bundle_registers_nothing(db_session) -> None:
    """Inventing a row for a process with no model would make the audit trail
    claim a model was serving when none was."""

    class Bundle:
        version = "unloaded"
        model_card: dict = {}

    assert register_champion(db_session, Bundle()) is None
    assert read_registry(db_session) == []


# ---------------------------------------------------------------------------
# POST /retrain -- and the thing it does not do
# ---------------------------------------------------------------------------


def test_a_retrain_request_is_202_and_fits_nothing(api, db_session, api_prefix: str) -> None:
    """202, not 200. The work outlives the request, and a 200 would claim a
    completed job -- this one takes minutes and runs in another process."""
    alert = _alert(db_session, index=1)
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    db_session.commit()

    response = api.post(f"{api_prefix}/retrain", json={"requested_by": "test"})

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["started_at"] is None
    assert body["challenger_version"] is None


def test_a_second_request_while_one_is_pending_is_409(api, db_session, api_prefix: str) -> None:
    """Two concurrent runs would consume the same labels and race to publish a
    champion."""
    alert = _alert(db_session, index=1)
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    db_session.commit()

    assert api.post(f"{api_prefix}/retrain", json={}).status_code == 202
    second = api.post(f"{api_prefix}/retrain", json={})

    assert second.status_code == 409
    assert "already" in second.json()["detail"]


def test_a_request_with_no_new_labels_is_422(api, api_prefix: str) -> None:
    """A challenger fitted on the same data as the champion differs from it only by
    random seed, so there would be nothing to promote on."""
    response = api.post(f"{api_prefix}/retrain", json={})

    assert response.status_code == 422
    assert "no analyst labels" in response.json()["detail"]


def test_the_retrain_history_reports_the_declined_runs(api, db_session, api_prefix: str) -> None:
    """A gate that has never turned anything down is a gate nobody has evidence
    for, so the declined run is the row worth keeping."""
    db_session.add(
        RetrainRun(
            status="completed",
            requested_by="cli",
            labels_consumed=34,
            false_positives_consumed=13,
            true_positives_consumed=21,
            champion_version="v1",
            challenger_version="v2",
            champion_pr_auc=0.8969,
            challenger_pr_auc=0.8914,
            held_out_split="val",
            promoted=False,
            decision="Not promoted: inside the measured noise floor.",
        )
    )
    db_session.commit()

    body = api.get(f"{api_prefix}/retrain").json()

    assert body["pending"] == 0
    assert len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["promoted"] is False
    # Both scores on the same row, measured on the same split in the same run.
    assert run["champion_pr_auc"] == pytest.approx(0.8969)
    assert run["challenger_pr_auc"] == pytest.approx(0.8914)
    assert run["held_out_split"] == "val"


def test_the_history_says_who_executes_a_pending_run(api, api_prefix: str) -> None:
    """The endpoint reports the worker rather than the dashboard carrying its own
    copy of how the deployment is wired."""
    body = api.get(f"{api_prefix}/retrain").json()

    assert "training.retrain" in body["worker_hint"]
    assert "never fits a model" in body["worker_hint"]


def test_no_route_trains_a_model(client) -> None:
    """The anti-pattern, asserted on the schema rather than trusted to review.

    `POST /retrain` is a request for a run. Nothing in the surface claims to fit,
    and `app/routes/` imports no estimator -- the one place a model is fitted is
    `backend/training/`, which the API only reads artifacts from.
    """
    schema = client.app.openapi()

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            summary = (operation.get("summary") or "").lower()
            assert "train a model" not in summary, f"{method.upper()} {path}"
            assert "fit" not in summary.split(), f"{method.upper()} {path}"
