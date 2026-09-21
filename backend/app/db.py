"""Database engine, session factory and the declarative base.

SQLite is the development default; the models deliberately avoid every
SQLite-only construct so that pointing `IDS_DATABASE_URL` at Postgres is a
configuration change and nothing more.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# Deterministic constraint names. Without these, Alembic autogenerate emits
# unnamed constraints that SQLite cannot later ALTER and Postgres names
# differently, which makes migrations diverge per backend.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base carrying the shared naming convention."""

    metadata = metadata


def _build_engine() -> Engine:
    url = settings.sqlalchemy_url
    is_sqlite = url.startswith("sqlite")

    engine = create_engine(
        url,
        echo=settings.db_echo,
        future=True,
        pool_pre_ping=True,
        # A single SQLite file is touched by request handlers and by the
        # replay background task, which live on different threads.
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            # SQLite ignores foreign keys unless asked; Postgres always
            # enforces them. Turn them on so behaviour matches.
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL keeps the replay writer from blocking dashboard readers.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


engine: Engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and background tasks."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
