"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

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
