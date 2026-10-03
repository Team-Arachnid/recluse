"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import Base
from app.main import create_app


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """App client with the lifespan run.

    Entering the context manager is what exercises startup, including artifact
    loading and the schema-hash check.
    """
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def api_prefix() -> str:
    return settings.api_v1_prefix


@pytest.fixture
def db_session() -> Iterator[Session]:
    """A throwaway in-memory database built from the ORM metadata.

    Deliberately not the dev SQLite file: these tests assert constraint
    behaviour and must not touch real data.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Phase 1 -- synthetic CICIDS2017
#
# The real download is ~500MB and gated behind a licence form, so the data
# tests run against a frame shaped like the published CSVs. Every defect this
# fixture carries is one the real files are documented to contain, and the
# column spellings -- leading spaces, "Flow Bytes/s", "Fwd Header Length.1" --
# are the published ones.
# ---------------------------------------------------------------------------

# Day -> (timestamp prefix, labels present). Mirrors the real week.
CICIDS_DAYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "monday": ("3/7/2017", ("BENIGN",)),
    "tuesday": ("4/7/2017", ("BENIGN", "FTP-Patator", "SSH-Patator")),
    "wednesday": ("5/7/2017", ("BENIGN", "DoS Hulk", "Heartbleed")),
    "thursday": ("6/7/2017", ("BENIGN", "Web Attack - XSS", "Infiltration")),
    "friday": ("7/7/2017", ("BENIGN", "Bot", "PortScan", "DDoS")),
}


def _raw_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for day, (date, labels) in CICIDS_DAYS.items():
        for index, label in enumerate(labels):
            for repeat in range(4):
                minute = index * 4 + repeat
                rows.append(
                    {
                        "Flow ID": f"{day}-{index}-{repeat}",
                        " Source IP": "192.168.10.5",
                        " Source Port": 50000 + minute,
                        " Destination IP": "192.168.10.50",
                        " Destination Port": (80, 443, 21, 22, 8080)[minute % 5],
                        " Timestamp": f"{date} {9 + minute // 60}:{minute % 60:02d}",
                        " Flow Duration": 1000 + minute,
                        " Total Fwd Packets": 5 + minute,
                        "Flow Bytes/s": 1000.0 + minute,
                        " Flow Packets/s": 10.0 + minute,
                        " Fwd IAT Min": 3 + minute,
                        "Fwd Header Length.1": 32,
                        " Bwd PSH Flags": 0,  # all-zero in the real files
                        " Fwd URG Flags": 0,  # all-zero in the real files
                        " Label": label,
                    }
                )
    return rows


@pytest.fixture
def raw_cicids_frame() -> pd.DataFrame:
    """A raw-looking frame carrying all five documented defects."""
    frame = pd.DataFrame(_raw_rows())

    # Defect 2: zero-duration flows produce Inf and NaN rates.
    frame.loc[0, "Flow Bytes/s"] = np.inf
    frame.loc[1, " Flow Packets/s"] = -np.inf
    frame.loc[2, "Flow Bytes/s"] = np.nan

    # Defect 5: negative duration and IAT values.
    frame.loc[3, " Flow Duration"] = -500
    frame.loc[4, " Fwd IAT Min"] = -7

    # Defect 3: exact duplicate rows, which the real files carry in bulk.
    frame = pd.concat([frame, frame.iloc[[10, 11, 12]]], ignore_index=True)

    # Defect 1: labels with stray whitespace.
    frame.loc[5, " Label"] = "  BENIGN "

    return frame


@pytest.fixture
def clean_cicids_frame(raw_cicids_frame: pd.DataFrame) -> pd.DataFrame:
    """The synthetic frame after cleaning -- the input split.py expects."""
    from training.clean import clean_frame

    cleaned, _ = clean_frame(raw_cicids_frame)
    return cleaned


# ---------------------------------------------------------------------------
# Phase 2 -- splits shaped like the real ones
#
# The real training run takes minutes on a million rows. These frames are small
# and separable, but they reproduce the two structural properties of the real
# split that the Stage 1 code has to survive: a family too rare to train on, and
# a test day whose families are absent from the training vocabulary entirely.
# ---------------------------------------------------------------------------

FLOW_COLUMNS: tuple[str, ...] = (
    "destination_port",
    "flow_duration",
    "total_fwd_packets",
    "flow_bytes_s",
    "flow_packets_s",
    "fwd_iat_min",
)

# label -> (port, centre of each numeric feature). Separated enough that a
# classifier can learn them, overlapping enough that thresholds matter.
FLOW_PROFILES: dict[str, tuple[int, tuple[float, ...]]] = {
    "BENIGN": (443, (2_000.0, 8.0, 1_500.0, 12.0, 40.0)),
    "DoS Hulk": (80, (60_000.0, 220.0, 90_000.0, 600.0, 2.0)),
    "FTP-Patator": (21, (9_000.0, 14.0, 300.0, 30.0, 12.0)),
    "Heartbleed": (443, (120_000.0, 40.0, 5_000.0, 55.0, 8.0)),
    "Web Attack - XSS": (80, (14_000.0, 20.0, 2_400.0, 45.0, 15.0)),
    "Infiltration": (8080, (300_000.0, 900.0, 700.0, 6.0, 90.0)),
    "DDoS": (80, (52_000.0, 260.0, 88_000.0, 640.0, 3.0)),
    "PortScan": (445, (120.0, 2.0, 40.0, 3.0, 1.0)),
}


def _flows(label: str, rows: int, rng: np.random.Generator) -> pd.DataFrame:
    port, centres = FLOW_PROFILES[label]
    data = {"destination_port": np.full(rows, port, dtype="int64")}
    for name, centre in zip(FLOW_COLUMNS[1:], centres, strict=True):
        data[name] = np.abs(rng.normal(centre, max(centre * 0.08, 1.0), rows))
    frame = pd.DataFrame(data)
    frame["label"] = label
    return frame


def _day(composition: dict[str, int], seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.concat(
        [_flows(label, rows, rng) for label, rows in composition.items()], ignore_index=True
    )
    return frame.reset_index(drop=True)


@pytest.fixture
def phase2_train() -> pd.DataFrame:
    """Tuesday + Wednesday: benign, one DoS family, one brute-force family.

    `Heartbleed` is here in the numbers the real capture has it in -- a handful
    of rows -- so the support floor has something to exclude.
    """
    return _day({"BENIGN": 600, "DoS Hulk": 200, "FTP-Patator": 150, "Heartbleed": 5}, seed=11)


@pytest.fixture
def phase2_val() -> pd.DataFrame:
    """Thursday: mostly benign, with families the training days never carried."""
    return _day({"BENIGN": 500, "Web Attack - XSS": 40, "Infiltration": 8}, seed=12)


@pytest.fixture
def phase2_test() -> pd.DataFrame:
    """Friday: two more families Stage 1 has never seen."""
    return _day({"BENIGN": 400, "DDoS": 120, "PortScan": 90}, seed=13)


@pytest.fixture
def phase3_benign() -> pd.DataFrame:
    """Monday plus the benign rows of Tuesday and Wednesday -- Stage 2's fit set.

    Benign and nothing else, because that is the one property the Stage 2
    training set is not allowed to lose. The duplicated block at the end is
    there because the real one carries duplicates too: Phase 1 removes them
    within each capture file and across the supervised splits, but the
    benign-only set is assembled from three days after that pass.
    """
    frame = _day({"BENIGN": 1_200}, seed=14)
    return pd.concat([frame, frame.iloc[:40]], ignore_index=True)


@dataclass(frozen=True)
class BudgetSettings:
    """The subset of `Settings` the training and evaluation paths read.

    A stub rather than the real settings so a test's threshold arithmetic does
    not move when someone edits `.env`.
    """

    expected_daily_flow_volume: int = 100_000
    analyst_capacity_per_hour: int = 40
    analyst_shift_hours: int = 8

    @property
    def max_alerts_per_day(self) -> int:
        return self.analyst_capacity_per_hour * self.analyst_shift_hours

    @property
    def target_fpr(self) -> float:
        return self.max_alerts_per_day / self.expected_daily_flow_volume


@pytest.fixture
def budget() -> BudgetSettings:
    return BudgetSettings()


# ---------------------------------------------------------------------------
# Phase 4 -- both stages at once
#
# Phase 4 is the first phase whose subject is the two stages together, so the
# artifacts it needs are built by running the real Phase 2 and Phase 3 code on
# the small frames above rather than by hand-assembling a bundle. What that
# buys is that a test of the cascade is a test of what the API actually loads.
# ---------------------------------------------------------------------------

# Three epochs is enough for the loop to run and the artifact to be consistent.
# Separation on a thousand synthetic rows is not a claim worth spending minutes
# of every test run on.
STAGE2_FAST = {"max_epochs": 3, "patience": 2, "batch_rows": 128, "baselines": False}
STAGE1_FAST = {"depth_grid": (8,), "sweep_rows": 10_000}


@pytest.fixture
def two_stage_artifacts(tmp_path, phase2_train, phase2_val, phase2_test, phase3_benign, budget):
    """An artifacts directory carrying a promoted Stage 1 and a fitted Stage 2.

    Session-scoped would be faster, but `tmp_path` is per-test and these tests
    write to the directory. Three forest fits and three epochs is a few seconds.
    """
    from training.features import PORT_ENCODING_BUCKETED
    from training.train_autoencoder import train as train_anomaly
    from training.train_supervised import promote
    from training.train_supervised import train as train_supervised

    artifacts = tmp_path / "artifacts"
    run = train_supervised(
        phase2_train,
        phase2_val,
        artifacts_dir=artifacts,
        algorithm="rf",
        port_encoding=PORT_ENCODING_BUCKETED,
        settings=budget,
        **STAGE1_FAST,
    )
    promote(run, artifacts)
    train_anomaly(
        phase3_benign,
        phase2_val,
        phase2_test,
        artifacts_dir=artifacts,
        settings=budget,
        **STAGE2_FAST,
    )
    return artifacts
