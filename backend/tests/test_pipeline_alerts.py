"""Phase 5 -- the dedupe upsert (`app/dedupe.py::upsert_alert`) and the alert
pipeline (`app/pipeline.py::ingest_batch`).

`test_dedupe.py` does not exist yet, so the upsert's own tests live here
too, ahead of the pipeline tests that exercise it indirectly.

The pipeline tests run against real artifacts from the `two_stage_artifacts`
fixture rather than a stub bundle, and assert through the ORM (a real
`db_session` commit) rather than by inspecting the candidate dict Task 6
builds -- a `family_matches_kind` violation, for instance, is only real once
SQLite actually checks it.

Two fixed attack families recur throughout because their behaviour against
the fixture's trained bundle is already known and deterministic: "DoS Hulk"
(family `dos`) and "FTP-Patator" (family `brute_force`) are both in the
training vocabulary and score as confident `KNOWN` alerts at the model's own
(very low, on this toy dataset) `tau_sup`. `brute_force` additionally ranks
`port_is_21` inside `narrate`'s own top-3 contributor window, which is what
makes it the fixture for the port-namespace regression test below.
`UNCLASSIFIED_ANOMALY` alerts need a different trick: Stage 1 was trained
only on `benign`/`dos`/`brute_force`, so its confidence on "Infiltration" (a
family it has never seen) already sits far under the fixture's real
`tau_sup`. Rather than lean on exactly how low that confidence happens to be,
these tests bump the loaded bundle's `tau_sup` to 0.99 first, which makes
Stage 1 decline deterministically and isolates Stage 2's path -- a plain
attribute assignment on an already-loaded bundle, not a retrain.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.config import settings
from app.dedupe import dedupe_key, upsert_alert
from app.events import EventBroker
from app.inference import load_bundle
from app.models import Alert
from app.pipeline import ingest_batch

UTC = dt.UTC
NOW = dt.datetime(2017, 7, 4, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# app/dedupe.py::upsert_alert
# ---------------------------------------------------------------------------


def _candidate(**overrides: Any) -> dict[str, Any]:
    """A fully-populated prospective `Alert` dict, shaped the way
    `app.pipeline.ingest_batch` builds one -- everything except the three
    dedupe-bookkeeping columns `upsert_alert` owns itself.

    `dedupe_key` is derived through the real `dedupe_key()` function from
    whatever `src_ip`/`family`/`detected_at` this call ends up with, rather
    than hand-typed, so a test that wants a different window or host
    exercises the real keying by overriding those fields instead of the key
    directly.
    """
    detected_at = overrides.get("detected_at", NOW)
    src_ip = overrides.get("src_ip", "172.16.0.1")
    family = overrides.get("family", "dos")
    kind = overrides.get("kind", "KNOWN")

    base: dict[str, Any] = {
        "kind": kind,
        "family": family,
        "detection_stage": "stage1_supervised",
        "severity": "high",
        "risk_score": 0.6,
        "confidence": 0.9,
        "anomaly_score": None,
        "detected_at": detected_at,
        "src_ip": src_ip,
        "src_port": None,
        "dst_ip": "192.168.10.50",
        "dst_port": 80,
        "protocol": None,
        "asset_criticality": "critical",
        "host_prior_alert_count": 0,
        "mitre_technique": "T1499",
        "explanation": {"explainer": "treeshap", "base_value": 0.1, "contributors": []},
        "narrative": "Classified as dos: placeholder evidence.",
        "recommended_actions": {
            "technique": None,
            "has_playbook": True,
            "summary": "placeholder",
            "actions": [],
        },
        "raw_flow": {"destination_port": 80},
        "dedupe_key": dedupe_key(src_ip, family if family is not None else kind, detected_at),
        "status": "open",
        "model_version": "test-1",
        "source": "replay",
        "ground_truth_label": None,
    }
    base.update(overrides)
    return base


def test_a_fresh_insert_sets_occurrence_count_one_and_first_seen_equals_last_seen(db_session):
    alert, created = upsert_alert(db_session, _candidate())
    db_session.commit()

    assert created is True
    assert alert.occurrence_count == 1
    assert alert.first_seen == alert.last_seen == NOW


def test_two_candidates_with_the_same_key_produce_one_row_with_occurrence_count_two(db_session):
    first_alert, first_created = upsert_alert(db_session, _candidate())
    db_session.commit()
    second_alert, second_created = upsert_alert(db_session, _candidate())
    db_session.commit()

    assert first_created is True
    assert second_created is False
    assert first_alert.id == second_alert.id
    assert db_session.query(Alert).count() == 1
    assert second_alert.occurrence_count == 2


def test_the_second_hit_keeps_the_higher_risk_score_even_when_it_arrives_second(db_session):
    upsert_alert(db_session, _candidate(risk_score=0.9, severity="critical"))
    db_session.commit()

    alert, created = upsert_alert(db_session, _candidate(risk_score=0.3, severity="low"))
    db_session.commit()

    assert created is False
    assert alert.risk_score == 0.9
    assert alert.severity == "critical"


def test_the_second_hit_does_not_overwrite_explanation_narrative_or_raw_flow(db_session):
    first = _candidate(
        narrative="first narrative",
        explanation={"explainer": "treeshap", "contributors": [{"feature": "flow_duration"}]},
        raw_flow={"destination_port": 80, "flow_duration": 1.0},
    )
    second = _candidate(
        narrative="second narrative",
        explanation={"explainer": "treeshap", "contributors": [{"feature": "flow_bytes_s"}]},
        raw_flow={"destination_port": 443, "flow_duration": 2.0},
    )

    upsert_alert(db_session, first)
    db_session.commit()
    alert, created = upsert_alert(db_session, second)
    db_session.commit()

    assert created is False
    assert alert.narrative == "first narrative"
    assert alert.explanation["contributors"][0]["feature"] == "flow_duration"
    assert alert.raw_flow["destination_port"] == 80


def test_two_candidates_in_different_windows_produce_two_rows(db_session):
    later = NOW + dt.timedelta(seconds=settings.dedupe_window_seconds * 2)

    upsert_alert(db_session, _candidate(detected_at=NOW))
    db_session.commit()
    alert, created = upsert_alert(db_session, _candidate(detected_at=later))
    db_session.commit()

    assert created is True
    assert db_session.query(Alert).count() == 2
    assert alert.first_seen == later


# ---------------------------------------------------------------------------
# app/pipeline.py::ingest_batch
# ---------------------------------------------------------------------------


def test_a_batch_with_no_alerts_produces_nothing_and_publishes_nothing(
    db_session, two_stage_artifacts
):
    """`decisions` is handed over directly rather than produced by
    `score_batch`: the point under test is what `ingest_batch` does with an
    all-`None` batch, not whether a particular flow happens to score that
    way on a toy fixture."""
    bundle = load_bundle(two_stage_artifacts)
    flows = [{"destination_port": 443, "flow_duration": 1000.0 + i} for i in range(5)]
    decisions = [
        {
            "kind": None,
            "family": None,
            "detection_stage": None,
            "confidence": 0.01,
            "anomaly_score": None,
            "model_version": bundle.version,
        }
        for _ in flows
    ]
    broker = EventBroker()
    queue = broker.subscribe()

    alerts = ingest_batch(
        db_session, flows=flows, decisions=decisions, bundle=bundle, detected_at=NOW, broker=broker
    )

    assert alerts == []
    assert queue.empty()
    assert db_session.query(Alert).count() == 0


def test_known_and_anomaly_alerts_are_fully_formed_and_satisfy_family_matches_kind(
    db_session, two_stage_artifacts, phase2_train, phase2_val
):
    """The project's two headline claims, pinned through a real commit rather
    than by inspecting a dict: a KNOWN alert is named, mapped and advised; an
    UNCLASSIFIED_ANOMALY alert is deliberately none of those things. Neither
    can violate `family_matches_kind` -- SQLite only actually checks that
    once this test commits, not when the candidate dict is built.
    """
    bundle = load_bundle(two_stage_artifacts)

    known_flows = (
        phase2_train[phase2_train["label"] == "DoS Hulk"]
        .head(5)
        .drop(columns="label")
        .to_dict("records")
    )
    known_decisions = bundle.score_batch(known_flows)
    assert all(d["kind"] == "KNOWN" for d in known_decisions)

    known_alerts = ingest_batch(
        db_session, flows=known_flows, decisions=known_decisions, bundle=bundle, detected_at=NOW
    )
    db_session.commit()  # family_matches_kind fires for real right here

    assert known_alerts
    known = known_alerts[0]
    assert known.kind == "KNOWN"
    assert known.family == "dos"
    assert known.mitre_technique == "T1499"
    assert known.narrative
    assert known.explanation["explainer"] == "treeshap"
    assert known.recommended_actions["has_playbook"] is True
    assert known.raw_flow["_provenance"]["src_ip"] == "derived"

    bundle.tau_sup = 0.99  # see module docstring
    anomaly_flows = (
        phase2_val[phase2_val["label"] == "Infiltration"].drop(columns="label").to_dict("records")
    )
    anomaly_decisions = bundle.score_batch(anomaly_flows)
    assert any(d["kind"] == "UNCLASSIFIED_ANOMALY" for d in anomaly_decisions)

    anomaly_alerts = ingest_batch(
        db_session,
        flows=anomaly_flows,
        decisions=anomaly_decisions,
        bundle=bundle,
        detected_at=NOW + dt.timedelta(hours=1),
    )
    db_session.commit()

    anomalies = [a for a in anomaly_alerts if a.kind == "UNCLASSIFIED_ANOMALY"]
    assert anomalies
    anomaly = anomalies[0]
    assert anomaly.family is None
    assert anomaly.mitre_technique is None
    assert anomaly.recommended_actions["has_playbook"] is False
    assert anomaly.explanation["explainer"] == "reconstruction_error"
    assert anomaly.raw_flow["_provenance"]["src_ip"] == "derived"


def test_a_known_alerts_narrative_reports_the_observed_destination_port(
    db_session, two_stage_artifacts, phase2_train
):
    """The Task 2 review's warning, pinned rather than merely trusted:
    `narrate(..., flow=...)` must receive the *raw* flow dict, keyed by
    `destination_port` (`training.features.PORT_COLUMN`), never one renamed
    to `dst_port`. FTP-Patator's port (21) is confirmed to rank inside
    `narrate`'s own top-3 contributor window against this fixture's trained
    model, so a correctly-wired sentence must name it; a pipeline that
    passed the wrong dict would drop this clause with no error at all.
    """
    bundle = load_bundle(two_stage_artifacts)
    flows = (
        phase2_train[phase2_train["label"] == "FTP-Patator"]
        .head(5)
        .drop(columns="label")
        .to_dict("records")
    )
    decisions = bundle.score_batch(flows)
    assert all(d["kind"] == "KNOWN" and d["family"] == "brute_force" for d in decisions)

    alerts = ingest_batch(
        db_session, flows=flows, decisions=decisions, bundle=bundle, detected_at=NOW
    )
    db_session.commit()

    assert alerts
    assert "Observed destination port: 21." in alerts[0].narrative


def test_a_burst_in_one_window_collapses_to_one_row_with_two_published_events(
    db_session, two_stage_artifacts, phase2_train
):
    """The Phase 5 checkpoint's actual claim, pinned in a unit test before
    anyone runs a replay: a burst from one host in one window is one row,
    and the ticker sees it collapse rather than flood."""
    bundle = load_bundle(two_stage_artifacts)
    flows = (
        phase2_train[phase2_train["label"] == "DoS Hulk"]
        .head(2)
        .drop(columns="label")
        .to_dict("records")
    )
    decisions = bundle.score_batch(flows)
    assert all(d["kind"] == "KNOWN" for d in decisions)

    broker = EventBroker()
    queue = broker.subscribe()

    first = ingest_batch(
        db_session,
        flows=[flows[0]],
        decisions=[decisions[0]],
        bundle=bundle,
        detected_at=NOW,
        broker=broker,
        start_index=0,
    )
    second = ingest_batch(
        db_session,
        flows=[flows[1]],
        decisions=[decisions[1]],
        bundle=bundle,
        detected_at=NOW,
        broker=broker,
        start_index=1,
    )

    # Deliberately not committed yet: the point is that upsert_alert's own
    # flush, not this test's commit, is what already makes both rows and
    # both ids real.
    assert db_session.query(Alert).count() == 1
    assert first[0].id == second[0].id
    assert second[0].occurrence_count == 2

    first_event = queue.get_nowait()
    second_event = queue.get_nowait()
    assert first_event["occurrence_count"] == 1
    assert second_event["occurrence_count"] == 2
    assert first_event["id"] == second_event["id"] == second[0].id
    assert db_session.get(Alert, first_event["id"]) is not None

    db_session.commit()


def test_host_prior_alert_count_reflects_earlier_batches_not_this_ones_inserts(
    db_session, two_stage_artifacts, phase2_train
):
    """`dos` and `brute_force` are the only two attack classes this fixture's
    Stage 1 was trained on (`web_attack`/Heartbleed falls below the support
    floor, the same way `test_loao.py` finds it does), and both are sourced
    from `_KALI` in `app.topology`. That makes them the pair to use for "two
    distinct new rows from the same host in one batch": a third, made-up
    family is not available, and a held-out family like `port_scan` cannot
    stand in for it either, because Stage 1 can only ever predict a class it
    was actually trained on.
    """
    bundle = load_bundle(two_stage_artifacts)

    dos_flows = (
        phase2_train[phase2_train["label"] == "DoS Hulk"]
        .head(2)
        .drop(columns="label")
        .to_dict("records")
    )
    dos_decisions = bundle.score_batch(dos_flows)
    assert all(d["kind"] == "KNOWN" and d["family"] == "dos" for d in dos_decisions)

    ingest_batch(
        db_session, flows=dos_flows, decisions=dos_decisions, bundle=bundle, detected_at=NOW
    )
    db_session.commit()
    assert db_session.query(Alert).count() == 1  # one dedupe bucket, both flows from _KALI

    # A later window, so this batch's own `dos` flows are a fresh bucket --
    # not a hit against the row the first batch already committed -- and
    # therefore a second brand-new row alongside `brute_force`, both from
    # the same host, both inserted in this same call.
    later = NOW + dt.timedelta(minutes=30)
    more_dos_flows = (
        phase2_train[phase2_train["label"] == "DoS Hulk"]
        .iloc[2:4]
        .drop(columns="label")
        .to_dict("records")
    )
    ftp_flows = (
        phase2_train[phase2_train["label"] == "FTP-Patator"]
        .head(2)
        .drop(columns="label")
        .to_dict("records")
    )
    second_flows = more_dos_flows + ftp_flows
    second_decisions = bundle.score_batch(second_flows)
    assert all(d["kind"] == "KNOWN" for d in second_decisions)
    assert {d["family"] for d in second_decisions} == {"dos", "brute_force"}

    second_alerts = ingest_batch(
        db_session, flows=second_flows, decisions=second_decisions, bundle=bundle, detected_at=later
    )
    db_session.commit()

    # Two brand-new rows in *this* batch (a new `dos` window and the first
    # `brute_force` ever), both sourced from _KALI -- neither may count the
    # other, only the one row the first batch already committed.
    assert db_session.query(Alert).count() == 3
    assert {a.family for a in second_alerts} == {"dos", "brute_force"}
    assert all(a.host_prior_alert_count == 1 for a in second_alerts)


def test_a_naive_detected_at_is_rejected_rather_than_silently_stored(
    db_session, two_stage_artifacts, phase2_train
):
    bundle = load_bundle(two_stage_artifacts)
    flows = (
        phase2_train[phase2_train["label"] == "DoS Hulk"]
        .head(1)
        .drop(columns="label")
        .to_dict("records")
    )
    decisions = bundle.score_batch(flows)
    naive = dt.datetime(2017, 7, 4, 12, 0)  # no tzinfo

    with pytest.raises(ValueError, match="tz-aware"):
        ingest_batch(db_session, flows=flows, decisions=decisions, bundle=bundle, detected_at=naive)

    assert db_session.query(Alert).count() == 0


def test_a_batch_with_only_stage2_alerts_never_calls_the_stage1_explainer(
    db_session, two_stage_artifacts, phase2_val, monkeypatch
):
    bundle = load_bundle(two_stage_artifacts)
    bundle.tau_sup = 0.99  # see module docstring
    flows = (
        phase2_val[phase2_val["label"] == "Infiltration"].drop(columns="label").to_dict("records")
    )
    decisions = bundle.score_batch(flows)
    assert all(d["kind"] == "UNCLASSIFIED_ANOMALY" for d in decisions)

    def _must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("explain_supervised must not run for an all-Stage-2 batch")

    monkeypatch.setattr("app.pipeline.explain_supervised", _must_not_be_called)

    alerts = ingest_batch(
        db_session, flows=flows, decisions=decisions, bundle=bundle, detected_at=NOW
    )
    db_session.commit()

    assert alerts


def test_a_batch_with_only_stage1_alerts_never_calls_the_stage2_explainer(
    db_session, two_stage_artifacts, phase2_train, monkeypatch
):
    bundle = load_bundle(two_stage_artifacts)
    flows = (
        phase2_train[phase2_train["label"] == "DoS Hulk"]
        .head(5)
        .drop(columns="label")
        .to_dict("records")
    )
    decisions = bundle.score_batch(flows)
    assert all(d["kind"] == "KNOWN" for d in decisions)

    def _must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("explain_anomaly must not run for an all-Stage-1 batch")

    monkeypatch.setattr("app.pipeline.explain_anomaly", _must_not_be_called)

    alerts = ingest_batch(
        db_session, flows=flows, decisions=decisions, bundle=bundle, detected_at=NOW
    )
    db_session.commit()

    assert alerts
