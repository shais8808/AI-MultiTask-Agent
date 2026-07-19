"""
tools/task_tools.py
=====================

Why this file exists
---------------------
Implements the four REQUIRED task tools: `create_task`, `list_tasks`,
`update_task`, `complete_task`. Each tool is a plain, independently
testable Python function that:

1. Accepts a Pydantic input schema (already validated by the caller,
   validated AGAIN here defensively via `.model_validate` for tools
   invoked with raw dicts from the LLM).
2. Talks to the database ONLY through `TaskRepository` — never raw SQL/ORM.
3. Returns a `ToolResult` — success/failure is always explicit, never a
   raised exception that could crash the agent graph.

How it interacts with the rest of the system
-----------------------------------------------
- Registered in `TASK_TOOL_REGISTRY` at the bottom of this file, which
  `agent/nodes.py` merges into the global tool registry used for tool
  selection and execution.
- `update_task` and `complete_task` are marked `requires_approval=True`
  per the APPROVAL RULES spec (updating/completing tasks are sensitive).
- `database/connection.get_db_session` is used directly (not FastAPI's
  `Depends(get_db)`) because tools execute inside LangGraph nodes, not
  HTTP request handlers.
"""

import logging
import time
from typing import Any, Dict

from app.database.connection import get_db_session
from app.database.repository import TaskNotFoundError, TaskRepository
from app.schemas.task import TaskComplete, TaskCreate, TaskListFilter, TaskUpdate
from app.schemas.tool_models import ToolResult

logger = logging.getLogger(__name__)


def create_task(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Create a new task.

    Parameters
    ----------
    arguments : dict
        Raw arguments (from LLM tool selection or API request), validated
        here against `TaskCreate`.
    tool_call_id : str
        Correlates this result back to the originating `ToolCall`.

    Returns
    -------
    ToolResult
        `success=True` with the created task's data on success;
        `success=False` with a descriptive error on validation or DB failure.
    """
    start = time.monotonic()
    try:
        data = TaskCreate.model_validate(arguments)
    except Exception as exc:  # Pydantic ValidationError or type coercion failure
        logger.warning("create_task validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="create_task",
            success=False,
            error=f"Invalid arguments for create_task: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = TaskRepository(db)
            task = repo.create(data)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="create_task",
            success=True,
            output={"task": task.model_dump(mode="json")},
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001 - normalized into ToolResult
        logger.error("create_task database error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="create_task",
            success=False,
            error=f"Failed to create task due to a database error: {exc}",
        )


def list_tasks(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    List tasks, optionally filtered by status, priority, tag, or overdue-only.

    Never requires approval — read-only, safe to call freely.
    """
    start = time.monotonic()
    try:
        data = TaskListFilter.model_validate(arguments)
    except Exception as exc:
        logger.warning("list_tasks validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="list_tasks",
            success=False,
            error=f"Invalid arguments for list_tasks: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = TaskRepository(db)
            tasks = repo.list(
                status=data.status,
                priority=data.priority,
                tag=data.tag,
                overdue_only=data.overdue_only,
                limit=data.limit,
            )
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="list_tasks",
            success=True,
            output={
                "tasks": [t.model_dump(mode="json") for t in tasks],
                "count": len(tasks),
            },
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("list_tasks database error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="list_tasks",
            success=False,
            error=f"Failed to list tasks due to a database error: {exc}",
        )


def update_task(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Update an existing task's fields. SENSITIVE — requires human approval
    before execution (enforced by the Approval Gate node, not this function;
    this function assumes approval has already been granted by the time
    it is called).
    """
    start = time.monotonic()
    try:
        data = TaskUpdate.model_validate(arguments)
    except Exception as exc:
        logger.warning("update_task validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="update_task",
            success=False,
            error=f"Invalid arguments for update_task: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = TaskRepository(db)
            task = repo.update(data)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="update_task",
            success=True,
            output={"task": task.model_dump(mode="json")},
            duration_ms=duration_ms,
        )
    except TaskNotFoundError as exc:
        logger.warning("update_task: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="update_task",
            success=False,
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("update_task database error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="update_task",
            success=False,
            error=f"Failed to update task due to a database error: {exc}",
        )


def complete_task(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Mark a task as completed. SENSITIVE — requires human approval before
    execution (enforced by the Approval Gate node).
    """
    start = time.monotonic()
    try:
        data = TaskComplete.model_validate(arguments)
    except Exception as exc:
        logger.warning("complete_task validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="complete_task",
            success=False,
            error=f"Invalid arguments for complete_task: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = TaskRepository(db)
            task = repo.complete(data.task_id)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="complete_task",
            success=True,
            output={"task": task.model_dump(mode="json")},
            duration_ms=duration_ms,
        )
    except TaskNotFoundError as exc:
        logger.warning("complete_task: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="complete_task",
            success=False,
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("complete_task database error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="complete_task",
            success=False,
            error=f"Failed to complete task due to a database error: {exc}",
        )


# ---------------------------------------------------------------------
# Registry entry consumed by agent/nodes.py's global tool registry.
# Each entry: (callable, requires_approval, description-for-LLM-selection)
# ---------------------------------------------------------------------
TASK_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "create_task": {
        "fn": create_task,
        "requires_approval": False,
        "description": "Create a new task with title, description, priority, due date, and tags.",
        "input_schema": TaskCreate,
    },
    "list_tasks": {
        "fn": list_tasks,
        "requires_approval": False,
        "description": "List tasks, optionally filtered by status, priority, tag, or overdue-only.",
        "input_schema": TaskListFilter,
    },
    "update_task": {
        "fn": update_task,
        "requires_approval": True,
        "description": "Update fields of an existing task (title, description, priority, status, due date, tags).",
        "input_schema": TaskUpdate,
    },
    "complete_task": {
        "fn": complete_task,
        "requires_approval": True,
        "description": "Mark a specific task as completed.",
        "input_schema": TaskComplete,
    },
}
