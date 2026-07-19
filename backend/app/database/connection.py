"""
connection.py
==============

Why this file exists
---------------------
This module owns the SQLAlchemy `Engine` and session factory — the single
source of truth for how the application talks to the database.

By isolating engine/session creation here (separate from `models.py` and
`repository.py`), we can swap SQLite for Postgres/MySQL in production by
changing only `DATABASE_URL` in `.env` — no other file needs to change.

How it interacts with the rest of the system
-----------------------------------------------
- `database/models.py` (Phase 3) imports `Base` from this module so all ORM
  models share the same declarative base and metadata.
- `database/repository.py` (Phase 3) imports `get_db_session` to obtain a
  session for CRUD operations.
- `main.py` imports `init_db` to create tables at application startup, and
  uses `get_db` as a FastAPI dependency for request-scoped sessions.
"""

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# `check_same_thread=False` is required for SQLite when accessed from
# multiple threads/async request handlers, which FastAPI does by default.
# This is safe here because SQLAlchemy's session-per-request pattern below
# ensures each request gets its own isolated session.
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    echo=False,  # set True temporarily for SQL debugging; never enable in production logs
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
)


class Base(DeclarativeBase):
    """
    Shared declarative base for all ORM models.

    All models in `database/models.py` (Task, Note, ExecutionLog) inherit
    from this class so `init_db()` can discover and create every table via
    a single `Base.metadata.create_all(engine)` call.
    """

    pass


def init_db() -> None:
    """
    Create all database tables that don't already exist.

    Called once at FastAPI startup (see `main.py`'s lifespan handler).
    Safe to call multiple times — `create_all` is a no-op for existing tables.

    Raises
    ------
    sqlalchemy.exc.SQLAlchemyError
        If the database file/connection cannot be created or accessed
        (e.g. permissions issue, disk full). This is intentionally allowed
        to propagate so the app fails fast at startup rather than failing
        later on the first request (see ERROR HANDLING: 'Database failure').
    """
    # Import models here (not at module top) to avoid circular imports:
    # models.py imports Base from this module, so importing models.py at
    # the top of this file would create a circular dependency.
    from app.database import models  # noqa: F401

    logger.info("Initializing database schema at %s", settings.database_url)
    Base.metadata.create_all(bind=engine)
    logger.info("Database schema ready.")


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a request-scoped database session.

    Usage in a route:
        @router.get("/tasks")
        def list_tasks(db: Session = Depends(get_db)):
            ...

    The session is always closed after the request completes, even if an
    exception is raised, preventing connection leaks.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context-manager version of `get_db`, for use OUTSIDE of FastAPI request
    handlers — e.g. inside LangGraph agent nodes or background tasks, where
    the `Depends()` injection mechanism isn't available.

    Usage:
        with get_db_session() as db:
            repo = TaskRepository(db)
            repo.create(...)
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
