"""
schemas/task.py
================

Why this file exists
---------------------
Pydantic schemas are the validated boundary objects used everywhere a Task
crosses a layer: tool input/output, API request/response, repository return
values. They intentionally do NOT import SQLAlchemy — this keeps agent and
tool code free of ORM dependencies (Clean Architecture: inner layers don't
depend on infrastructure).

How it interacts with the rest of the system
-----------------------------------------------
- `database/repository.py` converts `Task` ORM rows to `TaskRead` before
  returning them to callers.
- `tools/task_tools.py` uses `TaskCreate` / `TaskUpdate` as tool input
  schemas and `TaskRead` / `TaskListResult` as output schemas.
- Reuses `PriorityEnum` / `StatusEnum` from `database/models.py` as the
  single source of truth for allowed values — no risk of the API and DB
  drifting out of sync on what a valid priority/status is.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.database.models import PriorityEnum, StatusEnum


class TaskCreate(BaseModel):
    """Input schema for the `create_task` tool / `POST /api/tasks`."""

    title: str = Field(..., min_length=1, max_length=255, description="Short task title.")
    description: str = Field(default="", max_length=5000)
    priority: PriorityEnum = Field(default=PriorityEnum.MEDIUM)
    due_date: Optional[datetime] = Field(default=None)
    tags: List[str] = Field(default_factory=list)
    source: str = Field(default="user", max_length=100)
    notes: str = Field(default="", max_length=5000)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank or whitespace-only")
        return v.strip()


class TaskUpdate(BaseModel):
    """
    Input schema for the `update_task` tool. All fields optional — only
    supplied fields are changed (partial update semantics).
    """

    task_id: str = Field(..., description="ID of the task to update.")
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    priority: Optional[PriorityEnum] = Field(default=None)
    status: Optional[StatusEnum] = Field(default=None)
    due_date: Optional[datetime] = Field(default=None)
    tags: Optional[List[str]] = Field(default=None)
    notes: Optional[str] = Field(default=None, max_length=5000)


class TaskComplete(BaseModel):
    """Input schema for the `complete_task` tool."""

    task_id: str = Field(..., description="ID of the task to mark completed.")


class TaskRead(BaseModel):
    """Output schema representing a Task as returned to callers."""

    id: str
    title: str
    description: str
    priority: PriorityEnum
    status: StatusEnum
    due_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    tags: List[str] = Field(default_factory=list)
    source: str
    notes: str

    model_config = {"from_attributes": True}


class TaskListFilter(BaseModel):
    """Input schema for the `list_tasks` tool — all filters optional."""

    status: Optional[StatusEnum] = None
    priority: Optional[PriorityEnum] = None
    tag: Optional[str] = None
    overdue_only: bool = False
    limit: int = Field(default=50, ge=1, le=500)


class TaskListResult(BaseModel):
    """Output schema for `list_tasks` — the list plus a count for convenience."""

    tasks: List[TaskRead]
    count: int
