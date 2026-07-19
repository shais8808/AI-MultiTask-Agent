"""
repository.py
==============

Why this file exists
---------------------
Implements the Repository Pattern: every piece of code that needs to read
or write Tasks, Notes, or ExecutionLogs goes through `TaskRepository`,
`NoteRepository`, or `LogRepository` — never through raw SQLAlchemy
queries scattered across tools/services/routes.

Benefits this buys us:
- Tools (`tools/task_tools.py`) can be unit-tested by mocking the
  repository, without needing a real database.
- ORM objects never leak past this layer — every method returns a
  validated Pydantic schema (`TaskRead`, `NoteRead`), so callers never
  accidentally mutate a detached SQLAlchemy object.
- If the DB engine changes (SQLite -> Postgres), only `connection.py`
  changes; this file's SQL-adjacent logic (filtering, ordering) is
  portable across both.

How it interacts with the rest of the system
-----------------------------------------------
- Takes a `Session` (from `database/connection.py`'s `get_db` or
  `get_db_session`) via constructor injection — this is the Dependency
  Injection point referenced in the CODING STANDARDS.
- Raises domain-specific exceptions (`TaskNotFoundError`, `NoteNotFoundError`)
  that `tools/*.py` catch and translate into `ToolResult(success=False, ...)`.
"""

import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import ExecutionLog, Note, PriorityEnum, StatusEnum, Task
from app.schemas.note import NoteCreate, NoteRead
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate

logger = logging.getLogger(__name__)


class RepositoryError(Exception):
    """Base class for all repository-layer errors."""


class TaskNotFoundError(RepositoryError):
    """Raised when a task_id does not exist in the database."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"Task with id={task_id!r} was not found.")


class NoteNotFoundError(RepositoryError):
    """Raised when a note_id does not exist in the database."""

    def __init__(self, note_id: str):
        self.note_id = note_id
        super().__init__(f"Note with id={note_id!r} was not found.")


_SEARCH_STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "and", "or", "to", "my", "me", "about"}


def _tags_to_str(tags: List[str]) -> str:
    """Serialize a list of tags into the comma-separated storage format."""
    return ",".join(t.strip() for t in tags if t.strip())


def _tags_to_list(tags_str: Optional[str]) -> List[str]:
    """Deserialize the stored comma-separated tag string back into a list."""
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


class TaskRepository:
    """All database access for the `Task` entity."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: TaskCreate) -> TaskRead:
        """Insert a new task and return it as a validated `TaskRead`."""
        task = Task(
            title=data.title,
            description=data.description,
            priority=data.priority,
            due_date=data.due_date,
            tags=_tags_to_str(data.tags),
            source=data.source,
            notes=data.notes,
        )
        self.db.add(task)
        self.db.flush()
        self.db.refresh(task)
        logger.info("Created task id=%s title=%r", task.id, task.title)
        return self._to_schema(task)

    def get(self, task_id: str) -> TaskRead:
        """Fetch a single task by ID. Raises `TaskNotFoundError` if absent."""
        task = self.db.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return self._to_schema(task)

    def list(
        self,
        status: Optional[StatusEnum] = None,
        priority: Optional[PriorityEnum] = None,
        tag: Optional[str] = None,
        overdue_only: bool = False,
        limit: int = 50,
    ) -> List[TaskRead]:
        """List tasks with optional filters — powers the `list_tasks` tool."""
        stmt = select(Task)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if priority is not None:
            stmt = stmt.where(Task.priority == priority)
        if overdue_only:
            now = datetime.now(timezone.utc)
            stmt = stmt.where(Task.due_date.isnot(None), Task.due_date < now).where(
                Task.status.notin_([StatusEnum.COMPLETED, StatusEnum.CANCELLED])
            )
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit)

        results = self.db.execute(stmt).scalars().all()

        # Tag filtering done in Python since tags are a comma-separated
        # string, not a normalized/queryable column.
        if tag:
            results = [t for t in results if tag.lower() in _tags_to_list(t.tags)]

        return [self._to_schema(t) for t in results]

    def update(self, data: TaskUpdate) -> TaskRead:
        """Partially update a task. Raises `TaskNotFoundError` if absent."""
        task = self.db.get(Task, data.task_id)
        if task is None:
            raise TaskNotFoundError(data.task_id)

        if data.title is not None:
            task.title = data.title
        if data.description is not None:
            task.description = data.description
        if data.priority is not None:
            task.priority = data.priority
        if data.status is not None:
            task.status = data.status
        if data.due_date is not None:
            task.due_date = data.due_date
        if data.tags is not None:
            task.tags = _tags_to_str(data.tags)
        if data.notes is not None:
            task.notes = data.notes

        self.db.flush()
        self.db.refresh(task)
        logger.info("Updated task id=%s", task.id)
        return self._to_schema(task)

    def complete(self, task_id: str) -> TaskRead:
        """Mark a task as completed. Raises `TaskNotFoundError` if absent."""
        task = self.db.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        task.status = StatusEnum.COMPLETED
        self.db.flush()
        self.db.refresh(task)
        logger.info("Completed task id=%s", task.id)
        return self._to_schema(task)

    def delete(self, task_id: str) -> None:
        """Delete a task permanently. Raises `TaskNotFoundError` if absent."""
        task = self.db.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        self.db.delete(task)
        self.db.flush()
        logger.info("Deleted task id=%s", task_id)

    def detect_overdue(self) -> List[TaskRead]:
        """Return all non-terminal tasks whose due_date has passed."""
        return self.list(overdue_only=True, limit=500)

    @staticmethod
    def _to_schema(task: Task) -> TaskRead:
        return TaskRead(
            id=task.id,
            title=task.title,
            description=task.description or "",
            priority=task.priority,
            status=task.status,
            due_date=task.due_date,
            created_at=task.created_at,
            updated_at=task.updated_at,
            tags=_tags_to_list(task.tags),
            source=task.source or "user",
            notes=task.notes or "",
        )


class NoteRepository:
    """All database access for the `Note` entity."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, data: NoteCreate) -> NoteRead:
        """Insert a new note and return it as a validated `NoteRead`."""
        note = Note(
            title=data.title,
            content=data.content,
            category=data.category,
            tags=_tags_to_str(data.tags),
        )
        self.db.add(note)
        self.db.flush()
        self.db.refresh(note)
        logger.info("Created note id=%s title=%r", note.id, note.title)
        return self._to_schema(note)

    def get(self, note_id: str) -> NoteRead:
        """Fetch a single note by ID. Raises `NoteNotFoundError` if absent."""
        note = self.db.get(Note, note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return self._to_schema(note)

    def search(
        self, query: str, category: Optional[str] = None, limit: int = 20
    ) -> List[NoteRead]:
        """
        Case-insensitive keyword search across title, content, and tags —
        powers the `search_notes` tool. Splits the query into individual
        words (dropping common stopwords) and matches a note if ANY
        meaningful word appears in its title, content, or tags — a query
        like "travel and meetings" matches on "travel" or "meetings"
        rather than requiring that exact phrase. This is intentionally
        simple (no full-text index) since note volume for a single-user
        productivity agent is small; a FTS5 virtual table would be the
        upgrade path if volume grows.
        """
        words = [w for w in query.lower().split() if w]
        terms = [w for w in words if w not in _SEARCH_STOPWORDS] or words

        stmt = select(Note)
        if category:
            stmt = stmt.where(Note.category == category)
        stmt = stmt.order_by(Note.updated_at.desc()).limit(limit * 3)  # over-fetch, filter below

        candidates = self.db.execute(stmt).scalars().all()
        matched = []
        for n in candidates:
            haystack = f"{n.title} {n.content} {n.tags or ''}".lower()
            if any(term in haystack for term in terms):
                matched.append(n)
        return [self._to_schema(n) for n in matched[:limit]]

    def list_all(self, limit: int = 100) -> List[NoteRead]:
        """List all notes, most recently updated first."""
        stmt = select(Note).order_by(Note.updated_at.desc()).limit(limit)
        results = self.db.execute(stmt).scalars().all()
        return [self._to_schema(n) for n in results]

    @staticmethod
    def _to_schema(note: Note) -> NoteRead:
        return NoteRead(
            id=note.id,
            title=note.title,
            content=note.content,
            category=note.category or "general",
            tags=_tags_to_list(note.tags),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )


class LogRepository:
    """
    All database access for the `ExecutionLog` entity.

    Used exclusively by `logging/run_logger.py` — no other module should
    write execution logs directly, ensuring every run is logged through
    the same validated path (which strips secrets before persisting).
    """

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        run_id: str,
        prompt: str,
        selected_model: str,
        started_at: datetime,
    ) -> ExecutionLog:
        """Create the initial log row when a run begins."""
        log = ExecutionLog(
            run_id=run_id,
            prompt=prompt,
            selected_model=selected_model,
            started_at=started_at,
            final_outcome="in_progress",
        )
        self.db.add(log)
        self.db.flush()
        self.db.refresh(log)
        return log

    def finalize(
        self,
        run_id: str,
        tools_used: List[str],
        arguments: dict,
        results: dict,
        approval_status: str,
        errors: str,
        ended_at: datetime,
        duration_ms: int,
        final_outcome: str,
    ) -> None:
        """Update the log row with the final outcome of a completed run."""
        stmt = select(ExecutionLog).where(ExecutionLog.run_id == run_id)
        log = self.db.execute(stmt).scalars().first()
        if log is None:
            logger.warning("finalize() called for unknown run_id=%s", run_id)
            return
        log.tools_used = json.dumps(tools_used)
        log.arguments = json.dumps(arguments, default=str)
        log.results = json.dumps(results, default=str)
        log.approval_status = approval_status
        log.errors = errors
        log.ended_at = ended_at
        log.duration_ms = duration_ms
        log.final_outcome = final_outcome
        self.db.flush()

    def list_recent(self, limit: int = 50) -> List[ExecutionLog]:
        """List the most recent execution logs — powers `/api/logs`."""
        stmt = select(ExecutionLog).order_by(ExecutionLog.started_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())
