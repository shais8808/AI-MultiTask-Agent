"""
tools/planning_tools.py
=========================

Why this file exists
---------------------
Implements `generate_work_plan` (REQUIRED) and `detect_overdue_tasks`
(BONUS). Both are read-focused planning tools: they gather task data via
`TaskRepository` and, for `generate_work_plan`, use the LLM to turn a raw
task list into a prioritized, actionable plan.

How it interacts with the rest of the system
-----------------------------------------------
- `generate_work_plan` depends on `services/llm_service.py` for the
  reasoning step. If the LLM call fails, the tool falls back to a simple
  rule-based ordering (priority desc, then due date asc) rather than
  failing outright — this satisfies ERROR HANDLING's "LLM error" case
  with graceful degradation instead of a hard failure.
- `detect_overdue_tasks` is pure repository logic — no LLM call needed.
- Neither tool requires approval (both are read-only / advisory).
"""

import logging
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.database.connection import get_db_session
from app.database.repository import TaskRepository
from app.schemas.tool_models import ToolResult
from app.services.llm_service import LLMServiceError, get_llm_service

logger = logging.getLogger(__name__)


class WorkPlanRequest(BaseModel):
    """Input schema for `generate_work_plan`."""

    focus: Optional[str] = Field(
        default=None, description="Optional focus area, e.g. 'today' or 'this week'."
    )
    max_items: int = Field(default=10, ge=1, le=50)


class OverdueRequest(BaseModel):
    """Input schema for `detect_overdue_tasks` (no parameters needed, but kept for consistency)."""

    pass


def generate_work_plan(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Generate a prioritized work plan from the user's current pending/
    in-progress tasks. Uses the LLM to order and group tasks sensibly; if
    the LLM is unavailable, falls back to a deterministic priority/due-date
    sort so the tool never fully fails just because the LLM had an issue.
    """
    start = time.monotonic()
    try:
        data = WorkPlanRequest.model_validate(arguments)
    except Exception as exc:
        logger.warning("generate_work_plan validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="generate_work_plan",
            success=False,
            error=f"Invalid arguments for generate_work_plan: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = TaskRepository(db)
            pending = repo.list(status=None, limit=200)
            actionable = [
                t for t in pending if t.status.value in ("pending", "in_progress", "blocked")
            ][: data.max_items * 3]

        if not actionable:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ToolResult(
                tool_call_id=tool_call_id,
                tool_name="generate_work_plan",
                success=True,
                output={"plan": [], "message": "No pending tasks to plan."},
                duration_ms=duration_ms,
            )

        plan = _generate_plan_with_llm(actionable, data.max_items, data.focus)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="generate_work_plan",
            success=True,
            output={"plan": plan},
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("generate_work_plan error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="generate_work_plan",
            success=False,
            error=f"Failed to generate work plan: {exc}",
        )


def _generate_plan_with_llm(actionable: List[Any], max_items: int, focus: Optional[str]) -> List[Dict[str, Any]]:
    """
    Attempt an LLM-ordered plan; fall back to deterministic sort on any
    LLM failure. Returns a list of {task_id, title, reason} dicts.
    """
    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    fallback_sorted = sorted(
        actionable,
        key=lambda t: (
            priority_rank.get(t.priority.value, 4),
            t.due_date or t.created_at,
        ),
    )[:max_items]
    fallback_plan = [
        {
            "task_id": t.id,
            "title": t.title,
            "reason": f"Priority={t.priority.value}, due={t.due_date.isoformat() if t.due_date else 'none'}",
        }
        for t in fallback_sorted
    ]

    try:
        llm = get_llm_service()
    except LLMServiceError as exc:
        logger.warning("LLM unavailable for generate_work_plan, using fallback sort: %s", exc)
        return fallback_plan

    task_summaries = "\n".join(
        f"- id={t.id} | title={t.title!r} | priority={t.priority.value} | "
        f"status={t.status.value} | due={t.due_date.isoformat() if t.due_date else 'none'}"
        for t in actionable
    )
    focus_clause = f" with a focus on '{focus}'" if focus else ""
    prompt = (
        f"Given this list of tasks{focus_clause}, produce a prioritized work plan "
        f"of at most {max_items} tasks. For each, give a one-sentence reason it belongs "
        f"at that position (urgency, priority, dependencies).\n\n"
        f"Tasks:\n{task_summaries}\n\n"
        f'Return JSON: {{"plan": [{{"task_id": "...", "title": "...", "reason": "..."}}]}}'
    )
    try:
        result = llm.generate_json(prompt, system_prompt="You are a productivity planning assistant.")
        plan = result.get("plan")
        if not isinstance(plan, list) or not plan:
            raise ValueError("LLM returned no usable plan")
        return plan[:max_items]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM plan generation failed, using fallback sort: %s", exc)
        return fallback_plan


def detect_overdue_tasks(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Detect all tasks whose due date has passed and are not yet completed
    or cancelled. Pure repository query — no LLM involved.
    """
    start = time.monotonic()
    try:
        OverdueRequest.model_validate(arguments or {})
    except Exception as exc:
        logger.warning("detect_overdue_tasks validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="detect_overdue_tasks",
            success=False,
            error=f"Invalid arguments for detect_overdue_tasks: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = TaskRepository(db)
            overdue = repo.detect_overdue()
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="detect_overdue_tasks",
            success=True,
            output={
                "overdue_tasks": [t.model_dump(mode="json") for t in overdue],
                "count": len(overdue),
            },
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("detect_overdue_tasks database error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="detect_overdue_tasks",
            success=False,
            error=f"Failed to detect overdue tasks due to a database error: {exc}",
        )


PLANNING_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "generate_work_plan": {
        "fn": generate_work_plan,
        "requires_approval": False,
        "description": "Generate a prioritized work plan from current pending/in-progress tasks.",
        "input_schema": WorkPlanRequest,
    },
    "detect_overdue_tasks": {
        "fn": detect_overdue_tasks,
        "requires_approval": False,
        "description": "Detect all tasks that are overdue and not yet completed or cancelled.",
        "input_schema": OverdueRequest,
    },
}
