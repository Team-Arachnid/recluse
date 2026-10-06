"""Phase 5 -- the triage queue, the detail drawer, verdicts and host context.

These run against a real in-memory database built from the ORM metadata (the
`db_session` fixture), with the app's `get_session` dependency overridden onto
it, so the `CheckConstraint`s and the indexes are the real ones. A queue test
that asserted against hand-built dicts would pass with a schema the database
would reject.
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
from app.models import Alert, AnalystVerdict

NOW = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """An in-memory database the TestClient's worker thread can also reach.

    `conftest.py`'s `db_session` is deliberately a plain in-memory engine, and
    that is right for the tests which only touch the ORM. It cannot be used
    here: `TestClient` runs the app on its own thread, and SQLite refuses a
    connection created on a different one. So this file builds its own engine
    with `check_same_thread=False` plus `StaticPool` -- the pool matters as
    much as the flag, because the default pool would hand out a *new*
    connection per thread and each new connection to `:memory:` is a new,
    empty database.
    """
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
    """A client whose request sessions are the test's own in-memory database."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _alert(session, **overrides) -> Alert:
    """Persist one alert, defaulted to a plausible KNOWN row.

    Defaults rather than a fixture per shape, because almost every test here
    varies one field and needs the other twenty to be valid against the
    `family_matches_kind` constraint.
    """
    kind = overrides.pop("kind", "KNOWN")
    family = overrides.pop("family", "port_scan")
    if kind == "UNCLASSIFIED_ANOMALY":
        family = None

    detected_at = overrides.pop("detected_at", NOW)
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
        "src_port": None,
        "dst_ip": "192.168.10.50",
        "dst_port": 445,
        "protocol": None,
        "asset_criticality": "critical",
        "host_prior_alert_count": 0,
        "mitre_technique": "T1046" if kind == "KNOWN" else None,
        "explanation": {"explainer": "treeshap", "contributors": []},
        "narrative": "A sentence.",
        "recommended_actions": {
            "technique": None,
            "has_playbook": True,
            "summary": "s",
            "actions": ["a"],
        },
        "raw_flow": {"destination_port": 445, "_provenance": {"src_ip": "derived"}},
        "dedupe_key": f"k-{detected_at.timestamp()}-{overrides.get('risk_score', 0.5)}",
        "occurrence_count": 1,
        "first_seen": detected_at,
        "last_seen": detected_at,
        "status": "open",
        "model_version": "stage1-lgbm-test",
        "source": "replay",
        "ground_truth_label": "PortScan",
    }
    fields.update(overrides)
    alert = Alert(**fields)
    session.add(alert)
    session.commit()
    return alert


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def test_the_queue_is_sorted_by_risk_not_time(api, db_session, api_prefix: str) -> None:
    """The one ordering guarantee the whole screen is built on.

    Sorting a triage queue chronologically ranks alerts by when a packet
    happened rather than by what needs attention first, so the oldest alert
    here deliberately carries the highest risk.
    """
    _alert(db_session, risk_score=0.1, detected_at=NOW + dt.timedelta(hours=2), dedupe_key="a")
    _alert(db_session, risk_score=0.9, detected_at=NOW, dedupe_key="b")
    _alert(db_session, risk_score=0.5, detected_at=NOW + dt.timedelta(hours=1), dedupe_key="c")

    items = api.get(f"{api_prefix}/alerts").json()["items"]

    assert [row["risk_score"] for row in items] == [0.9, 0.5, 0.1]


def test_an_empty_queue_is_200_with_no_items(api, api_prefix: str) -> None:
    body = api.get(f"{api_prefix}/alerts").json()

    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.parametrize(
    ("filter_name", "value", "expected"),
    [
        ("severity", "critical", 1),
        ("kind", "UNCLASSIFIED_ANOMALY", 1),
        ("family", "port_scan", 1),
        ("status", "closed", 1),
    ],
)
def test_each_filter_narrows_the_queue(
    api, db_session, api_prefix: str, filter_name: str, value: str, expected: int
) -> None:
    # One row per filter under test, each distinguishable on exactly the field
    # that filter reads -- so a filter that matched on the wrong column, or
    # ignored its argument entirely, changes the count.
    _alert(db_session, dedupe_key="plain", family="port_scan")
    _alert(db_session, dedupe_key="crit", severity="critical", family="ddos")
    _alert(db_session, dedupe_key="anom", kind="UNCLASSIFIED_ANOMALY")
    _alert(db_session, dedupe_key="closed", status="closed", family="botnet")

    items = api.get(f"{api_prefix}/alerts", params={filter_name: value}).json()["items"]

    assert len(items) == expected


def test_an_unrecognised_filter_value_is_422(api, api_prefix: str) -> None:
    """The vocabularies are Literals, so the validation layer catches this."""
    response = api.get(f"{api_prefix}/alerts", params={"severity": "catastrophic"})

    assert response.status_code == 422


def test_the_time_window_filters_on_detected_at(api, db_session, api_prefix: str) -> None:
    _alert(db_session, dedupe_key="old", detected_at=NOW - dt.timedelta(days=2))
    _alert(db_session, dedupe_key="new", detected_at=NOW)

    items = api.get(
        f"{api_prefix}/alerts", params={"since": (NOW - dt.timedelta(hours=1)).isoformat()}
    ).json()["items"]

    assert len(items) == 1


# ---------------------------------------------------------------------------
# Pagination
#
# Keyset, not offset: the replay writes to this table while an analyst pages
# through it, and an offset page silently skips and repeats rows when a new
# higher-risk alert lands between two requests.
# ---------------------------------------------------------------------------


def test_paging_covers_every_row_exactly_once(api, db_session, api_prefix: str) -> None:
    for index in range(10):
        _alert(db_session, dedupe_key=f"k{index}", risk_score=index / 10)

    seen: list[int] = []
    cursor = None
    for _ in range(10):  # bounded so a cursor bug cannot loop forever
        params = {"limit": 3}
        if cursor is not None:
            params["cursor"] = cursor
        body = api.get(f"{api_prefix}/alerts", params=params).json()
        seen.extend(row["id"] for row in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 10
    assert len(set(seen)) == 10, "a row was returned on two different pages"


def test_paging_is_stable_when_a_higher_risk_alert_lands_mid_page(
    api, db_session, api_prefix: str
) -> None:
    """The failure keyset pagination exists to prevent, asserted directly.

    An offset-based page 2 would re-show the last row of page 1 here, because
    the insert shifts every row down by one. The cursor encodes a position in
    the sort order instead, so it is unaffected.
    """
    for index in range(6):
        _alert(db_session, dedupe_key=f"k{index}", risk_score=index / 10)

    first = api.get(f"{api_prefix}/alerts", params={"limit": 3}).json()
    page_one_ids = [row["id"] for row in first["items"]]

    _alert(db_session, dedupe_key="intruder", risk_score=0.99)

    second = api.get(
        f"{api_prefix}/alerts", params={"limit": 3, "cursor": first["next_cursor"]}
    ).json()
    page_two_ids = [row["id"] for row in second["items"]]

    assert not set(page_one_ids) & set(page_two_ids)


def test_ties_on_risk_score_do_not_lose_rows(api, db_session, api_prefix: str) -> None:
    """`risk_score` is rounded to four places, so ties are common, not rare.

    Without the `id` tie breaker the database may order tied rows differently
    per request, and a cursor on `risk_score` alone would skip some of them.
    """
    for index in range(6):
        _alert(db_session, dedupe_key=f"tie{index}", risk_score=0.5)

    seen: list[int] = []
    cursor = None
    for _ in range(6):
        params = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = api.get(f"{api_prefix}/alerts", params=params).json()
        seen.extend(row["id"] for row in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert sorted(seen) == sorted({*seen})
    assert len(seen) == 6


def test_a_garbage_cursor_is_422_not_a_silent_reset(api, api_prefix: str) -> None:
    """Treating it as "start from the top" would make a paging bug look like a
    queue that keeps resetting."""
    response = api.get(f"{api_prefix}/alerts", params={"cursor": "not-a-cursor"})

    assert response.status_code == 422
    assert "cursor" in response.json()["detail"]


def test_the_last_page_has_no_next_cursor(api, db_session, api_prefix: str) -> None:
    _alert(db_session, dedupe_key="only")

    body = api.get(f"{api_prefix}/alerts", params={"limit": 10}).json()

    assert len(body["items"]) == 1
    assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def test_detail_carries_the_three_panels(api, db_session, api_prefix: str) -> None:
    """Why it fired, what it is, how to fix it -- in one request, by design."""
    alert = _alert(db_session, dedupe_key="d")

    body = api.get(f"{api_prefix}/alerts/{alert.id}").json()

    assert body["explanation"]["explainer"] == "treeshap"
    assert body["narrative"]
    assert body["recommended_actions"]["has_playbook"] is True
    assert body["raw_flow"]["_provenance"]["src_ip"] == "derived"
    assert body["ground_truth_label"] == "PortScan"


def test_an_anomaly_detail_names_no_technique_and_no_family(
    api, db_session, api_prefix: str
) -> None:
    """The project's headline case: Stage 2 fired because Stage 1 could not name it."""
    alert = _alert(db_session, dedupe_key="anom", kind="UNCLASSIFIED_ANOMALY")

    body = api.get(f"{api_prefix}/alerts/{alert.id}").json()

    assert body["kind"] == "UNCLASSIFIED_ANOMALY"
    assert body["family"] is None
    assert body["mitre_technique"] is None


def test_an_unknown_id_is_404(api, api_prefix: str) -> None:
    assert api.get(f"{api_prefix}/alerts/9999").status_code == 404


def test_a_non_integer_id_is_422(api, api_prefix: str) -> None:
    assert api.get(f"{api_prefix}/alerts/not-a-number").status_code == 422


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def test_a_verdict_is_created_with_the_alerts_model_version(
    api, db_session, api_prefix: str
) -> None:
    """Captured from the alert being judged, not from whatever is loaded now.

    A label attributed to the wrong model version is worse than no label: it
    would train the next model on a correction to a decision it never made.
    """
    alert = _alert(db_session, dedupe_key="v", model_version="stage1-lgbm-202609281410")

    response = api.post(
        f"{api_prefix}/alerts/{alert.id}/verdict",
        json={"verdict": "TP", "note": "confirmed scan from lab host"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["verdict"] == "TP"
    assert body["model_version"] == "stage1-lgbm-202609281410"
    assert db_session.get(AnalystVerdict, body["id"]) is not None


def test_a_verdict_does_not_change_the_alerts_status(api, db_session, api_prefix: str) -> None:
    """Recording a judgement and moving an alert through triage are different
    actions; a verdict that silently closed an alert would take it out of a
    colleague's queue mid-review."""
    alert = _alert(db_session, dedupe_key="v2")

    api.post(f"{api_prefix}/alerts/{alert.id}/verdict", json={"verdict": "FP"})

    db_session.refresh(alert)
    assert alert.status == "open"


def test_an_invalid_verdict_value_is_422(api, db_session, api_prefix: str) -> None:
    alert = _alert(db_session, dedupe_key="v3")

    response = api.post(f"{api_prefix}/alerts/{alert.id}/verdict", json={"verdict": "MAYBE"})

    assert response.status_code == 422


def test_a_verdict_on_an_unknown_alert_is_404(api, api_prefix: str) -> None:
    assert api.post(f"{api_prefix}/alerts/9999/verdict", json={"verdict": "TP"}).status_code == 404


def test_the_queue_shows_the_most_recent_verdict(api, db_session, api_prefix: str) -> None:
    """`analyst_verdicts` is one-to-many, so "the" verdict has to be defined."""
    alert = _alert(db_session, dedupe_key="v4")

    api.post(f"{api_prefix}/alerts/{alert.id}/verdict", json={"verdict": "FP"})
    api.post(f"{api_prefix}/alerts/{alert.id}/verdict", json={"verdict": "TP"})

    items = api.get(f"{api_prefix}/alerts").json()["items"]

    assert items[0]["latest_verdict"] == "TP"


def test_an_unjudged_alert_has_a_null_verdict(api, db_session, api_prefix: str) -> None:
    _alert(db_session, dedupe_key="v5")

    items = api.get(f"{api_prefix}/alerts").json()["items"]

    assert items[0]["latest_verdict"] is None


# ---------------------------------------------------------------------------
# Host correlation
# ---------------------------------------------------------------------------


def test_related_returns_the_same_host_and_excludes_the_anchor(
    api, db_session, api_prefix: str
) -> None:
    """A scan then an exploit from one host is one incident told in two alerts."""
    anchor = _alert(db_session, dedupe_key="r1", src_ip="192.168.10.8")
    _alert(
        db_session,
        dedupe_key="r2",
        src_ip="192.168.10.8",
        detected_at=NOW + dt.timedelta(hours=1),
        family="web_attack",
    )
    _alert(db_session, dedupe_key="r3", src_ip="172.16.0.1")

    body = api.get(f"{api_prefix}/alerts/{anchor.id}/related").json()

    assert [row["src_ip"] for row in body] == ["192.168.10.8"]
    assert anchor.id not in [row["id"] for row in body]


def test_related_excludes_alerts_outside_the_window(api, db_session, api_prefix: str) -> None:
    anchor = _alert(db_session, dedupe_key="w1", src_ip="192.168.10.8")
    _alert(
        db_session,
        dedupe_key="w2",
        src_ip="192.168.10.8",
        detected_at=NOW + dt.timedelta(days=5),
    )

    body = api.get(f"{api_prefix}/alerts/{anchor.id}/related").json()

    assert body == []


def test_the_window_is_measured_from_the_anchor_not_from_now(
    api, db_session, api_prefix: str
) -> None:
    """An alert opened a week late would otherwise show no context at all.

    Both rows here are far in the past relative to the test run, so a window
    anchored on `now` would return nothing.
    """
    long_ago = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    anchor = _alert(db_session, dedupe_key="p1", src_ip="192.168.10.8", detected_at=long_ago)
    _alert(
        db_session,
        dedupe_key="p2",
        src_ip="192.168.10.8",
        detected_at=long_ago + dt.timedelta(hours=2),
    )

    body = api.get(f"{api_prefix}/alerts/{anchor.id}/related").json()

    assert len(body) == 1


def test_related_reaches_backwards_as_well_as_forwards(api, db_session, api_prefix: str) -> None:
    """The reconnaissance that preceded an alert is the context worth most."""
    anchor = _alert(
        db_session, dedupe_key="b1", src_ip="192.168.10.8", detected_at=NOW, family="web_attack"
    )
    _alert(
        db_session,
        dedupe_key="b2",
        src_ip="192.168.10.8",
        detected_at=NOW - dt.timedelta(hours=3),
    )

    body = api.get(f"{api_prefix}/alerts/{anchor.id}/related").json()

    assert len(body) == 1


def test_related_on_an_unknown_alert_is_404(api, api_prefix: str) -> None:
    assert api.get(f"{api_prefix}/alerts/9999/related").status_code == 404
