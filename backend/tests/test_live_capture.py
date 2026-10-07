"""Phase 9 -- the live path: authorised capture only, shadow before alert.

Captures are driven from hand-built pcaps in a temporary capture directory, so
these exercise the real `/ingest/*` routes, the real flow meter, the real models
and the real alert pipeline without touching a network.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.live_capture as live_capture
from app.config import settings
from app.db import Base
from app.flowmeter import meter_pcap
from app.inference import ModelBundle, load_bundle
from app.models import Alert, ShadowScore
from app.risk import risk_score
from tests.packets import http_exchange, write_pcap
from training.calibrate_live import CalibrationError, calibrate
from training.metrics import error_histogram

PCAP = "burn-in.pcap"


@pytest.fixture
def capture_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A capture directory holding one pcap of twenty short exchanges."""
    directory = tmp_path / "pcap"
    directory.mkdir()
    frames = []
    for n in range(20):
        frames += http_exchange(f"10.0.1.{n + 2}", 41000 + n, start_us=n * 50_000)
    write_pcap(directory / PCAP, sorted(frames, key=lambda frame: frame[0]))
    monkeypatch.setattr(settings, "live_pcap_dir", directory)
    monkeypatch.setattr(settings, "live_interfaces", "")
    return directory


@pytest.fixture
def private_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """The capture's writes go to a database of its own, not the shared one."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'live.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @contextmanager
    def scope() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(live_capture, "session_scope", scope)
    yield factory
    engine.dispose()


@pytest.fixture
def no_calibration() -> Iterator[None]:
    path = settings.artifacts_path / live_capture.LOCAL_THRESHOLD_FILE
    path.unlink(missing_ok=True)
    yield
    path.unlink(missing_ok=True)


def burn_in_histogram(bundle: ModelBundle) -> dict[str, Any]:
    """A burn-in's errors on the shipped histogram's bins, as `calibrate` keeps them."""
    assert bundle.benign_error_histogram is not None
    errors = np.linspace(0.001, 0.2, 1_000)
    return asdict(error_histogram(errors, bundle.benign_error_histogram["edges"]))


def wait_until_idle(client: TestClient, api_prefix: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"{api_prefix}/ingest/status").json()
        if not status["running"]:
            return status
        time.sleep(0.1)
    raise AssertionError("the capture did not finish")


def test_an_interface_outside_the_allow_list_is_refused(
    client: TestClient, api_prefix: str, capture_dir: Path
) -> None:
    response = client.post(
        f"{api_prefix}/ingest/start", json={"source": "interface", "interface": "eth0"}
    )

    assert response.status_code == 403
    assert "IDS_LIVE_INTERFACES" in response.json()["detail"]


@pytest.mark.parametrize("name", ["../secrets.pcap", "/etc/passwd", "missing.pcap"])
def test_a_pcap_outside_the_capture_directory_is_refused(
    client: TestClient, api_prefix: str, capture_dir: Path, name: str
) -> None:
    response = client.post(f"{api_prefix}/ingest/start", json={"source": "pcap", "pcap": name})

    assert response.status_code == 404


def test_alert_mode_is_refused_before_a_burn_in(
    client: TestClient, api_prefix: str, capture_dir: Path, no_calibration: None
) -> None:
    response = client.post(
        f"{api_prefix}/ingest/start", json={"source": "pcap", "pcap": PCAP, "mode": "alert"}
    )

    assert response.status_code == 409
    assert "shadow" in response.json()["detail"]


def test_shadow_mode_scores_every_flow_and_alerts_no_one(
    client: TestClient,
    api_prefix: str,
    capture_dir: Path,
    private_db: sessionmaker[Session],
    no_calibration: None,
) -> None:
    response = client.post(f"{api_prefix}/ingest/start", json={"source": "pcap", "pcap": PCAP})
    assert response.status_code == 202, response.text
    status = wait_until_idle(client, api_prefix)

    assert status["error"] is None
    assert status["flows"] == 20 and status["scored"] == 20
    assert status["shadow_rows"] == 20 and status["alerts"] == 0
    with private_db() as session:
        rows = session.scalars(select(ShadowScore)).all()
        assert session.scalars(select(Alert)).all() == []
    assert len(rows) == 20
    assert all(row.stage2_error is not None and row.stage2_error >= 0 for row in rows)
    assert {row.src_ip for row in rows} == {f"10.0.1.{n + 2}" for n in range(20)}
    assert {(row.protocol, row.dst_port) for row in rows} == {("tcp", 80)}


def test_calibration_cuts_the_percentile_and_refuses_a_thin_burn_in() -> None:
    bundle = load_bundle(settings.artifacts_path)
    rows = [
        ShadowScore(
            session_id="s",
            captured_at=dt.datetime(2026, 10, 7, tzinfo=dt.UTC),
            capture_source="pcap:x",
            model_version=bundle.version,
            src_ip="10.0.0.2",
            src_port=1,
            dst_ip="10.0.0.1",
            dst_port=443,
            protocol="tcp",
            stage1_confidence=0.0,
            stage2_error=error,
            would_alert=None,
        )
        for error in [i / 1000 for i in range(1, 1001)]
    ]

    result = calibrate(
        rows,
        stage2_version=bundle.stage2_version,
        tau_dataset=0.1,
        dataset_percentiles={"p99.5": 0.1},
        percentile=99.5,
        min_flows=500,
        dataset_edges=bundle.benign_error_histogram["edges"],
    )

    assert result["tau_anom_local"] == pytest.approx(0.995, abs=1e-3)
    assert result["dataset_threshold_alert_rate"] == pytest.approx(0.901, abs=1e-3)
    # The burn-in's own distribution travels with its threshold, on the shipped
    # bins, so a live alert can be ranked in it (`app.risk.stage2_base`).
    histogram = result["error_histogram"]
    assert histogram["edges"] == bundle.benign_error_histogram["edges"]
    assert histogram["rows"] == sum(histogram["counts"]) == 1000
    with pytest.raises(CalibrationError, match="at least 2,000"):
        calibrate(
            rows,
            stage2_version=bundle.stage2_version,
            tau_dataset=0.1,
            dataset_percentiles={},
            percentile=99.5,
            min_flows=2_000,
        )


def test_alert_mode_writes_observed_endpoints_at_the_local_threshold(
    client: TestClient,
    api_prefix: str,
    capture_dir: Path,
    private_db: sessionmaker[Session],
    no_calibration: None,
) -> None:
    bundle = load_bundle(settings.artifacts_path)
    # A threshold of zero: every flow Stage 1 does not name is an anomaly, so
    # the run is guaranteed to write alerts the assertions can read.
    calibration = {
        "tau_anom_local": 0.0,
        "tau_anom_dataset": bundle.tau_anom,
        "percentile": 99.5,
        "flows": 1000,
        "computed_at": "2026-10-07T00:00:00+00:00",
        "stage2_version": bundle.stage2_version,
        "dataset_threshold_alert_rate": 0.2,
        "stage1_alert_rate": 0.0,
        "capture_sources": ["pcap:x"],
        "window_start": "2026-10-07T00:00:00+00:00",
        "window_end": "2026-10-07T01:00:00+00:00",
        "error_histogram": burn_in_histogram(bundle),
    }
    (settings.artifacts_path / live_capture.LOCAL_THRESHOLD_FILE).write_text(
        json.dumps(calibration)
    )

    status = client.get(f"{api_prefix}/ingest/status").json()
    assert status["calibration"]["tau_anom_local"] == 0.0
    assert status["tau_anom_dataset"] == pytest.approx(bundle.tau_anom)

    response = client.post(
        f"{api_prefix}/ingest/start", json={"source": "pcap", "pcap": PCAP, "mode": "alert"}
    )
    assert response.status_code == 202, response.text
    status = wait_until_idle(client, api_prefix)
    assert status["alerts"] > 0 and status["tau_anom"] == 0.0

    with private_db() as session:
        alerts = session.scalars(select(Alert)).all()
    assert alerts
    for alert in alerts:
        assert alert.source == "live"
        assert alert.src_ip.startswith("10.0.1.") and alert.dst_ip == "10.0.0.1"
        assert alert.dst_port == 80 and alert.protocol == "tcp" and alert.src_port is not None
        assert alert.ground_truth_label is None and alert.ground_truth_counts is None
        assert alert.explanation and alert.narrative
        provenance = alert.raw_flow["_provenance"]
        assert provenance["detected_at"] == "capture_clock"
        assert provenance["src_ip"] == "observed" and provenance["tau_anom"] == 0.0


def test_a_live_alert_is_ranked_against_the_threshold_it_was_decided_at(
    capture_dir: Path, private_db: sessionmaker[Session]
) -> None:
    """Risk is headroom past the threshold that produced the alert (`app.risk`).

    A live alert is decided at the local threshold, so its rank is measured in
    the burn-in's distribution from there. Measured from the 2017 threshold
    instead, it would describe another network's normal -- on the real burn-in,
    a flow just over the local bar read as far past the dataset's and was
    ranked critical.
    """
    bundle = load_bundle(settings.artifacts_path)
    flows, _ = meter_pcap(capture_dir / PCAP)
    local = burn_in_histogram(bundle)
    state = live_capture.IngestState(mode="alert", source="pcap:x", session_id="s", tau_anom=0.05)

    live_capture.score_flows(
        bundle=bundle,
        broker=None,
        state=state,
        flows=flows,
        calibration={"tau_anom_local": 0.05, "computed_at": None, "error_histogram": local},
    )

    with private_db() as session:
        alerts = session.scalars(select(Alert)).all()
    assert len(alerts) == len(flows) == 20
    for alert in alerts:
        assert alert.kind == "UNCLASSIFIED_ANOMALY"
        evidence = {
            "kind": alert.kind,
            "confidence": alert.confidence,
            "anomaly_score": alert.anomaly_score,
            "tau_sup": bundle.tau_sup,
        }
        here = risk_score(**evidence, tau_anom=0.05, histogram=local)
        there = risk_score(
            **evidence, tau_anom=bundle.tau_anom, histogram=bundle.benign_error_histogram
        )
        assert alert.risk_score == here != there


def test_replay_and_capture_never_write_at_once(
    client: TestClient, api_prefix: str, capture_dir: Path
) -> None:
    ingest = client.app.state.ingest
    ingest.running, ingest.source = True, "pcap:held"
    try:
        response = client.post(f"{api_prefix}/replay/start", json={"speed": 1, "dataset": "demo"})
    finally:
        ingest.running = False
    assert response.status_code == 409
    assert "capture" in response.json()["detail"]
