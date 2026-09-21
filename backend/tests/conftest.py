"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

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
