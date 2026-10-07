"""Application settings.

Every tunable — path, port, threshold budget — arrives through here from the
environment. Nothing downstream hardcodes a location or a port, so the same
image runs locally, in docker compose, and against Postgres without a code
change (see `IDS_DATABASE_URL` in `.env.example`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# backend/app/config.py -> backend/ -> <repo root>
BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
REPO_ROOT: Path = BACKEND_DIR.parent


def _resolve(raw: str | Path) -> Path:
    """Resolve a configured path, treating relative values as repo-root-relative.

    Keeps `IDS_DATA_DIR=data` meaningful no matter which directory the process
    was started from, while still honouring absolute paths (which is what the
    container passes in).
    """
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IDS_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),  # we legitimately expose `model_*` names
    )

    # ---- Runtime -------------------------------------------------------
    app_name: str = "Recluse"
    env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000
    api_v1_prefix: str = "/api/v1"

    # ---- Persistence ---------------------------------------------------
    database_url: str = "sqlite+pysqlite:///data/ids.db"
    db_echo: bool = False

    # ---- Paths ---------------------------------------------------------
    data_dir: Path = Path("data")
    artifacts_dir: Path = Path("backend/artifacts")
    reports_dir: Path = Path("reports")
    # The committed model release (Phase 8): one trained pair plus the demo
    # flows, installed into `artifacts_dir` when nothing is serving there.
    release_dir: Path = Path("backend/release")

    # ---- CORS ----------------------------------------------------------
    # Comma-separated, not JSON: pydantic-settings would otherwise demand a
    # JSON array in the env file for a list-typed field.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- False-positive budget (Phase 2 threshold selection) -----------
    expected_daily_flow_volume: int = Field(default=1_000_000, gt=0)
    analyst_capacity_per_hour: int = Field(default=40, gt=0)
    analyst_shift_hours: int = Field(default=8, gt=0)

    # ---- Alert pipeline ------------------------------------------------
    dedupe_window_seconds: int = Field(default=300, gt=0)

    # ---- Drift monitoring (Phase 7) ------------------------------------
    # Drift has to be measured over *scored traffic*, not over stored alerts.
    # Alerts are the rows that crossed a threshold, so a PSI computed from them
    # answers "do the alerts look unusual" rather than "has the traffic moved" --
    # and the second question is the one a drift monitor exists to ask. The
    # pipeline therefore keeps a systematic sample of every flow it scores,
    # alerting or not.
    #
    # Systematic (every Nth row) rather than random, so a replay produces the
    # same sample twice and a drift number can be reproduced.
    drift_sample_stride: int = Field(default=500, gt=0)
    # Rows retained. At the default stride a million flows a day contributes
    # 2,000 rows, so this holds about three weeks before the nightly job prunes.
    drift_sample_keep: int = Field(default=50_000, gt=0)
    drift_bins: int = Field(default=10, ge=2, le=50)
    # How far back a nightly run looks for its observation.
    drift_window_hours: int = Field(default=24, gt=0)
    # The smallest observation worth scoring. PSI over a handful of rows is
    # noise, and a drift banner raised by noise is worse than no banner.
    drift_min_rows: int = Field(default=200, gt=0)

    # ---- Active learning (Phase 7) -------------------------------------
    # The poisoning guards on the autoencoder's benign refit pool. An attacker
    # who can generate enough traffic, and get it dismissed as a false positive,
    # can otherwise teach the baseline that their traffic is normal.
    #
    # No row enters the pool without an analyst's FP confirmation, and no single
    # source host may contribute more than this fraction of it.
    benign_refit_host_cap: float = Field(default=0.2, gt=0.0, le=1.0)
    # Below this the pool is refused outright rather than refit on thin
    # evidence: a baseline moved by twenty rows is a baseline moved by whoever
    # supplied the twenty rows.
    benign_refit_min_rows: int = Field(default=200, gt=0)

    # ---- Containment ---------------------------------------------------
    # Present so the constraint is explicit and greppable rather than merely
    # absent. There is no serving code path that drops traffic, and setting
    # this to true is rejected at startup rather than silently ignored.
    allow_auto_block: bool = False

    @field_validator("allow_auto_block")
    @classmethod
    def _reject_auto_block(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "IDS_ALLOW_AUTO_BLOCK cannot be enabled. The system alerts, "
                "ranks and explains; it never drops traffic. Containment "
                "actions are manual and confirmed by an analyst."
            )
        return value

    # ---- Derived -------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_path(self) -> Path:
        return _resolve(self.data_dir)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def artifacts_path(self) -> Path:
        return _resolve(self.artifacts_dir)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reports_path(self) -> Path:
        return _resolve(self.reports_dir)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def release_path(self) -> Path:
        return _resolve(self.release_dir)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_alerts_per_day(self) -> int:
        """Analyst capacity per day — the numerator of the FP budget."""
        return self.analyst_capacity_per_hour * self.analyst_shift_hours

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_fpr(self) -> float:
        """The false-positive rate the supervised threshold must respect.

        Phase 2 picks `tau_sup` as the smallest threshold whose measured FPR
        stays under this number, instead of defaulting to 0.5.
        """
        return self.max_alerts_per_day / self.expected_daily_flow_volume

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_url(self) -> str:
        """`database_url` with relative SQLite paths anchored to the repo root.

        A bare `sqlite:///data/ids.db` is resolved against the working
        directory, which silently produces a different database depending on
        where uvicorn was launched. Anchor it once, here.
        """
        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
            db_path = Path(url.database)
            if not db_path.is_absolute():
                db_path = _resolve(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = url.set(database=str(db_path))
        return url.render_as_string(hide_password=False)

    def ensure_directories(self) -> None:
        """Create the directories the app writes to. Idempotent."""
        for path in (self.data_path, self.artifacts_path, self.reports_path):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton (cached so the env is read once)."""
    return Settings()


settings = get_settings()
