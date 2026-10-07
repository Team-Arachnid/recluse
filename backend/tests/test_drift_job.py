"""Phase 7 -- the nightly drift job, run against real artifacts.

`app/drift.py`'s arithmetic is tested on its own in `test_drift.py`. What these
pin is the job around it: that it reads the samples through the same feature
builder training used, that it refuses to produce a number it cannot stand
behind, and that a snapshot is attributed to the model that actually scored the
traffic in its window.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import pytest
from sqlalchemy import select

import training.drift_job as drift_job_module
from app.config import settings
from app.models import DriftFeature, DriftRun, FlowSample
from training.drift_job import DriftJobError, run_drift_job
from training.drift_reference import build_reference, write_reference
from training.features import load_preprocessing_bundle

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def scope(db_session, monkeypatch):
    @contextmanager
    def isolated():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr(drift_job_module, "session_scope", isolated)
    monkeypatch.setattr(settings, "drift_min_rows", 20)
    return db_session


@pytest.fixture
def referenced(two_stage_artifacts, phase2_train):
    """Artifacts plus a drift reference cut from the training split."""
    bundle = load_preprocessing_bundle(two_stage_artifacts / "preprocessing.pkl")
    references, rows = build_reference(phase2_train, bundle, rows=10_000)
    write_reference(
        references,
        artifacts_dir=two_stage_artifacts,
        source="train.parquet (test fixture)",
        rows=rows,
        schema_hash=bundle["schema_hash"],
        bins=10,
    )
    return two_stage_artifacts


def _samples(session, frame, *, versions: list[str], start: dt.datetime) -> None:
    flows = frame.drop(columns=["label"]).to_dict(orient="records")
    for index, flow in enumerate(flows):
        session.add(
            FlowSample(
                scored_at=start + dt.timedelta(seconds=index),
                model_version=versions[index * len(versions) // len(flows)],
                source="replay",
                anomaly_score=0.01,
                alerted=False,
                raw_flow={key: float(value) for key, value in flow.items()},
            )
        )
    session.commit()


def test_a_snapshot_is_stored_per_feature(scope, referenced, phase2_test) -> None:
    _samples(scope, phase2_test.head(80), versions=["v1"], start=NOW - dt.timedelta(hours=1))

    run, report = run_drift_job(artifacts_dir=referenced, now=NOW, window_hours=24, prune=False)

    assert run is not None
    assert run.rows_observed == 80
    assert run.features_scored == len(report.features) > 0
    stored = scope.execute(select(DriftFeature).where(DriftFeature.run_id == run.id)).scalars()
    assert {row.feature for row in stored} == {entry.feature for entry in report.features}
    # Every sample came from a replay, and the snapshot says so.
    assert "dataset replay" in (run.notes or "")


def test_a_window_spanning_a_promotion_is_attributed_to_the_latest_scorer(
    scope, referenced, phase2_test
) -> None:
    """Not to whichever version name sorts last. "stage1-rf-..." sorts after
    "stage1-lgbm-...", so the old rule attributed a window to an older RF champion
    whenever one had scored any of it."""
    _samples(
        scope,
        phase2_test.head(80),
        versions=["stage1-rf-old", "stage1-lgbm-new"],
        start=NOW - dt.timedelta(hours=1),
    )

    run, _ = run_drift_job(artifacts_dir=referenced, now=NOW, window_hours=24, prune=False)

    assert run.model_version == "stage1-lgbm-new"
    assert "spans 2 model versions" in (run.notes or "")


def test_too_few_samples_records_the_run_and_scores_nothing(scope, referenced, phase2_test) -> None:
    _samples(scope, phase2_test.head(5), versions=["v1"], start=NOW - dt.timedelta(hours=1))

    run, report = run_drift_job(artifacts_dir=referenced, now=NOW, window_hours=24, prune=False)

    assert run.features_scored == 0
    assert report.features == []
    assert "under the" in (run.notes or "")


def test_no_reference_is_a_refusal_not_a_number(scope, two_stage_artifacts) -> None:
    with pytest.raises(DriftJobError, match="no drift reference"):
        run_drift_job(artifacts_dir=two_stage_artifacts, now=NOW)


def test_a_reference_from_another_feature_contract_is_refused(scope, referenced) -> None:
    path = referenced / "drift_reference.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"schema_hash": "sha256:', '"schema_hash": "sha256:0'
        ),
        encoding="utf-8",
    )

    with pytest.raises(DriftJobError, match="different feature order"):
        run_drift_job(artifacts_dir=referenced, now=NOW)


def test_a_dry_run_measures_and_stores_nothing(scope, referenced, phase2_test) -> None:
    _samples(scope, phase2_test.head(80), versions=["v1"], start=NOW - dt.timedelta(hours=1))

    run, report = run_drift_job(artifacts_dir=referenced, now=NOW, dry_run=True)

    assert run is None
    assert report.features
    assert scope.execute(select(DriftRun)).scalars().all() == []
