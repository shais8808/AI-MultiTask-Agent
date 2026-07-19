"""
tools/note_tools.py
=====================

Why this file exists
---------------------
Implements the two REQUIRED note tools: `search_notes`, `save_note`.
Follows the exact same pattern as `task_tools.py` for consistency: Pydantic
validation, repository-only DB access, `ToolResult` everywhere.

How it interacts with the rest of the system
-----------------------------------------------
- Registered in `NOTE_TOOL_REGISTRY`, merged into the global tool registry
  by `agent/nodes.py`.
- `save_note` is NOT sensitive (creating a note is reversible/low-risk,
  unlike creating multiple tasks) — no approval required.
- Notes saved here are later searchable by `extract_meeting_actions` and
  `convert_meeting_notes_to_tasks` in `tools/report_tools.py`.
"""

import logging
import time
from typing import Any, Dict

from app.database.connection import get_db_session
from app.database.repository import NoteRepository
from app.schemas.note import NoteCreate, NoteSearchQuery
from app.schemas.tool_models import ToolResult

logger = logging.getLogger(__name__)


def save_note(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Save a new note (e.g. meeting notes, reference material, ideas).

    Not sensitive — does not require approval.
    """
    start = time.monotonic()
    try:
        data = NoteCreate.model_validate(arguments)
    except Exception as exc:
        logger.warning("save_note validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="save_note",
            success=False,
            error=f"Invalid arguments for save_note: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = NoteRepository(db)
            note = repo.create(data)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="save_note",
            success=True,
            output={"note": note.model_dump(mode="json")},
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("save_note database error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="save_note",
            success=False,
            error=f"Failed to save note due to a database error: {exc}",
        )


def search_notes(arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Search notes by keyword (case-insensitive substring match on title and
    content), optionally filtered by category. Read-only, safe to call freely.
    """
    start = time.monotonic()
    try:
        data = NoteSearchQuery.model_validate(arguments)
    except Exception as exc:
        logger.warning("search_notes validation failed: %s", exc)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="search_notes",
            success=False,
            error=f"Invalid arguments for search_notes: {exc}",
        )

    try:
        with get_db_session() as db:
            repo = NoteRepository(db)
            notes = repo.search(query=data.query, category=data.category, limit=data.limit)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="search_notes",
            success=True,
            output={
                "notes": [n.model_dump(mode="json") for n in notes],
                "count": len(notes),
            },
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("search_notes database error: %s", exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name="search_notes",
            success=False,
            error=f"Failed to search notes due to a database error: {exc}",
        )


NOTE_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "save_note": {
        "fn": save_note,
        "requires_approval": False,
        "description": "Save a new note with title, content, category, and tags.",
        "input_schema": NoteCreate,
    },
    "search_notes": {
        "fn": search_notes,
        "requires_approval": False,
        "description": "Search existing notes by keyword, optionally filtered by category.",
        "input_schema": NoteSearchQuery,
    },
}
