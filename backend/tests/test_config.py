"""Configuration behaviour that later phases depend on."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.config import REPO_ROOT, Settings, settings


def test_false_positive_budget_arithmetic() -> None:
    """The Phase 2 threshold is derived from this, not from 0.5."""
    local = Settings(
        expected_daily_flow_volume=1_000_000,
        analyst_capacity_per_hour=40,
        analyst_shift_hours=8,
    )

    assert local.max_alerts_per_day == 320
    assert local.target_fpr == pytest.approx(3.2e-4)


def test_budget_inputs_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(expected_daily_flow_volume=0)


def test_auto_block_cannot_be_enabled() -> None:
    """No configuration turns this system into an inline blocker."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(allow_auto_block=True)

    assert "never drops traffic" in str(excinfo.value)


def test_relative_paths_anchor_to_the_repo_root_not_the_cwd() -> None:
    """Otherwise the database silently differs depending on where uvicorn ran."""
    assert settings.data_path == (REPO_ROOT / "data").resolve()
    assert settings.artifacts_path == (REPO_ROOT / "backend" / "artifacts").resolve()


def test_absolute_paths_are_respected(tmp_path) -> None:
    local = Settings(data_dir=tmp_path)

    assert local.data_path == tmp_path


def test_sqlite_url_is_rewritten_to_an_absolute_path() -> None:
    local = Settings(database_url="sqlite+pysqlite:///data/ids.db")

    database = make_url(local.sqlalchemy_url).database
    assert database is not None
    from pathlib import Path

    assert Path(database).is_absolute()


def test_postgres_url_passes_through_untouched() -> None:
    """Swapping backends is meant to be this line and nothing else."""
    url = "postgresql+psycopg://ids:secret@db:5432/ids"
    local = Settings(database_url=url)

    assert local.sqlalchemy_url == url


def test_cors_origins_parse_from_a_comma_separated_string() -> None:
    local = Settings(cors_origins="http://a.test, http://b.test ,")

    assert local.cors_origin_list == ["http://a.test", "http://b.test"]
