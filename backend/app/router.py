"""
router.py
=========

Why this file exists
---------------------
Two responsibilities that sit BETWEEN the tool implementations and the
LangGraph agent nodes:

1. `GLOBAL_TOOL_REGISTRY` — merges the four per-domain tool registries
   (`task_tools`, `note_tools`, `planning_tools`, `report_tools`) into one
   dict that `agent/nodes.py` uses for both LLM tool-selection (building
   the tool description list) and actual dispatch (looking up the callable
   by name).

2. Cheap, deterministic pre-filters — `is_greeting_only` and
   `requires_approval` — that let `agent/nodes.py` avoid unnecessary LLM
   calls or DB writes without needing full intent classification for
   obviously-safe cases. This directly implements "Prevents unnecessary
   tool calls" and "Never call tools unnecessarily" from the spec.

How it interacts with the rest of the system
-----------------------------------------------
- `agent/nodes.py` imports `GLOBAL_TOOL_REGISTRY`, `get_tool_descriptions`,
  `dispatch_tool`, and `tool_requires_approval` from this module.
- `tools/*.py` are the only modules this file imports from — it never
  touches the database or LLM directly.
"""

import logging
from typing import Any, Dict, List, Optional

from app.schemas.tool_models import ToolResult
from app.tools.note_tools import NOTE_TOOL_REGISTRY
from app.tools.planning_tools import PLANNING_TOOL_REGISTRY
from app.tools.report_tools import REPORT_TOOL_REGISTRY
from app.tools.task_tools import TASK_TOOL_REGISTRY

logger = logging.getLogger(__name__)

# Single source of truth for every tool the agent can call.
GLOBAL_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    **TASK_TOOL_REGISTRY,
    **NOTE_TOOL_REGISTRY,
    **PLANNING_TOOL_REGISTRY,
    **REPORT_TOOL_REGISTRY,
}

# Simple greetings/small talk that never need tool routing or even an LLM
# intent call — short-circuited directly to a canned-but-varied response
# path in `agent/nodes.py`. Kept intentionally small and conservative:
# anything not an exact/near match falls through to real intent analysis.
_GREETING_PHRASES = {
    "hi", "hello", "hey", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "ok", "okay", "cool", "got it", "sounds good",
}


def is_greeting_only(message: str) -> bool:
    """
    Returns True if the message is unambiguously small talk with no
    possible task/note intent — a cheap pre-filter before spending an LLM
    call on intent classification.
    """
    normalized = message.strip().lower().rstrip("!.?")
    return normalized in _GREETING_PHRASES


def get_tool_descriptions() -> str:
    """
    Build the human-readable tool description block injected into the
    tool-selection prompt (see `agent/prompts.py`'s
    `TOOL_SELECTION_SYSTEM_PROMPT`).
    """
    lines = []
    for name, entry in GLOBAL_TOOL_REGISTRY.items():
        schema = entry["input_schema"]
        fields = ", ".join(schema.model_fields.keys())
        lines.append(f"- {name}({fields}): {entry['description']}")
    return "\n".join(lines)


def tool_requires_approval(tool_name: str) -> bool:
    """Returns whether the named tool is on the sensitive-action list."""
    entry = GLOBAL_TOOL_REGISTRY.get(tool_name)
    if entry is None:
        return False
    return bool(entry["requires_approval"])


def dispatch_tool(tool_name: str, arguments: Dict[str, Any], tool_call_id: str) -> ToolResult:
    """
    Look up and invoke a tool by name. Returns a `ToolResult` in ALL cases
    — including when the tool name is unknown — so the agent graph never
    has to handle a raised exception for tool dispatch (ERROR HANDLING:
    'Unsupported request').
    """
    entry = GLOBAL_TOOL_REGISTRY.get(tool_name)
    if entry is None:
        logger.warning("dispatch_tool: unknown tool_name=%r", tool_name)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            success=False,
            error=f"Unknown tool: {tool_name!r}. This request is not supported.",
        )
    try:
        return entry["fn"](arguments, tool_call_id)
    except Exception as exc:  # noqa: BLE001 - absolute last-resort safety net
        logger.error("dispatch_tool: unhandled exception in tool %r: %s", tool_name, exc, exc_info=True)
        return ToolResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            success=False,
            error=f"Unexpected error executing tool {tool_name!r}: {exc}",
        )


def resolve_referenced_task(
    ordinal_or_id: str, referenced_tasks: List[Dict[str, Any]]
) -> Optional[str]:
    """
    Resolve a positional reference ("the second one", "task 2", or a raw
    task ID) against the list of tasks last shown to the user.

    This is the concrete mechanism behind the STATE MANAGEMENT example:
    'Show high priority tasks' -> later -> 'Mark the second one complete'.

    Returns the resolved task_id, or None if it cannot be confidently
    resolved (caller should then trigger clarification rather than guess).
    """
    if not referenced_tasks:
        return None

    normalized = ordinal_or_id.strip().lower()

    # Direct ID match (already a UUID-like string).
    for t in referenced_tasks:
        if t.get("id") == ordinal_or_id:
            return t["id"]

    ordinal_words = {
        "first": 1, "1st": 1, "one": 1,
        "second": 2, "2nd": 2, "two": 2,
        "third": 3, "3rd": 3, "three": 3,
        "fourth": 4, "4th": 4, "four": 4,
        "fifth": 5, "5th": 5, "five": 5,
    }
    for word, position in ordinal_words.items():
        if word in normalized:
            index = position - 1
            if 0 <= index < len(referenced_tasks):
                return referenced_tasks[index].get("id")

    # Try to parse a bare number like "task 2" / "#2".
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if digits:
        index = int(digits) - 1
        if 0 <= index < len(referenced_tasks):
            return referenced_tasks[index].get("id")

    return None
