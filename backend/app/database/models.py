"""
models.py
=========

Why this file exists
---------------------
Defines the ORM schema for the entire application: `Task`, `Note`, and
`ExecutionLog`. These are the only three tables the system needs — tasks
and notes are the user's actual data, and ExecutionLog is the agent's
audit trail (required by the LOGGING spec: run id, prompt, tools used,
approvals, errors, timing).

How it interacts with the rest of the system
-----------------------------------------------
- Inherits `Base` from `database/connection.py` so `init_db()` can create
  every table with one `Base.metadata.create_all()` call.
- `database/repository.py` is the ONLY module that queries these models
  directly. Tools, agent nodes, and API routes never import `models.py` —
  they go through the repository, which returns Pydantic schemas instead
  of raw ORM objects (keeps ORM concerns out of the agent/tool layer).
- `schemas/task.py` and `schemas/note.py` mirror these fields for
  validation at the API/tool boundary; the enums defined here are reused
  by those Pydantic schemas so there is a single source of truth for
  allowed values.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base


def _new_uuid() -> str:
    """Generate a URL-safe unique ID string used as primary key for all tables."""
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    """Single source of truth for 'now' — always UTC, never naive-local time."""
    return datetime.now(timezone.utc)


class PriorityEnum(str, enum.Enum):
    """Task priority levels, ordered low to critical."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StatusEnum(str, enum.Enum):
    """Task lifecycle states."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Task(Base):
    """
    A single actionable task tracked by the agent.

    `tags` is stored as a comma-separated string rather than a separate
    join table — for this project's scale (single-user productivity agent)
    a normalized tags table adds complexity without real benefit; the
    repository layer handles list<->string conversion so callers never see
    the raw string representation.

    `source` records where the task originated (e.g. "user", "meeting_notes",
    "agent_generated") — useful for the Weekly Report tool and for audit.
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True, default="")
    priority: Mapped[PriorityEnum] = mapped_column(
        Enum(PriorityEnum), nullable=False, default=PriorityEnum.MEDIUM
    )
    status: Mapped[StatusEnum] = mapped_column(
        Enum(StatusEnum), nullable=False, default=StatusEnum.PENDING
    )
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    tags: Mapped[str] = mapped_column(String(500), nullable=True, default="")
    source: Mapped[str] = mapped_column(String(100), nullable=True, default="user")
    notes: Mapped[str] = mapped_column(Text, nullable=True, default="")

    def __repr__(self) -> str:
        return f"<Task id={self.id!r} title={self.title!r} status={self.status!r}>"


class Note(Base):
    """
    A free-form note (meeting notes, reference material, ideas) that the
    agent can search via `search_notes` and convert into tasks via
    `extract_meeting_actions` / `convert_meeting_notes_to_tasks`.
    """

    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=True, default="general")
    tags: Mapped[str] = mapped_column(String(500), nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Note id={self.id!r} title={self.title!r}>"


class ExecutionLog(Base):
    """
    One row per agent run — the audit trail required by the LOGGING spec.

    Structured JSON-serializable fields (`tools_used`, `arguments`, `results`)
    are stored as Text containing JSON, rather than normalized child tables,
    because a run's tool sequence is written once and read as a whole (never
    queried by individual tool call) — JSON blobs keep the schema simple
    without sacrificing queryability of the run-level fields that DO need
    filtering (status, timestamps).

    CRITICAL: `prompt`, `arguments`, and `results` must never contain API
    keys or other secrets — enforced by `logging/run_logger.py` before a row
    is written here, not by this model.
    """

    __tablename__ = "execution_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    selected_model: Mapped[str] = mapped_column(String(100), nullable=False)
    tools_used: Mapped[str] = mapped_column(Text, nullable=True, default="[]")  # JSON list
    arguments: Mapped[str] = mapped_column(Text, nullable=True, default="{}")  # JSON dict
    results: Mapped[str] = mapped_column(Text, nullable=True, default="{}")  # JSON dict
    approval_status: Mapped[str] = mapped_column(String(50), nullable=True, default="not_required")
    errors: Mapped[str] = mapped_column(Text, nullable=True, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(nullable=True)
    final_outcome: Mapped[str] = mapped_column(String(50), nullable=True, default="pending")

    def __repr__(self) -> str:
        return f"<ExecutionLog run_id={self.run_id!r} outcome={self.final_outcome!r}>"
