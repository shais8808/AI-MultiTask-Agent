"""
routes/tasks.py
=================

Why this file exists
---------------------
Provides direct REST CRUD over tasks for the frontend's Task Panel — this
is separate from the agent's `create_task`/`update_task`/etc. tools.
The distinction matters: these routes are for a human directly managing
their task list through the UI (immediate, no LLM involved), while the
agent tools in `tools/task_tools.py` are for the LLM-driven conversational
flow (subject to approval gating for sensitive actions). Both ultimately
go through the same `TaskRepository`, so data is always consistent
regardless of which path created/modified it.

How it interacts with the rest of the system
-----------------------------------------------
- Uses FastAPI's `Depends(get_db)` — the request-scoped session dependency
  from `database/connection.py` — since these ARE plain HTTP request
  handlers (unlike agent nodes/tools, which use `get_db_session()`).
- Reuses `schemas/task.py` for both request validation and response models.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.repository import TaskNotFoundError, TaskRepository
from app.database.models import PriorityEnum, StatusEnum
from app.schemas.task import TaskCreate, TaskListResult, TaskRead, TaskUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=TaskListResult)
async def list_tasks(
    status: StatusEnum | None = None,
    priority: PriorityEnum | None = None,
    tag: str | None = None,
    overdue_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> TaskListResult:
    """List tasks with optional filters — powers the Task Panel."""
    repo = TaskRepository(db)
    tasks = repo.list(status=status, priority=priority, tag=tag, overdue_only=overdue_only, limit=limit)
    db.commit()
    return TaskListResult(tasks=tasks, count=len(tasks))


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskRead:
    repo = TaskRepository(db)
    try:
        task = repo.get(task_id)
        db.commit()
        return task
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", response_model=TaskRead, status_code=201)
async def create_task(data: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    repo = TaskRepository(db)
    task = repo.create(data)
    db.commit()
    return task


@router.put("/{task_id}", response_model=TaskRead)
async def update_task(task_id: str, data: TaskUpdate, db: Session = Depends(get_db)) -> TaskRead:
    if data.task_id != task_id:
        data = data.model_copy(update={"task_id": task_id})
    repo = TaskRepository(db)
    try:
        task = repo.update(data)
        db.commit()
        return task
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/complete", response_model=TaskRead)
async def complete_task(task_id: str, db: Session = Depends(get_db)) -> TaskRead:
    repo = TaskRepository(db)
    try:
        task = repo.complete(task_id)
        db.commit()
        return task
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str, db: Session = Depends(get_db)) -> None:
    repo = TaskRepository(db)
    try:
        repo.delete(task_id)
        db.commit()
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
