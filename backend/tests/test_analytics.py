"""Phase 5 -- GET /analytics/summary and GET /analytics/mitre-coverage.

Unlike `/metrics/*`, everything here is computed from the database, so these
tests write alerts and verdicts and then assert the aggregate.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import create_app
from app.mitre import TECHNIQUES
from app.models import Alert, AnalystVerdict


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


def _alert(session, *, key: str, minutes_ago: int = 5, **overrides) -> Alert:
    """An alert placed relative to now, because the ranges are relative to now."""
    kind = overrides.pop("kind", "KNOWN")
    family = None if kind == "UNCLASSIFIED_ANOMALY" else overrides.pop("family", "port_scan")
    overrides.pop("family", None)
    detected_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes_ago)

    fields = {
        "kind": kind,
        "family": family,
        "detection_stage": "stage1_supervised" if kind == "KNOWN" else "stage2_anomaly",
        "severity": "high",
        "risk_score": 0.5,
        "confidence": 0.9 if kind == "KNOWN" else None,
        "anomaly_score": None if kind == "KNOWN" else 0.3,
        "detected_at": detected_at,
        "src_ip": "172.16.0.1",
        "dst_ip": "192.168.10.50",
        "dst_port": 445,
        "asset_criticality": "critical",
        "host_prior_alert_count": 0,
        "mitre_technique": "T1046" if kind == "KNOWN" else None,
        "explanation": {"explainer": "treeshap", "contributors": []},
        "narrative": "A sentence.",
        "recommended_actions": {"has_playbook": True},
        "raw_flow": {},
        "dedupe_key": key,
        "occurrence_count": 1,
        "first_seen": detected_at,
        "last_seen": detected_at,
        "status": "open",
        "model_version": "v-test",
        "source": "replay",
    }
    fields.update(overrides)
    alert = Alert(**fields)
    session.add(alert)
    session.commit()
    return alert


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_an_empty_database_is_200_with_zeros(api, api_prefix: str) -> None:
    """Not a 404 and not an error: no alerts yet is a real answer."""
    body = api.get(f"{api_prefix}/analytics/summary").json()

    assert body["total_alerts"] == 0
    assert body["unclassified_rate"] == 0.0
    assert body["series"] == []


def test_the_series_splits_known_from_unclassified(api, db_session, api_prefix: str) -> None:
    """The unclassified line is the novel-detection headline, so it is its own
    series rather than folded into a total."""
    _alert(db_session, key="k1")
    _alert(db_session, key="k2")
    _alert(db_session, key="a1", kind="UNCLASSIFIED_ANOMALY")

    body = api.get(f"{api_prefix}/analytics/summary").json()

    assert body["total_alerts"] == 3
    assert body["unclassified_alerts"] == 1
    assert body["unclassified_rate"] == pytest.approx(1 / 3)
    assert sum(bucket["known"] for bucket in body["series"]) == 2
    assert sum(bucket["unclassified"] for bucket in body["series"]) == 1


def test_empty_buckets_are_zeros_not_gaps(api, db_session, api_prefix: str) -> None:
    """A chart that skips empty hours draws a line through the gap and makes
    an outage look like steady traffic."""
    _alert(db_session, key="old", minutes_ago=60 * 20)
    _alert(db_session, key="new", minutes_ago=1)

    body = api.get(f"{api_prefix}/analytics/summary", params={"range": "24h"}).json()

    assert len(body["series"]) > 2
    assert any(b["known"] == 0 and b["unclassified"] == 0 for b in body["series"])


def test_the_range_excludes_older_alerts(api, db_session, api_prefix: str) -> None:
    _alert(db_session, key="ancient", minutes_ago=60 * 24 * 10)
    _alert(db_session, key="recent", minutes_ago=5)

    day = api.get(f"{api_prefix}/analytics/summary", params={"range": "24h"}).json()
    everything = api.get(f"{api_prefix}/analytics/summary", params={"range": "all"}).json()

    assert day["total_alerts"] == 1
    assert everything["total_alerts"] == 2


def test_an_unrecognised_range_is_422(api, api_prefix: str) -> None:
    assert api.get(f"{api_prefix}/analytics/summary", params={"range": "1y"}).status_code == 422


def test_the_ranked_tables_count_and_order(api, db_session, api_prefix: str) -> None:
    for index in range(3):
        _alert(db_session, key=f"h{index}", dst_ip="192.168.10.50")
    _alert(db_session, key="other", dst_ip="192.168.10.51")

    body = api.get(f"{api_prefix}/analytics/summary").json()
    hosts = body["top_destination_hosts"]

    assert hosts[0] == {"value": "192.168.10.50", "count": 3}
    assert hosts[1] == {"value": "192.168.10.51", "count": 1}


def test_the_family_mix_excludes_unclassified(api, db_session, api_prefix: str) -> None:
    """An anomaly has no family by construction, so it cannot appear here --
    it is in `unclassified_alerts` instead."""
    _alert(db_session, key="f1", family="ddos")
    _alert(db_session, key="f2", kind="UNCLASSIFIED_ANOMALY")

    families = api.get(f"{api_prefix}/analytics/summary").json()["families"]

    assert [row["value"] for row in families] == ["ddos"]


# ---------------------------------------------------------------------------
# Throughput
# ---------------------------------------------------------------------------


def test_throughput_counts_opened_and_resolved(api, db_session, api_prefix: str) -> None:
    _alert(db_session, key="o1")
    _alert(db_session, key="r1", status="closed")
    _alert(db_session, key="r2", status="dismissed")

    throughput = api.get(f"{api_prefix}/analytics/summary").json()["throughput"]

    assert throughput["opened"] == 3
    assert throughput["resolved"] == 2


def test_unsure_is_excluded_from_the_true_positive_rate(api, db_session, api_prefix: str) -> None:
    """UNSURE is not a judgement that the alert was wrong.

    Folding it into the denominator would drag the rate toward zero every time
    an analyst was honest about not knowing, which punishes the behaviour the
    feedback loop most wants.
    """
    alert = _alert(db_session, key="v1")
    for verdict in ("TP", "TP", "FP", "UNSURE"):
        db_session.add(AnalystVerdict(alert_id=alert.id, verdict=verdict))
    db_session.commit()

    throughput = api.get(f"{api_prefix}/analytics/summary").json()["throughput"]

    assert throughput["verdicts"] == 4
    assert throughput["unsure"] == 1
    assert throughput["true_positive_rate"] == pytest.approx(2 / 3)


def test_the_true_positive_rate_is_null_with_nothing_decided(
    api, db_session, api_prefix: str
) -> None:
    """Null, not zero: zero would claim every alert was a false positive."""
    _alert(db_session, key="n1")

    throughput = api.get(f"{api_prefix}/analytics/summary").json()["throughput"]

    assert throughput["true_positive_rate"] is None
    assert throughput["mean_seconds_to_verdict"] is None


# ---------------------------------------------------------------------------
# MITRE coverage
# ---------------------------------------------------------------------------


def test_the_heatmap_axis_comes_from_the_table_not_the_data(api, api_prefix: str) -> None:
    """With no alerts at all, every technique still has a row, with a zero.

    "We have never seen this" and "we cannot see this" look identical when the
    axis is built from whatever happened to fire, and telling them apart is
    the entire point of a coverage heatmap.
    """
    body = api.get(f"{api_prefix}/analytics/mitre-coverage").json()

    assert len(body["techniques"]) == len(TECHNIQUES)
    assert all(row["count"] == 0 for row in body["techniques"])


def test_counts_land_on_the_right_technique(api, db_session, api_prefix: str) -> None:
    _alert(db_session, key="c1", mitre_technique="T1046")
    _alert(db_session, key="c2", mitre_technique="T1046")
    _alert(db_session, key="c3", mitre_technique="T1110")

    rows = {
        row["technique_id"]: row["count"]
        for row in api.get(f"{api_prefix}/analytics/mitre-coverage").json()["techniques"]
    }

    assert rows["T1046"] == 2
    assert rows["T1110"] == 1
    assert rows["T1498"] == 0


def test_unclassified_anomalies_are_counted_beside_the_table(
    api, db_session, api_prefix: str
) -> None:
    """Stage 2 maps to no technique, so it gets no row -- but omitting the
    count would understate exactly the detections this project is proudest of."""
    _alert(db_session, key="u1", kind="UNCLASSIFIED_ANOMALY")
    _alert(db_session, key="u2", kind="UNCLASSIFIED_ANOMALY")

    body = api.get(f"{api_prefix}/analytics/mitre-coverage").json()

    assert body["unclassified_anomalies"] == 2
    assert all(row["technique_id"] is not None for row in body["techniques"])


def test_every_row_carries_its_plain_english_line(api, api_prefix: str) -> None:
    """The analyst reads a sentence, not a bare ATT&CK code."""
    body = api.get(f"{api_prefix}/analytics/mitre-coverage").json()

    assert all(row["means"] and row["name"] and row["url"] for row in body["techniques"])


# ---------------------------------------------------------------------------
# Phase 6 -- GET /analytics/feedback, the Feedback Loop screen
# ---------------------------------------------------------------------------


def test_feedback_on_an_untouched_deployment_reads_as_nothing_judged(api, api_prefix: str) -> None:
    """Zero labels is a real state. It is also the state the screen is built to
    make uncomfortable, so it reports it rather than hiding behind a dash."""
    response = api.get(f"{api_prefix}/analytics/feedback")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["labels_total"] == 0
    assert body["labels_pending_retrain"] == 0
    assert body["disagreement_rate"] is None
    assert body["judged_share"] == 0.0
    assert body["by_model_version"] == []


def test_pending_labels_are_the_ones_no_retrain_has_consumed(
    api, db_session, api_prefix: str
) -> None:
    """`consumed_at` is what makes "since the last retrain" answerable without
    a second table, so the count has to come from it and not from a date."""
    first = _alert(db_session, key="a")
    second = _alert(db_session, key="b")
    db_session.add(AnalystVerdict(alert_id=first.id, verdict="TP"))
    db_session.add(
        AnalystVerdict(alert_id=second.id, verdict="FP", consumed_at=dt.datetime.now(dt.UTC))
    )
    db_session.commit()

    body = api.get(f"{api_prefix}/analytics/feedback").json()

    assert body["labels_total"] == 2
    assert body["labels_pending_retrain"] == 1
    assert body["labels_consumed"] == 1


def test_disagreement_excludes_unsure_from_the_denominator(
    api, db_session, api_prefix: str
) -> None:
    """UNSURE is not a judgement that the model was wrong. Counting it as one
    would push the rate up every time somebody was honest about not knowing."""
    alerts = [_alert(db_session, key=f"k{index}") for index in range(4)]
    for alert, verdict in zip(alerts, ("TP", "TP", "FP", "UNSURE"), strict=True):
        db_session.add(AnalystVerdict(alert_id=alert.id, verdict=verdict))
    db_session.commit()

    body = api.get(f"{api_prefix}/analytics/feedback").json()

    assert body["true_positives"] == 2
    assert body["false_positives"] == 1
    assert body["unsure"] == 1
    assert body["disagreement_rate"] == pytest.approx(1 / 3)


def test_judged_alerts_counts_rows_not_verdicts(api, db_session, api_prefix: str) -> None:
    """Two analysts judging one alert is one alert judged, twice."""
    alert = _alert(db_session, key="a")
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="TP", analyst="ana"))
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP", analyst="bo"))
    db_session.commit()

    body = api.get(f"{api_prefix}/analytics/feedback").json()

    assert body["labels_total"] == 2
    assert body["judged_alerts"] == 1
    assert body["judged_share"] == pytest.approx(1.0)


def test_labels_are_grouped_by_the_model_version_they_judged(
    api, db_session, api_prefix: str
) -> None:
    """The audit trail doing its job: a label recorded against a since-replaced
    champion stays attributed to it, so a retrain can tell which of its labels
    correct decisions it actually made."""
    old = _alert(db_session, key="old", model_version="stage1-rf-1")
    new = _alert(db_session, key="new", model_version="stage1-lgbm-2")
    db_session.add(AnalystVerdict(alert_id=old.id, verdict="FP", model_version="stage1-rf-1"))
    db_session.add(AnalystVerdict(alert_id=new.id, verdict="TP", model_version="stage1-lgbm-2"))
    db_session.add(AnalystVerdict(alert_id=new.id, verdict="TP", model_version="stage1-lgbm-2"))
    db_session.commit()

    rows = api.get(f"{api_prefix}/analytics/feedback").json()["by_model_version"]

    assert [row["model_version"] for row in rows] == ["stage1-lgbm-2", "stage1-rf-1"]
    assert rows[0]["true_positives"] == 2
    assert rows[1]["false_positives"] == 1


def test_retraining_is_reported_as_available_with_the_phase_that_landed_it(
    api, api_prefix: str
) -> None:
    """Phase 7 shipped `POST /retrain`, so the flag a screen would read says so;
    the roadmap belongs to the backend, not the screen."""
    body = api.get(f"{api_prefix}/analytics/feedback").json()

    assert body["retrain_available"] is True
    assert "Phase 7" in body["retrain_phase"]
