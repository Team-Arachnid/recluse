"""Phase 7 -- the retraining pipeline, the Stage 2 refit, and the audit trail.

The retrain had no tests until the review that found three bugs in it, and two of
those bugs share a shape: something rewrote a shared artifact and quietly dropped
the part that belonged to the other stage. A Stage 1 promotion wrote a fresh
model card and lost Stage 2's histogram and version; a Stage 1 fit refitted the
scaler both stages read. Neither raised. These tests pin the shared artifacts
across a promotion, which is where that kind of bug lives.

The real retrain fits a million rows three times. These fit a thousand, against
the same small frames the Phase 2 to 4 tests use, through the same code.
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

import training.refit_autoencoder as refit_module
import training.retrain as retrain_module
from app.feedback import BenignPool
from app.inference import compose_version, load_bundle
from app.models import Alert, AnalystVerdict, RetrainRun
from training.features import load_preprocessing_bundle
from training.refit_autoencoder import ARCHIVE_DIR, refit_stage2, split_pool
from training.retrain import Comparison, publish_challenger, run_retrain

NOW = dt.datetime(2026, 10, 6, 9, 0, tzinfo=dt.UTC)

# A refit that proves the loop runs and the artifacts stay consistent, not one
# that proves separation on a thousand synthetic rows.
REFIT_FAST = {"rehearsal_rows": 400, "max_epochs": 2, "patience": 1}
STAGE1_FAST = {"algorithm": "rf", "depth_grid": (8,), "sweep_rows": 10_000}


def _card(artifacts: Path) -> dict:
    return json.loads((artifacts / "model_card.json").read_text(encoding="utf-8"))


def _flows(frame: pd.DataFrame) -> list[dict]:
    return [
        {
            key: (float(value) if key != "destination_port" else int(value))
            for key, value in row.items()
        }
        for row in frame.drop(columns=["label"]).to_dict(orient="records")
    ]


@pytest.fixture
def processed(tmp_path, phase2_train, phase2_val, phase2_test, phase3_benign) -> Path:
    """The four processed splits, on disk where the retrain reads them."""
    directory = tmp_path / "data" / "processed"
    directory.mkdir(parents=True)
    for name, frame in (
        ("train", phase2_train),
        ("val", phase2_val),
        ("test", phase2_test),
        ("benign_train", phase3_benign),
    ):
        frame.to_parquet(directory / f"{name}.parquet")
    return directory


# ---------------------------------------------------------------------------
# The served version names both stages
# ---------------------------------------------------------------------------


def test_the_served_version_names_both_stages(two_stage_artifacts) -> None:
    """ "Which model scored this alert" has to name the autoencoder too. The card's
    top-level version is Stage 1's alone, so a Stage 2 refit would otherwise
    leave every later alert carrying the same string as every earlier one."""
    card = _card(two_stage_artifacts)
    bundle = load_bundle(two_stage_artifacts)

    assert bundle.stage1_version == card["version"]
    assert bundle.stage2_version == card["stage2"]["version"]
    assert bundle.version == card["version"] + "+" + card["stage2"]["version"]
    # The columns that record it are sixty-four characters wide.
    assert len(bundle.version) <= 64


def test_a_card_naming_a_missing_autoencoder_does_not_claim_it(two_stage_artifacts) -> None:
    (two_stage_artifacts / "autoencoder.pt").unlink()

    bundle = load_bundle(two_stage_artifacts)

    assert bundle.version == _card(two_stage_artifacts)["version"]
    assert "+" not in bundle.version


def test_compose_version_skips_what_is_absent() -> None:
    assert compose_version("s1", "s2") == "s1+s2"
    assert compose_version("s1", None) == "s1"
    assert compose_version(None, None) == "unloaded"


# ---------------------------------------------------------------------------
# A Stage 1 promotion leaves Stage 2's record alone
# ---------------------------------------------------------------------------


def test_publishing_a_challenger_keeps_stage_2s_section(two_stage_artifacts, tmp_path) -> None:
    """The bug this pins: the card was rewritten from scratch and kept only
    `tau_anom`, so every Stage 2 alert afterwards was risk-ranked by the crude
    no-histogram fallback and the served version stopped naming the autoencoder."""
    before = _card(two_stage_artifacts)
    challenger = tmp_path / "challenger"
    challenger.mkdir()
    for name in ("supervised_rf.pkl", "preprocessing_rf.pkl"):
        (challenger / name).write_bytes((two_stage_artifacts / name).read_bytes())

    comparison = Comparison(
        gate="val",
        champion_version=before["version"],
        challenger_version="stage1-rf-209901010000",
    )
    publish_challenger(
        challenger_dir=challenger,
        artifacts_dir=two_stage_artifacts,
        algorithm="rf",
        comparison=comparison,
        run_record={**before["run"], "threshold": {"tau": 0.123}},
    )

    after = _card(two_stage_artifacts)
    assert after["version"] == "stage1-rf-209901010000"
    assert after["thresholds"]["tau_sup"] == 0.123
    assert after["thresholds"]["tau_anom"] == before["thresholds"]["tau_anom"]
    assert after["stage2"] == before["stage2"]
    assert after["anomaly_algorithm"] == before["anomaly_algorithm"]
    assert after["previous_champion"] == before["version"]
    # The old champion's test-day numbers do not describe the new one.
    assert "test" not in after

    bundle = load_bundle(two_stage_artifacts)
    assert bundle.benign_error_histogram is not None
    assert bundle.version.endswith("+" + before["stage2"]["version"])


# ---------------------------------------------------------------------------
# The Stage 2 refit and its gate
# ---------------------------------------------------------------------------


def _usable_pool(flows: list[dict]) -> BenignPool:
    hosts = {f"10.1.0.{index % 6}": 0 for index in range(len(flows))}
    pool = BenignPool(flows=flows, candidates=len(flows), host_cap=0.2, usable=True)
    pool.admitted_by_host = {host: len(flows) // len(hosts) for host in hosts}
    pool.per_host_allowance = len(flows) // len(hosts)
    pool.reason = f"-- {len(flows)} rows from {len(hosts)} host(s)"
    return pool


def test_split_pool_is_deterministic_and_never_leaves_a_side_empty() -> None:
    flows = [{"flow_duration": float(index)} for index in range(10)]

    first = split_pool(flows)
    second = split_pool(flows)

    assert first == second
    fit, holdout = first
    assert len(fit) + len(holdout) == 10
    assert fit and holdout
    assert split_pool([{"a": 1.0}]) == ([{"a": 1.0}], [])


def test_a_refused_pool_leaves_the_autoencoder_untouched(
    two_stage_artifacts, processed, budget
) -> None:
    weights = (two_stage_artifacts / "autoencoder.pt").read_bytes()
    pool = BenignPool(
        candidates=13, host_cap=0.2, usable=False, reason="-- 1 rows is under the floor"
    )

    result = refit_stage2(
        pool, artifacts_dir=two_stage_artifacts, processed_dir=processed, settings=budget
    )

    assert result.attempted is False
    assert result.promoted is False
    assert "left alone" in result.decision
    assert (two_stage_artifacts / "autoencoder.pt").read_bytes() == weights


def test_a_refit_that_does_not_improve_is_declined_and_writes_nothing(
    two_stage_artifacts, processed, budget, phase2_test, monkeypatch
) -> None:
    monkeypatch.setattr(refit_module, "MIN_IMPROVEMENT", 10.0)
    weights = (two_stage_artifacts / "autoencoder.pt").read_bytes()
    card = _card(two_stage_artifacts)
    pool = _usable_pool(_flows(phase2_test[phase2_test["label"] == "BENIGN"].head(60)))

    result = refit_stage2(
        pool,
        artifacts_dir=two_stage_artifacts,
        processed_dir=processed,
        settings=budget,
        **REFIT_FAST,
    )

    assert result.attempted is True
    assert result.promoted is False
    assert "not promoted" in result.decision
    # Both arms measured on the same held-out set, each at its own threshold.
    assert result.champion is not None and result.challenger is not None
    assert result.pool_holdout_rows > 0
    assert result.champion["tau_anom"] > 0 and result.challenger["tau_anom"] > 0
    assert (two_stage_artifacts / "autoencoder.pt").read_bytes() == weights
    assert _card(two_stage_artifacts) == card
    assert not (two_stage_artifacts / ARCHIVE_DIR).exists()


def test_a_promoted_refit_archives_the_champion_and_changes_the_served_version(
    two_stage_artifacts, processed, budget, phase2_test, monkeypatch
) -> None:
    monkeypatch.setattr(refit_module, "MIN_IMPROVEMENT", -10.0)
    before = _card(two_stage_artifacts)
    old_version = load_bundle(two_stage_artifacts).version
    pool = _usable_pool(_flows(phase2_test[phase2_test["label"] == "BENIGN"].head(60)))

    result = refit_stage2(
        pool,
        artifacts_dir=two_stage_artifacts,
        processed_dir=processed,
        settings=budget,
        **REFIT_FAST,
    )

    assert result.promoted is True
    archive = two_stage_artifacts / ARCHIVE_DIR / before["stage2"]["version"]
    assert (archive / "autoencoder.pt").exists()
    assert (archive / "metrics_anomaly.json").exists()

    after = _card(two_stage_artifacts)
    assert after["stage2"]["version"] == result.challenger_version
    assert after["stage2"]["previous_version"] == before["stage2"]["version"]
    assert after["thresholds"]["tau_anom"] == pytest.approx(result.challenger["tau_anom"])
    assert after["stage2"]["refit"]["pool"]["usable"] is True
    # Stage 1's record is untouched by a Stage 2 promotion.
    assert after["version"] == before["version"]
    assert after["thresholds"]["tau_sup"] == before["thresholds"]["tau_sup"]

    # What the threshold slider and the drift overlay read moves with it.
    metrics = json.loads((two_stage_artifacts / "metrics_anomaly.json").read_text())
    assert metrics["training"]["version"] == result.challenger_version
    assert metrics["training"]["threshold"]["tau"] == pytest.approx(result.challenger["tau_anom"])

    bundle = load_bundle(two_stage_artifacts)
    assert bundle.version != old_version
    assert bundle.version == before["version"] + "+" + result.challenger_version
    assert bundle.tau_anom == pytest.approx(result.challenger["tau_anom"])


# ---------------------------------------------------------------------------
# The whole retrain, against an isolated database
# ---------------------------------------------------------------------------


def _alert(session, *, index: int, flow: dict, family: str | None, src_ip: str) -> Alert:
    kind = "KNOWN" if family else "UNCLASSIFIED_ANOMALY"
    alert = Alert(
        kind=kind,
        family=family,
        detection_stage="stage1_supervised" if family else "stage2_anomaly",
        severity="high",
        risk_score=0.5,
        confidence=0.9 if family else None,
        anomaly_score=None if family else 0.3,
        detected_at=NOW + dt.timedelta(seconds=index),
        src_ip=src_ip,
        dst_ip="192.168.10.50",
        dst_port=80,
        asset_criticality="high",
        host_prior_alert_count=0,
        mitre_technique="T1499" if family else None,
        explanation={"explainer": "treeshap", "contributors": []},
        narrative="A sentence.",
        recommended_actions={
            "technique": None,
            "has_playbook": True,
            "summary": "s",
            "actions": [],
        },
        raw_flow={**flow, "_provenance": {"src_ip": "derived"}},
        dedupe_key=f"key-{index}",
        occurrence_count=1,
        first_seen=NOW,
        last_seen=NOW,
        status="open",
        model_version="test-version",
        source="replay",
    )
    session.add(alert)
    session.flush()
    return alert


@pytest.fixture
def isolated_scope(db_session, monkeypatch):
    """Point the retrain's `session_scope` at the in-memory test database."""

    @contextmanager
    def scope():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr(retrain_module, "session_scope", scope)
    return db_session


def test_a_retrain_freezes_the_feature_contract_and_records_both_stages(
    two_stage_artifacts, processed, tmp_path, isolated_scope, phase2_test, phase2_train, monkeypatch
) -> None:
    """End to end: verdicts in, a gated challenger and a logged comparison out.

    Promotion is forced so the shared-artifact properties can be checked after
    one -- the frozen scaler and the surviving Stage 2 record are exactly the
    things a promotion used to break.
    """
    monkeypatch.setattr(retrain_module, "MIN_IMPROVEMENT", -10.0)
    session = isolated_scope
    served = load_preprocessing_bundle(two_stage_artifacts / "preprocessing.pkl")
    before = _card(two_stage_artifacts)

    benign = _flows(phase2_test[phase2_test["label"] == "BENIGN"].head(12))
    attacks = _flows(phase2_train[phase2_train["label"] == "DoS Hulk"].head(6))
    for index, flow in enumerate(benign):
        alert = _alert(session, index=index, flow=flow, family=None, src_ip="172.16.0.1")
        session.add(AnalystVerdict(alert_id=alert.id, verdict="FP", model_version="test-version"))
    for index, flow in enumerate(attacks, start=100):
        alert = _alert(session, index=index, flow=flow, family="dos", src_ip="172.16.0.1")
        session.add(AnalystVerdict(alert_id=alert.id, verdict="TP", model_version="test-version"))
    session.commit()

    reports = tmp_path / "reports"
    comparison = run_retrain(
        artifacts_dir=two_stage_artifacts,
        data_dir=processed.parent,
        reports_dir=reports,
        control=False,
        **STAGE1_FAST,
    )

    assert comparison.promoted is True
    # One host supplied every false positive, so the cap leaves a single row and
    # the floor refuses it: the replay case, recorded rather than skipped.
    assert comparison.stage2["attempted"] is False
    assert "left alone" in comparison.stage2["decision"]

    # The scaler both stages read is the one that was serving.
    promoted = load_preprocessing_bundle(two_stage_artifacts / "preprocessing.pkl")
    np.testing.assert_array_equal(promoted["scaler"].center_, served["scaler"].center_)
    np.testing.assert_array_equal(promoted["scaler"].scale_, served["scaler"].scale_)
    assert promoted["schema_hash"] == served["schema_hash"]

    # Stage 2's record survived the Stage 1 promotion.
    after = _card(two_stage_artifacts)
    assert after["stage2"] == before["stage2"]
    assert after["version"] == comparison.challenger_version

    # What the API serves about Stage 1 now describes the promoted model, with
    # the caveat that its test numbers are no longer clean.
    metrics = json.loads((two_stage_artifacts / "metrics_supervised.json").read_text())
    assert metrics["test"]["version"] == comparison.challenger_version
    assert "replay of the test day" in metrics["caveat"]

    # The comparison is logged with both halves, and every label is consumed.
    run = session.execute(select(RetrainRun)).scalar_one()
    assert run.status == "completed"
    assert run.promoted is True
    assert run.comparison["stage2"]["attempted"] is False
    assert run.labels_consumed == len(benign) + len(attacks)
    pending = (
        session.execute(select(AnalystVerdict).where(AnalystVerdict.consumed_at.is_(None)))
        .scalars()
        .all()
    )
    assert pending == []

    report = (reports / "phase7_retrain.md").read_text(encoding="utf-8")
    assert "## Stage 2: the benign baseline" in report
    assert "left alone" in report


def test_a_retrain_with_no_labels_is_refused(
    two_stage_artifacts, processed, tmp_path, isolated_scope
) -> None:
    with pytest.raises(retrain_module.RetrainError, match="no unconsumed analyst verdicts"):
        run_retrain(
            artifacts_dir=two_stage_artifacts,
            data_dir=processed.parent,
            reports_dir=tmp_path / "reports",
            **STAGE1_FAST,
        )
