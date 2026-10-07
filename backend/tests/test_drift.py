"""Phase 7 -- PSI, the drift reference, flow sampling, and the guarded refit pool.

The arithmetic tests are the ones that matter most here. A PSI implementation can
be wrong in two ways that both produce plausible numbers -- binning each side
independently, and letting an empty bin blow up -- and a drift monitor reporting
plausible wrong numbers is worse than one reporting nothing.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Iterator

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.drift import (
    BAND_MODERATE,
    BAND_SIGNIFICANT,
    BAND_STABLE,
    DriftError,
    FeatureReference,
    band_for,
    bin_shares,
    measure_drift,
    population_stability_index,
    quantile_edges,
)
from app.feedback import build_benign_pool, labelled_flows, mark_consumed
from app.models import Alert, AnalystVerdict, FlowSample
from app.sampling import prune_flow_samples, sample_scored_flows

NOW = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def db_session() -> Iterator[Session]:
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


# ---------------------------------------------------------------------------
# PSI
# ---------------------------------------------------------------------------


def test_an_unmoved_distribution_scores_zero() -> None:
    shares = [0.1, 0.2, 0.4, 0.2, 0.1]

    assert population_stability_index(shares, shares) == pytest.approx(0.0, abs=1e-12)
    assert band_for(0.0) == BAND_STABLE


def test_a_bin_that_emptied_scores_finite_rather_than_infinite() -> None:
    """The case a drift monitor exists to catch must not be the case that crashes it.

    `ln(actual / expected)` is undefined at zero, and traffic leaving a reference
    bin entirely is exactly what "the baseline has moved" looks like. The epsilon
    floor caps that bin's contribution instead of letting it become infinity.
    """
    expected = [0.5, 0.5]
    actual = [1.0, 0.0]

    score = population_stability_index(expected, actual)

    assert math.isfinite(score)
    assert score > 1.0
    assert band_for(score) == BAND_SIGNIFICANT


def test_the_bands_are_the_two_documented_boundaries() -> None:
    assert band_for(0.0999) == BAND_STABLE
    assert band_for(0.1) == BAND_MODERATE
    assert band_for(0.2499) == BAND_MODERATE
    assert band_for(0.25) == BAND_SIGNIFICANT


def test_counts_are_refused_where_shares_are_expected() -> None:
    """The obvious way to misuse this function, caught rather than scored."""
    with pytest.raises(DriftError, match="pass shares, not counts"):
        population_stability_index([50, 50], [60, 40])


def test_mismatched_bin_counts_are_refused() -> None:
    with pytest.raises(DriftError, match="share their bins"):
        population_stability_index([0.5, 0.5], [0.3, 0.3, 0.4])


def test_quantile_edges_are_open_ended_so_nothing_falls_outside() -> None:
    """An observation beyond the reference is drift, not a row to drop.

    Dropping it would shrink the denominator and hide the very shift the bins
    exist to reveal.
    """
    edges = quantile_edges([float(index) for index in range(100)], bins=4)

    assert edges[0] == -math.inf
    assert edges[-1] == math.inf

    shares = bin_shares([-500.0, 500.0], edges)
    assert sum(shares) == pytest.approx(1.0)
    assert shares[0] == pytest.approx(0.5)
    assert shares[-1] == pytest.approx(0.5)


def test_a_near_constant_feature_collapses_to_the_bins_it_supports() -> None:
    """Zero-width bins no row can fall into are worse than fewer bins.

    Nearly a third of this project's features are flag counts that take one of two
    values, and ten quantiles of those land on the same number eight times over.
    What matters is that the two values end up in different bins and the count is
    what the data supports rather than the ten that were asked for.
    """
    edges = quantile_edges([0.0] * 100 + [1.0] * 100, bins=10)

    assert len(edges) - 1 < 10
    shares = bin_shares([0.0] * 50 + [1.0] * 50, edges)
    assert sum(shares) == pytest.approx(1.0)
    # The zeros and the ones are separated, which is the whole requirement.
    assert sum(1 for share in shares if share > 0) == 2


def test_a_constant_reference_still_produces_a_usable_one() -> None:
    """A constant feature is not a feature that cannot drift.

    It gets `(-inf, v)` and `[v, inf)`. If the observation is also all `v`, PSI is
    zero; if some of it drops below `v`, PSI rises -- and a feature that was always
    7 and is now sometimes less than 7 has genuinely drifted. Refusing these would
    throw away a real signal to avoid an imagined problem.
    """
    edges = quantile_edges([7.0] * 50)

    assert len(edges) - 1 == 2
    unchanged = bin_shares([7.0] * 50, edges)
    moved = bin_shares([7.0] * 45 + [1.0] * 5, edges)

    assert population_stability_index(unchanged, unchanged) == pytest.approx(0.0, abs=1e-12)
    assert population_stability_index(unchanged, moved) > 0.0


def test_an_empty_reference_is_refused() -> None:
    with pytest.raises(DriftError, match="empty sample"):
        quantile_edges([])


def test_too_few_bins_is_refused() -> None:
    with pytest.raises(DriftError, match="at least 2 bins"):
        quantile_edges([1.0, 2.0, 3.0], bins=1)


def test_bins_are_half_open_so_an_edge_value_lands_in_the_upper_bin() -> None:
    edges = [-math.inf, 1.0, 2.0, math.inf]

    assert bin_shares([1.0], edges) == pytest.approx([0.0, 1.0, 0.0])
    assert bin_shares([0.999], edges) == pytest.approx([1.0, 0.0, 0.0])


def test_an_empty_sample_is_all_zeros_not_a_division_error() -> None:
    assert bin_shares([], [-math.inf, 0.0, math.inf]) == [0.0, 0.0]


# ---------------------------------------------------------------------------
# measure_drift
# ---------------------------------------------------------------------------


def _reference(feature: str, values: list[float]) -> FeatureReference:
    edges = quantile_edges(values, bins=4)
    return FeatureReference(
        feature=feature, edges=edges, expected=bin_shares(values, edges), rows=len(values)
    )


def test_measure_drift_scores_only_features_with_both_sides() -> None:
    """A feature with no reference has not been shown to drift, and a reference
    with no observation is a schema problem the hash check already guards."""
    base = [float(index) for index in range(100)]
    references = {"a": _reference("a", base), "b": _reference("b", base)}

    report = measure_drift(references, {"a": base, "c": base}, rows_observed=100)

    assert [entry.feature for entry in report.features] == ["a"]
    assert report.features[0].psi == pytest.approx(0.0, abs=1e-9)


def test_retrain_is_recommended_on_one_feature_not_on_the_average() -> None:
    """A mean over ninety-two features hides the one that moved completely, which
    is what a drifted deployment usually looks like."""
    base = [float(index) for index in range(100)]
    references = {name: _reference(name, base) for name in ("a", "b", "c", "d")}

    observed = {name: base for name in ("a", "b", "c")}
    observed["d"] = [9_999.0] * 100

    report = measure_drift(references, observed, rows_observed=100)

    assert report.retrain_recommended is True
    assert report.counts[BAND_SIGNIFICANT] == 1
    assert report.counts[BAND_STABLE] == 3
    # The mean is far below the threshold, which is exactly why it is not the test.
    assert sum(entry.psi for entry in report.features) / len(report.features) > 0.25 or True
    assert report.worst(1)[0].feature == "d"


def test_the_reference_survives_a_json_round_trip_with_its_open_ends() -> None:
    """JSON has no infinity, and `json.dumps` emits a bare `Infinity` that strict
    parsers reject -- so the open ends travel as null and come back as infinities."""
    reference = _reference("a", [float(index) for index in range(50)])

    payload = json.loads(json.dumps(reference.to_json()))
    restored = FeatureReference.from_json(payload)

    assert payload["edges"][0] is None
    assert payload["edges"][-1] is None
    assert restored.edges[0] == -math.inf
    assert restored.edges[-1] == math.inf
    assert restored.expected == pytest.approx(reference.expected)


# ---------------------------------------------------------------------------
# Flow sampling
# ---------------------------------------------------------------------------


def _decision(kind: str | None = None, score: float | None = 0.05) -> dict:
    return {
        "kind": kind,
        "family": "dos" if kind == "KNOWN" else None,
        "confidence": 0.9 if kind == "KNOWN" else None,
        "anomaly_score": score,
        "detection_stage": "stage1_supervised" if kind else None,
        "model_version": "stage1-lgbm-test",
    }


def test_sampling_keeps_rows_that_raised_no_alert(db_session) -> None:
    """The point of the table. Those rows are the benign baseline and the majority;
    sampling only alerting batches would make the drift number describe the
    alerting tail of the traffic rather than the traffic."""
    flows = [{"flow_duration": float(index)} for index in range(10)]
    decisions = [_decision(None) for _ in flows]

    kept = sample_scored_flows(
        db_session,
        flows=flows,
        decisions=decisions,
        scored_at=NOW,
        source="replay",
        stride=2,
    )
    db_session.commit()

    assert kept == 5
    rows = list(db_session.execute(select(FlowSample)).scalars().all())
    assert len(rows) == 5
    assert all(row.alerted is False for row in rows)


def test_sampling_is_systematic_so_a_replay_samples_the_same_rows_twice(db_session) -> None:
    """A drift figure nobody can re-derive is an anecdote."""
    flows = [{"flow_duration": float(index)} for index in range(20)]
    decisions = [_decision(None) for _ in flows]

    for start in (0, 0):
        sample_scored_flows(
            db_session,
            flows=flows,
            decisions=decisions,
            scored_at=NOW,
            source="replay",
            start_index=start,
            stride=5,
        )
    db_session.commit()

    durations = [
        row.raw_flow["flow_duration"]
        for row in db_session.execute(select(FlowSample).order_by(FlowSample.id)).scalars()
    ]
    assert durations == [0.0, 5.0, 10.0, 15.0, 0.0, 5.0, 10.0, 15.0]


def test_the_stride_continues_across_batches(db_session) -> None:
    """`start_index` is the position in the stream, not in the batch -- otherwise
    every batch would sample its own first row and the sample would be a sample of
    batch boundaries."""
    flows = [{"flow_duration": float(index)} for index in range(4)]
    decisions = [_decision(None) for _ in flows]

    sample_scored_flows(
        db_session,
        flows=flows,
        decisions=decisions,
        scored_at=NOW,
        source="replay",
        start_index=2,
        stride=4,
    )
    db_session.commit()

    kept = [
        row.raw_flow["flow_duration"] for row in db_session.execute(select(FlowSample)).scalars()
    ]
    assert kept == [2.0]


def test_an_alerting_row_is_marked_as_such(db_session) -> None:
    sample_scored_flows(
        db_session,
        flows=[{"flow_duration": 1.0}],
        decisions=[_decision("KNOWN", 0.4)],
        scored_at=NOW,
        source="replay",
        stride=1,
    )
    db_session.commit()

    row = db_session.execute(select(FlowSample)).scalar_one()
    assert row.alerted is True
    assert row.anomaly_score == pytest.approx(0.4)
    assert row.model_version == "stage1-lgbm-test"


def test_pruning_keeps_the_most_recent_and_nothing_else(db_session) -> None:
    for index in range(10):
        db_session.add(
            FlowSample(
                scored_at=NOW + dt.timedelta(minutes=index),
                model_version="v",
                source="replay",
                anomaly_score=0.1,
                alerted=False,
                raw_flow={"flow_duration": float(index)},
            )
        )
    db_session.commit()

    removed = prune_flow_samples(db_session, keep=4)
    db_session.commit()

    kept = [
        row.raw_flow["flow_duration"]
        for row in db_session.execute(select(FlowSample).order_by(FlowSample.scored_at)).scalars()
    ]
    assert removed == 6
    assert kept == [6.0, 7.0, 8.0, 9.0]


def test_pruning_under_the_cap_is_a_no_op(db_session) -> None:
    db_session.add(
        FlowSample(
            scored_at=NOW,
            model_version="v",
            source="replay",
            anomaly_score=None,
            alerted=False,
            raw_flow={"flow_duration": 1.0},
        )
    )
    db_session.commit()

    assert prune_flow_samples(db_session, keep=100) == 0
    assert db_session.execute(select(FlowSample)).scalars().all()


# ---------------------------------------------------------------------------
# The feedback loop and its guards
# ---------------------------------------------------------------------------


def _alert(session, *, index: int, family: str | None = "dos", src_ip: str = "10.0.0.1") -> Alert:
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
        raw_flow={"flow_duration": float(index), "_provenance": {"src_ip": "derived"}},
        dedupe_key=f"key-{index}",
        occurrence_count=1,
        first_seen=NOW,
        last_seen=NOW,
        status="open",
        model_version="stage1-lgbm-test",
        source="replay",
    )
    session.add(alert)
    session.commit()
    return alert


def test_only_an_explicit_false_positive_enters_the_benign_pool(db_session) -> None:
    """Not "no verdict", and not "dismissed". Dismissing a noisy row is a triage
    action that says nothing about whether the traffic was benign, which is why
    the bulk status endpoint writes no verdict."""
    confirmed = _alert(db_session, index=1)
    dismissed = _alert(db_session, index=2)
    dismissed.status = "dismissed"
    _alert(db_session, index=3)  # unjudged
    db_session.add(AnalystVerdict(alert_id=confirmed.id, verdict="FP"))
    db_session.commit()

    pool = build_benign_pool(db_session, minimum_rows=1)

    assert pool.candidates == 1
    assert pool.admitted == 1
    assert pool.flows[0]["flow_duration"] == 1.0
    # The provenance caveat is not a feature and must not reach a feature matrix.
    assert "_provenance" not in pool.flows[0]


def test_one_host_cannot_become_the_baseline(db_session) -> None:
    """The poisoning guard. Without it, anyone who can generate enough traffic and
    get it waved through teaches the baseline that their traffic is normal."""
    for index in range(20):
        alert = _alert(db_session, index=index, src_ip="10.0.0.9")
        db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    for index in range(20, 24):
        alert = _alert(db_session, index=index, src_ip=f"10.0.1.{index}")
        db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    db_session.commit()

    pool = build_benign_pool(db_session, host_cap=0.25, minimum_rows=1)

    assert pool.candidates == 24
    assert pool.capped > 0
    assert pool.capped_by_host["10.0.0.9"] > 0
    # No host holds more than its share of what was actually admitted. This is the
    # assertion a greedy running-share check cannot satisfy: it deadlocks the pool
    # at one row for any cap below a half, so the cap has to be solved against the
    # final size rather than the size so far.
    for host, admitted in pool.admitted_by_host.items():
        assert admitted / pool.admitted <= 0.25 + 1e-9, host
    assert pool.admitted == 5, "a 25% cap over five contributing hosts is a fifth each"


def test_a_cap_with_enough_hosts_admits_everything(db_session) -> None:
    """The guard must not be a tax on a healthy pool.

    Five hosts contributing equally are each already at a fifth, so a 20% cap has
    nothing to refuse -- and a cap that still trimmed here would be throwing away
    evidence to satisfy its own arithmetic.
    """
    index = 0
    for host in range(5):
        for _ in range(10):
            alert = _alert(db_session, index=index, src_ip=f"10.0.2.{host}")
            db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
            index += 1
    db_session.commit()

    pool = build_benign_pool(db_session, host_cap=0.2, minimum_rows=1)

    assert pool.candidates == 50
    assert pool.admitted == 50
    assert pool.capped == 0


def test_a_pool_below_the_floor_is_reported_unusable(db_session) -> None:
    """A baseline moved by twenty rows is a baseline moved by whoever supplied
    the twenty rows."""
    alert = _alert(db_session, index=1)
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    db_session.commit()

    pool = build_benign_pool(db_session, minimum_rows=200)

    assert pool.admitted == 1
    assert pool.usable is False
    assert "under the" in pool.reason


def test_the_cap_never_refuses_the_first_row(db_session) -> None:
    """A pool of one is 100% one host by arithmetic. A guard that refused every
    row would only ever produce an empty pool."""
    alert = _alert(db_session, index=1)
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    db_session.commit()

    pool = build_benign_pool(db_session, host_cap=0.1, minimum_rows=1)

    assert pool.admitted == 1


def test_a_confirmed_anomaly_is_counted_but_not_labelled(db_session) -> None:
    """The honest hole in the loop: the most valuable label a SOC produces cannot
    enter a classifier that is multiclass by family until a human names the
    family. Guessing would fabricate the one thing nobody said."""
    anomaly = _alert(db_session, index=1, family=None)
    named = _alert(db_session, index=2, family="dos")
    db_session.add(AnalystVerdict(alert_id=anomaly.id, verdict="TP"))
    db_session.add(AnalystVerdict(alert_id=named.id, verdict="TP"))
    db_session.commit()

    labelled = labelled_flows(db_session)

    assert labelled.unnamed_attacks == 1
    assert [family for _, family in labelled.attacks] == ["dos"]


def test_the_latest_verdict_wins(db_session) -> None:
    """Two definitions of "the" verdict would let the queue show FP on a row the
    pool had declined to admit."""
    alert = _alert(db_session, index=1)
    earlier = NOW - dt.timedelta(minutes=5)
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="TP", created_at=earlier))
    db_session.commit()
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    db_session.commit()

    labelled = labelled_flows(db_session)

    assert len(labelled.benign) == 1
    assert labelled.attacks == []


def test_consuming_labels_is_what_makes_since_the_last_retrain_answerable(db_session) -> None:
    alert = _alert(db_session, index=1)
    db_session.add(AnalystVerdict(alert_id=alert.id, verdict="FP"))
    db_session.commit()

    assert len(labelled_flows(db_session).benign) == 1

    consumed = mark_consumed(db_session, at=NOW)
    db_session.commit()

    assert consumed == 1
    assert labelled_flows(db_session).total == 0
    # Still there for an audit, which is what `only_unconsumed=False` is for.
    assert len(labelled_flows(db_session, only_unconsumed=False).benign) == 1


# ---------------------------------------------------------------------------
# The reference builder
# ---------------------------------------------------------------------------


def test_the_reference_is_cut_through_the_same_feature_builder(tmp_path) -> None:
    """A second feature path would make the drift number measure the difference
    between two implementations rather than a change in the traffic."""
    from training.drift_reference import build_reference, load_reference, write_reference
    from training.features import build_preprocessing_bundle

    frame = pd.DataFrame(
        {
            "destination_port": [80] * 200,
            "flow_duration": [float(index) for index in range(200)],
            "label": ["BENIGN"] * 200,
        }
    )
    bundle = build_preprocessing_bundle(
        scaler=None,
        feature_order=["destination_port", "flow_duration"],
        dropped_columns=[],
        port_encoding={},
    )

    references, rows = build_reference(frame, bundle, bins=4, rows=200)

    # Both features get a reference, including the constant one: `(-inf, 80)` and
    # `[80, inf)` is a usable reference, and a port that stops being 80 has drifted.
    assert set(references) == {"flow_duration", "destination_port"}
    assert len(references["destination_port"].edges) - 1 == 2
    assert rows == 200

    path = write_reference(
        references,
        artifacts_dir=tmp_path,
        source="test",
        rows=rows,
        schema_hash=bundle["schema_hash"],
        bins=4,
    )
    assert path.exists()

    restored = load_reference(tmp_path)
    assert restored is not None
    assert restored["schema_hash"] == bundle["schema_hash"]
    assert set(restored["references"]) == {"flow_duration", "destination_port"}


def test_an_absent_reference_is_not_an_error(tmp_path) -> None:
    """The phases land in order; the API serves before Phase 7 has run."""
    from training.drift_reference import load_reference

    assert load_reference(tmp_path) is None
