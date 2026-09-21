"""Engine and session factory. SQLite locally; DATABASE_URL can point at PostgreSQL later."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config import Settings
from db.base import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_settings: Settings | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_memory_sqlite(url: str) -> bool:
    normalized = url.replace("sqlite+pysqlite", "sqlite")
    return normalized in {"sqlite://", "sqlite:///:memory:"} or ":memory:" in normalized


def create_engine_from_url(url: str, **kwargs: Any) -> Engine:
    connect_args: dict[str, Any] = {}
    engine_kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": not _is_sqlite(url)}
    if _is_sqlite(url):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 0.1
        if _is_memory_sqlite(url):
            engine_kwargs["poolclass"] = StaticPool
        else:
            sqlite_path = url.removeprefix("sqlite:///").split("?", 1)[0]
            if sqlite_path and sqlite_path != ":memory:":
                Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    engine_kwargs["connect_args"] = connect_args
    engine_kwargs.update(kwargs)
    engine = create_engine(url, **engine_kwargs)

    if _is_sqlite(url):

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # Fail fast instead of blocking a request session for 30s.
            cursor.execute("PRAGMA busy_timeout=100")
            cursor.close()

    return engine


def create_engine_from_settings(settings: Settings) -> Engine:
    return create_engine_from_url(settings.resolved_database_url)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    import db.models  # noqa: F401  — register metadata

    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.commit()


def bind_runtime(engine: Engine, factory: sessionmaker[Session], settings: Settings | None = None) -> None:
    global _engine, _session_factory, _settings
    _engine = engine
    _session_factory = factory
    _settings = settings


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine is not initialized. Call init_db / bind_runtime first.")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("Database session factory is not initialized.")
    return _session_factory


def bootstrap_database(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine_from_settings(settings)
    init_db(engine)
    factory = session_factory(engine)
    bind_runtime(engine, factory, settings)
    return engine, factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session. Do not run queries in route handlers — use repositories."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def session_scope(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    maker = factory or get_session_factory()
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
