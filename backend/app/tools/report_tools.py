"""
tools/report_tools.py
=======================

Why this file exists
---------------------
Implements the LLM-reasoning-heavy tools: `extract_meeting_actions`
(REQUIRED), plus the bonus tools `generate_weekly_report`,
`detect_overdue_tasks` counterpart reporting, `draft_follow_up_email`,
and `convert_meeting_notes_to_tasks`.

These tools compose two things: `TaskRepository`/`NoteRepository` for data,
and `LLMService` for turning unstructured text (meeting notes) into
structured output (action items, task drafts) or turning structured data
(a week of tasks) into prose (a report).

How it interacts with the rest of the system
-----------------------------------------------
- `extract_meeting_actions` and `convert_meeting_notes_to_tasks` power
  Workflow 1 (Meeting Notes -> Extract Actions -> Show Tasks -> Approval ->
  Create Tasks). `extract_meeting_actions` only PROPOSES tasks (no DB writes,
  no approval needed); `convert_meeting_notes_to_tasks` actually CREATES
  them and is marked `requires_approval=True` because "Creating multiple
  tasks" is explicitly a sensitive action per the approval rules.
- `generate_weekly_report` powers Workflow 3 (Weekly Review).
- `draft_follow_up_email` only drafts text — it never sends anything (no
  email-sending capability exists in this system), so it is NOT sensitive.
  If a future `send_email` tool is added, THAT tool must require approval.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.database.connection import get_db_session
from app.database.repository import TaskRepository
from app.schemas.task import TaskCreate
from app.schemas.tool_models import ToolResult
from app.services.llm_service import LLMServiceError, get_llm_service

logger = logging.getLogger(__name__)


def _ensure_aware(dt: datetime) -> datetime:
    """
    Normalize a possibly-naive datetime to timezone-aware UTC before
    comparing it against `datetime.now(timezone.utc)`.

    SQLite doesn't preserve timezone info on `DateTime(timezone=True)`
    columns — values round-tripped through it often come back naive even
    though they were stored as true UTC (see `models.py`'s `_utcnow()`).
    Without this, comparing a naive `Task.updated_at`/`due_date` against
    an aware "now" raises `TypeError: can't compare offset-naive and
    offset-aware datetimes` — this is safe regardless of DB backend since
    every datetime this app stores is UTC to begin with.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------
# extract_meeting_actions (REQUIRED)
# ---------------------------------------------------------------------
class ExtractActionsRequest(BaseModel):
    """Input schema for `extract_meeting_actions`."""

    meeting_text: str = Field(..., min_length=1, max_length=20000)


class ActionItem(BaseModel):
    """A single proposed action item extracted from meeting notes."""

    title: str
    description: str = ""
    priority: str = "medium"
    owner_hint: Optional[str] = None


def extract_meeting_actions(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Extract actionable items from raw meeting notes text using the LLM.
    Does NOT create tasks — this is the "propose" half of Workflow 1.
    Use `convert_meeting_notes_to_tasks` to actually persist them after
    the user reviews and approves.
    """
    start = time.monotonic()
    try:
        data = ExtractActionsRequest.model_validate(arguments)
    except Exception as exc:
        logger.warning("extract_meeting_actions validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="extract_meeting_actions",
            success=False,
            error=f"Invalid arguments for extract_meeting_actions: {exc}",
        )

    try:
        llm = get_llm_service()
        prompt = (
            "Extract all actionable tasks from these meeting notes. For each action item, "
            "give a short title, a one-sentence description, an inferred priority "
            "(low/medium/high/critical), and an owner_hint if a person is named.\n\n"
            f"Meeting notes:\n{data.meeting_text}\n\n"
            'Return JSON: {"actions": [{"title": "...", "description": "...", '
            '"priority": "...", "owner_hint": "..." or null}]}'
        )
        result = llm.generate_json(prompt, system_prompt="You extract structured action items from meeting notes.")
        raw_actions = result.get("actions", [])
        actions = [ActionItem.model_validate(a) for a in raw_actions]
    except LLMServiceError as exc:
        logger.error("extract_meeting_actions LLM error: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="extract_meeting_actions",
            success=False,
            error=f"LLM error while extracting actions: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("extract_meeting_actions error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="extract_meeting_actions",
            success=False,
            error=f"Failed to extract meeting actions: {exc}",
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    return ToolResult(
        tool_call_id=tool_call_id,
        tool_name="extract_meeting_actions",
        success=True,
        output={"actions": [a.model_dump() for a in actions], "count": len(actions)},
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------
# convert_meeting_notes_to_tasks (BONUS) - SENSITIVE
# ---------------------------------------------------------------------
class ConvertToTasksRequest(BaseModel):
    """Input schema for `convert_meeting_notes_to_tasks`."""

    actions: List[ActionItem] = Field(..., min_length=1)
    source: str = Field(default="meeting_notes", max_length=100)


def convert_meeting_notes_to_tasks(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Persist a list of previously-extracted action items as actual Task
    records. SENSITIVE — "Creating multiple tasks" requires approval;
    this function assumes approval has already been granted.
    """
    start = time.monotonic()
    try:
        data = ConvertToTasksRequest.model_validate(arguments)
    except Exception as exc:
        logger.warning("convert_meeting_notes_to_tasks validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="convert_meeting_notes_to_tasks",
            success=False,
            error=f"Invalid arguments for convert_meeting_notes_to_tasks: {exc}",
        )

    created = []
    try:
        with get_db_session() as db:
            repo = TaskRepository(db)
            for action in data.actions:
                priority = action.priority if action.priority in ("low", "medium", "high", "critical") else "medium"
                task_create = TaskCreate(
                    title=action.title,
                    description=action.description,
                    priority=priority,  # type: ignore[arg-type]
                    source=data.source,
                    notes=f"Owner hint: {action.owner_hint}" if action.owner_hint else "",
                )
                task = repo.create(task_create)
                created.append(task.model_dump(mode="json"))
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="convert_meeting_notes_to_tasks",
            success=True,
            output={"created_tasks": created, "count": len(created)},
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("convert_meeting_notes_to_tasks error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="convert_meeting_notes_to_tasks",
            success=False,
            error=f"Failed to create tasks from meeting notes: {exc}",
        )


# ---------------------------------------------------------------------
# generate_weekly_report (BONUS)
# ---------------------------------------------------------------------
class WeeklyReportRequest(BaseModel):
    """Input schema for `generate_weekly_report`."""

    days: int = Field(default=7, ge=1, le=90)


def generate_weekly_report(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Generate a weekly summary report: completion stats plus an LLM-written
    narrative summary. Powers Workflow 3 (Weekly Review).
    """
    start = time.monotonic()
    try:
        data = WeeklyReportRequest.model_validate(arguments or {})
    except Exception as exc:
        logger.warning("generate_weekly_report validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="generate_weekly_report",
            success=False,
            error=f"Invalid arguments for generate_weekly_report: {exc}",
        )

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=data.days)
        with get_db_session() as db:
            repo = TaskRepository(db)
            all_tasks = repo.list(limit=1000)

        recent = [t for t in all_tasks if _ensure_aware(t.updated_at) >= cutoff]
        completed = [t for t in recent if t.status.value == "completed"]
        active = [t for t in all_tasks if t.status.value not in ("completed", "cancelled")]
        overdue = [t for t in all_tasks if t.due_date and _ensure_aware(t.due_date) < datetime.now(timezone.utc)
                   and t.status.value not in ("completed", "cancelled")]

        stats = {
            "period_days": data.days,
            "total_active_tasks": len(active),
            "completed_this_period": len(completed),
            "overdue_count": len(overdue),
        }

        summary_text = _summarize_report(stats, completed, overdue)
        # Structurally separate from the narrative summary (Workflow C
        # step 5: "Agent recommends priorities for the next week") so the
        # frontend/caller can render "what to focus on" as its own list
        # rather than parsing it back out of prose.
        recommended_priorities = _recommend_next_week_priorities(active, overdue)

        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="generate_weekly_report",
            success=True,
            output={
                "stats": stats,
                "summary": summary_text,
                "recommended_priorities": recommended_priorities,
            },
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("generate_weekly_report error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="generate_weekly_report",
            success=False,
            error=f"Failed to generate weekly report: {exc}",
        )


def _summarize_report(stats: Dict[str, Any], completed: List[Any], overdue: List[Any]) -> str:
    """Generate the narrative portion via LLM; fall back to a templated summary on failure."""
    fallback = (
        f"In the last {stats['period_days']} days: {stats['completed_this_period']} task(s) completed, "
        f"{stats['overdue_count']} task(s) currently overdue, "
        f"{stats['total_active_tasks']} task(s) still active."
    )
    try:
        llm = get_llm_service()
        completed_titles = ", ".join(t.title for t in completed[:15]) or "none"
        overdue_titles = ", ".join(t.title for t in overdue[:15]) or "none"
        prompt = (
            f"Write a concise 3-4 sentence weekly productivity summary.\n"
            f"Stats: {stats}\n"
            f"Completed tasks: {completed_titles}\n"
            f"Overdue tasks: {overdue_titles}\n"
            "Return JSON: {\"summary\": \"...\"}"
        )
        result = llm.generate_json(prompt, system_prompt="You write concise productivity summaries.")
        summary = result.get("summary")
        return summary if isinstance(summary, str) and summary.strip() else fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning("Weekly report LLM summary failed, using fallback: %s", exc)
        return fallback


def _recommend_next_week_priorities(
    active: List[Any], overdue: List[Any], max_items: int = 5
) -> List[Dict[str, Any]]:
    """
    Recommend what to focus on next week — Workflow C step 5. Tries the
    LLM for reasoned recommendations (weighing overdue status alongside
    priority); falls back to a deterministic overdue-first, then
    priority/due-date sort on any LLM failure, matching the graceful-
    degradation pattern used by `generate_work_plan`.
    """
    if not active:
        return []

    priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    overdue_ids = {t.id for t in overdue}
    fallback_sorted = sorted(
        active,
        key=lambda t: (
            t.id not in overdue_ids,  # overdue tasks sort first
            priority_rank.get(t.priority.value, 4),
            t.due_date or t.created_at,
        ),
    )[:max_items]
    fallback = [
        {
            "task_id": t.id,
            "title": t.title,
            "reason": (
                f"Overdue, priority={t.priority.value}."
                if t.id in overdue_ids
                else f"Priority={t.priority.value}, due={t.due_date.isoformat() if t.due_date else 'none'}."
            ),
        }
        for t in fallback_sorted
    ]

    try:
        llm = get_llm_service()
    except LLMServiceError as exc:
        logger.warning("LLM unavailable for weekly priority recommendation, using fallback sort: %s", exc)
        return fallback

    task_summaries = "\n".join(
        f"- id={t.id} | title={t.title!r} | priority={t.priority.value} | "
        f"status={t.status.value} | due={t.due_date.isoformat() if t.due_date else 'none'} | "
        f"overdue={t.id in overdue_ids}"
        for t in active[:50]
    )
    prompt = (
        f"Given these currently active tasks, recommend the top {max_items} priorities "
        "to focus on NEXT WEEK. Favor overdue and high/critical-priority items. For each, "
        "give a one-sentence reason.\n\n"
        f"Tasks:\n{task_summaries}\n\n"
        'Return JSON: {"priorities": [{"task_id": "...", "title": "...", "reason": "..."}]}'
    )
    try:
        result = llm.generate_json(prompt, system_prompt="You are a productivity planning assistant.")
        priorities = result.get("priorities")
        if not isinstance(priorities, list) or not priorities:
            raise ValueError("LLM returned no usable priorities")
        return priorities[:max_items]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM weekly priority recommendation failed, using fallback sort: %s", exc)
        return fallback


# ---------------------------------------------------------------------
# draft_follow_up_email (BONUS) - NOT sensitive (draft only, never sent)
# ---------------------------------------------------------------------
class DraftEmailRequest(BaseModel):
    """Input schema for `draft_follow_up_email`."""

    context: str = Field(..., min_length=1, max_length=5000, description="What the email should follow up on.")
    recipient_name: Optional[str] = Field(default=None, max_length=200)
    tone: str = Field(default="professional", max_length=50)


def draft_follow_up_email(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Draft a follow-up email based on context (e.g. a completed task or
    meeting outcome). Returns TEXT ONLY — this tool has no ability to send
    email, so it is not a sensitive action.
    """
    start = time.monotonic()
    try:
        data = DraftEmailRequest.model_validate(arguments)
    except Exception as exc:
        logger.warning("draft_follow_up_email validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="draft_follow_up_email",
            success=False,
            error=f"Invalid arguments for draft_follow_up_email: {exc}",
        )

    try:
        llm = get_llm_service()
        recipient_clause = f" addressed to {data.recipient_name}" if data.recipient_name else ""
        prompt = (
            f"Draft a {data.tone} follow-up email{recipient_clause} based on this context:\n"
            f"{data.context}\n\n"
            'Return JSON: {"subject": "...", "body": "..."}'
        )
        result = llm.generate_json(prompt, system_prompt="You draft clear, concise professional emails.")
        subject = result.get("subject", "Follow-up")
        body = result.get("body", "")
        if not body:
            raise ValueError("LLM returned an empty email body")
    except Exception as exc:  # noqa: BLE001
        logger.error("draft_follow_up_email error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="draft_follow_up_email",
            success=False,
            error=f"Failed to draft follow-up email: {exc}",
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    return ToolResult(
        tool_call_id=tool_call_id,
        tool_name="draft_follow_up_email",
        success=True,
        output={"subject": subject, "body": body},
        duration_ms=duration_ms,
    )


REPORT_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "extract_meeting_actions": {
        "fn": extract_meeting_actions,
        "requires_approval": False,
        "description": "Extract actionable items from raw meeting notes text (proposal only, does not create tasks).",
        "input_schema": ExtractActionsRequest,
    },
    "convert_meeting_notes_to_tasks": {
        "fn": convert_meeting_notes_to_tasks,
        "requires_approval": True,
        "description": "Persist a list of previously extracted action items as real tasks.",
        "input_schema": ConvertToTasksRequest,
    },
    "generate_weekly_report": {
        "fn": generate_weekly_report,
        "requires_approval": False,
        "description": (
            "Generate a weekly productivity report: completion stats, an LLM-written summary, "
            "and recommended priorities to focus on next week."
        ),
        "input_schema": WeeklyReportRequest,
    },
    "draft_follow_up_email": {
        "fn": draft_follow_up_email,
        "requires_approval": False,
        "description": "Draft (not send) a follow-up email based on given context.",
        "input_schema": DraftEmailRequest,
    },
}
