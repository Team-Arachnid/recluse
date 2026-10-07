"""Phase 8 -- `make seed`: real flows, the real pipeline, nothing invented.

The seed exists so the dashboard never opens empty, and the brief forbids a
single number on it that did not come from a model run. These tests pin both
halves: what the seed writes is what the pipeline produces from the committed
flows, and what it does not write -- verdicts, and anything over existing data --
it refuses to.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.db
import training.drift_job
from app import seed
from app.config import settings
from app.db import Base
from app.inference import ModelBundle, load_bundle
from app.models import Alert, AnalystVerdict, FlowSample
from app.release import DEMO_FLOWS_CARD, DEMO_FLOWS_FILE

# The labels of the days that fitted weights. None may appear in the demo sample.
TRAINING_DAY_LABELS = {
    "FTP-Patator",
    "SSH-Patator",
    "DoS Hulk",
    "DoS GoldenEye",
    "DoS slowloris",
    "DoS Slowhttptest",
    "Heartbleed",
}


@pytest.fixture
def private_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    """A database of the seed's own, so its writes cannot reach other tests'."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'seed.db'}", future=True)
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

    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(app.db, "session_scope", scope)
    monkeypatch.setattr(training.drift_job, "session_scope", scope)
    yield factory
    engine.dispose()


@pytest.fixture(scope="module")
def bundle() -> ModelBundle:
    loaded = load_bundle(settings.artifacts_path)
    assert loaded.stage1_ready and loaded.stage2_ready
    return loaded


@pytest.fixture(scope="module")
def demo() -> pd.DataFrame:
    return pd.read_parquet(settings.release_path / DEMO_FLOWS_FILE)


@pytest.fixture(scope="module")
def mixed_flows(demo: pd.DataFrame) -> tuple[list[dict], list[str]]:
    """Up to 60 flows of every label in capture order, so both stages fire."""
    picked = demo.groupby("label", sort=False).head(60).sort_index()
    labels = [str(label) for label in picked["label"]]
    return picked.drop(columns="label").to_dict("records"), labels


def test_the_demo_sample_is_held_out_traffic_and_its_card_says_so(demo: pd.DataFrame) -> None:
    card = json.loads((settings.release_path / DEMO_FLOWS_CARD).read_text())

    assert len(demo) == card["rows"] == sum(entry["rows"] for entry in card["splits"].values())
    assert set(card["splits"]) == {"val", "test"}
    assert not set(demo["label"]) & TRAINING_DAY_LABELS
    # The rare families are kept whole, which is what the card claims.
    assert (demo["label"] == "Infiltration").sum() == card["splits"]["val"]["labels"][
        "Infiltration"
    ]
    assert "Sharafaldin" in card["dataset"]["citation"]


def test_the_seed_writes_what_the_pipeline_produces_and_nothing_else(
    private_db: sessionmaker[Session], bundle: ModelBundle, mixed_flows
) -> None:
    flows, labels = mixed_flows
    now = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)

    summary = seed.seed_database(
        bundle=bundle, flows=flows, labels=labels, dataset="demo", hours=24, now=now
    )

    with private_db() as session:
        alerts = session.scalars(select(Alert)).all()
        samples = session.scalar(select(func.count()).select_from(FlowSample))
        verdicts = session.scalar(select(func.count()).select_from(AnalystVerdict))

    assert alerts, "a mix of every label should alert"
    assert {alert.kind for alert in alerts} == {"KNOWN", "UNCLASSIFIED_ANOMALY"}
    assert summary.queue_rows == len(alerts)
    assert sum(alert.occurrence_count for alert in alerts) == summary.alerting_flows
    for alert in alerts:
        # Every alert explained, by the pipeline, and tagged with the seed's clock.
        assert alert.explanation and alert.narrative
        assert alert.raw_flow["_provenance"]["detected_at"] == "seed_clock"
        assert alert.model_version == bundle.version
        # The bucket's ground truth covers every flow it absorbed.
        assert sum(alert.ground_truth_counts.values()) == alert.occurrence_count
        assert alert.ground_truth_label in alert.ground_truth_counts
        stamped = alert.detected_at.replace(tzinfo=dt.UTC)
        assert now - dt.timedelta(hours=24) < stamped <= now
    assert samples == math.ceil(len(flows) / seed.SEED_SAMPLE_STRIDE)
    # Nothing an analyst says is simulated.
    assert verdicts == 0


def test_the_seed_refuses_to_mix_into_existing_data(
    private_db: sessionmaker[Session], bundle: ModelBundle, mixed_flows, capsys
) -> None:
    flows, labels = mixed_flows
    seed.seed_database(bundle=bundle, flows=flows[:500], labels=labels[:500], dataset="demo")
    with private_db() as session:
        before = session.scalar(select(func.count()).select_from(Alert))

    assert seed.main(["--rows", "100"]) == 1
    assert "already holds" in capsys.readouterr().err

    assert seed.main(["--if-empty"]) == 0
    assert "seed skipped" in capsys.readouterr().out

    with private_db() as session:
        assert session.scalar(select(func.count()).select_from(Alert)) == before


def test_reset_starts_over_and_keeps_the_audit_trail(
    private_db: sessionmaker[Session], bundle: ModelBundle, mixed_flows, capsys
) -> None:
    flows, labels = mixed_flows
    seed.seed_database(bundle=bundle, flows=flows[:500], labels=labels[:500], dataset="demo")

    assert seed.main(["--reset", "--rows", "1000"]) == 0
    output = capsys.readouterr().out
    assert "reset: deleted" in output
    assert "seeded 1,000 real flows from 'demo'" in output

    with private_db() as session:
        alerts = session.scalars(select(Alert)).all()
    assert alerts
    assert {alert.raw_flow["_provenance"]["detected_at"] for alert in alerts} == {"seed_clock"}
