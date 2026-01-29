"""
Database utilities for the music player backend.

Uses SQLAlchemy 2.0 style engine/sessions, configured by environment variables.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _build_database_url() -> str:
    """
    Build a SQLAlchemy database URL from environment variables.

    Supported env var strategies:
      1) DATABASE_URL (recommended): full SQLAlchemy/psycopg2 URL, e.g.
         postgresql+psycopg2://user:pass@host:port/db
      2) POSTGRES_* variables (as provided by the database container):
         POSTGRES_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT

    Returns:
        A SQLAlchemy database URL string.

    Raises:
        RuntimeError: if configuration is missing.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    # Fallback to the database container env var set.
    host = os.getenv("POSTGRES_URL")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    db = os.getenv("POSTGRES_DB")
    port = os.getenv("POSTGRES_PORT")

    if all([host, user, password, db, port]):
        # Ensure URL doesn't include protocol.
        host = host.replace("postgresql://", "").replace("postgres://", "")
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"

    raise RuntimeError(
        "Database configuration missing. Set DATABASE_URL or "
        "POSTGRES_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT."
    )


_ENGINE: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


# PUBLIC_INTERFACE
def get_engine() -> Engine:
    """Return (and lazily create) the SQLAlchemy Engine."""
    global _ENGINE, _SessionLocal
    if _ENGINE is None:
        url = _build_database_url()
        _ENGINE = create_engine(url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)
    return _ENGINE


# PUBLIC_INTERFACE
@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Yield a SQLAlchemy Session, handling commit/rollback.

    Usage:
        with get_db_session() as db:
            ...

    Yields:
        Session: an active SQLAlchemy session.
    """
    get_engine()
    assert _SessionLocal is not None  # created by get_engine()
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
