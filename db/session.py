"""Database engine + session management.

Targets Supabase/PostgreSQL in production via the DATABASE_URL env var, and
falls back to a local SQLite file for dev/tests so the project runs out of the
box. The schema is identical on both (no backend-specific column types).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base

_engine = None
_SessionLocal = None


def resolve_url(db_path: Optional[str] = None) -> str:
    """Return the SQLAlchemy URL.

    Priority: explicit DATABASE_URL (Supabase/Postgres) > provided sqlite path.
    Normalises the common `postgres://` prefix to `postgresql+psycopg://`.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):  # no driver specified -> use psycopg
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url
    if db_path is None:
        db_path = "data/scholarreach.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def is_postgres() -> bool:
    return resolve_url().startswith("postgresql")


def init_engine(db_path: Optional[str] = None) -> None:
    """Initialise the engine + create tables. Idempotent."""
    global _engine, _SessionLocal
    url = resolve_url(db_path)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, connect_args=connect_args, future=True,
                            pool_pre_ping=not url.startswith("sqlite"))
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)


def get_engine():
    if _engine is None:
        raise RuntimeError("Engine not initialised. Call init_engine() first.")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope around a series of operations."""
    if _SessionLocal is None:
        raise RuntimeError("Sessions not initialised. Call init_engine() first.")
    sess = _SessionLocal()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def new_session() -> Session:
    """Caller-managed session."""
    if _SessionLocal is None:
        raise RuntimeError("Sessions not initialised. Call init_engine() first.")
    return _SessionLocal()
