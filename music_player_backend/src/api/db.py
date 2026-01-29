"""
Database utilities for the music player backend.

Uses SQLAlchemy 2.0 style engine/sessions, configured by environment variables.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator, Optional
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def _redact_sqlalchemy_url(url: str) -> str:
    """
    Redact password from a SQLAlchemy URL for safe logging.

    Example:
        postgresql+psycopg2://user:pass@host:5432/db -> postgresql+psycopg2://user:***@host:5432/db
    """
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return url
        if "@" not in parsed.netloc:
            return url

        userinfo, hostinfo = parsed.netloc.rsplit("@", 1)
        if ":" in userinfo:
            user = userinfo.split(":", 1)[0]
            new_netloc = f"{user}:***@{hostinfo}"
        else:
            new_netloc = parsed.netloc

        return urlunparse(parsed._replace(netloc=new_netloc))
    except Exception:
        # Never fail URL construction due to logging concerns.
        return "<redacted>"


def _normalize_sqlalchemy_database_url(database_url: str) -> str:
    """
    Normalize URL for SQLAlchemy.

    - SQLAlchemy expects 'postgresql://' not 'postgres://'
    - We keep any explicit driver (e.g. postgresql+psycopg2://) as-is.
    """
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://") :]
    return database_url


def _build_database_url() -> str:
    """
    Build a SQLAlchemy database URL from environment variables.

    Supported env var strategies:
      1) DATABASE_URL (recommended): full SQLAlchemy/psycopg2 URL, e.g.
         postgresql+psycopg2://user:pass@host:port/db
      2) POSTGRES_* variables (as provided by the database container):
         POSTGRES_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT

    Important:
      - POSTGRES_URL may be either a full URL (postgresql://host:port/db) OR just a host.
        The previous implementation incorrectly treated a full URL as a host and produced
        invalid connection strings.

    Returns:
        A SQLAlchemy database URL string.

    Raises:
        RuntimeError: if configuration is missing.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return _normalize_sqlalchemy_database_url(database_url)

    postgres_url = os.getenv("POSTGRES_URL")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    db = os.getenv("POSTGRES_DB")
    port = os.getenv("POSTGRES_PORT")

    if not postgres_url:
        raise RuntimeError(
            "Database configuration missing. Set DATABASE_URL or "
            "POSTGRES_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT."
        )

    # If POSTGRES_URL looks like a URL, parse and merge with provided overrides.
    if postgres_url.startswith("postgresql://") or postgres_url.startswith("postgres://"):
        parsed = urlparse(_normalize_sqlalchemy_database_url(postgres_url))

        # Determine host/port/db from URL, then override with explicit env vars if provided.
        host = parsed.hostname or "localhost"
        final_port = int(port) if (port and port.isdigit()) else (parsed.port or 5432)

        path_db = parsed.path.lstrip("/") if parsed.path else ""
        final_db = (db or path_db).strip()

        final_user = (user or parsed.username or "").strip()
        final_password = (password or parsed.password or "").strip()

        if not (final_user and final_password and final_db):
            raise RuntimeError(
                "Database configuration incomplete. POSTGRES_URL must include db name "
                "or provide POSTGRES_DB, and provide POSTGRES_USER/POSTGRES_PASSWORD."
            )

        return f"postgresql+psycopg2://{final_user}:{final_password}@{host}:{final_port}/{final_db}"

    # Otherwise treat POSTGRES_URL as a host (possibly host:port).
    host = postgres_url.strip()
    host_only = host
    port_from_host: Optional[int] = None
    if ":" in host and host.rsplit(":", 1)[-1].isdigit():
        host_only, port_str = host.rsplit(":", 1)
        port_from_host = int(port_str)

    final_port = int(port) if (port and port.isdigit()) else (port_from_host or 5432)

    if not (user and password and db):
        raise RuntimeError(
            "Database configuration incomplete. When POSTGRES_URL is a host, you must provide "
            "POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB (and optionally POSTGRES_PORT)."
        )

    return f"postgresql+psycopg2://{user}:{password}@{host_only}:{final_port}/{db}"


_ENGINE: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


# PUBLIC_INTERFACE
def get_engine() -> Engine:
    """Return (and lazily create) the SQLAlchemy Engine."""
    global _ENGINE, _SessionLocal
    if _ENGINE is None:
        url = _build_database_url()

        # Startup log (safe): helps diagnose host/port issues in preview.
        logger.info("DB: using database url=%s", _redact_sqlalchemy_url(url))

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


# PUBLIC_INTERFACE
def db_session_dep() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a DB session and returns clear JSON errors.

    This is preferred over calling `get_db_session()` directly in route bodies because it:
    - converts missing DB configuration into HTTP 503 with actionable details
    - converts DB connection/query issues into HTTP 503 (instead of generic 500)
    """
    try:
        with get_db_session() as db:
            yield db
    except RuntimeError as exc:
        # Typically thrown by _build_database_url() for missing/invalid env configuration.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_misconfigured",
                "message": str(exc),
                "hint": (
                    "Set DATABASE_URL or provide POSTGRES_URL, POSTGRES_USER, POSTGRES_PASSWORD, "
                    "POSTGRES_DB (and optionally POSTGRES_PORT)."
                ),
            },
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_unavailable",
                "message": "Database connection/query failed.",
                "exception": exc.__class__.__name__,
                "hint": (
                    "Ensure the database container is running and the backend can reach it "
                    "using the "
                    "configured env vars."
                ),
            },
        )
