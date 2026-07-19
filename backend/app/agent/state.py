"""
agent/state.py
================

Why this file exists
---------------------
LangGraph threads a single mutable state object through every node in the
graph. This file defines that state's shape (`AgentState`, a `TypedDict`
as required by LangGraph's `StateGraph`) plus reducer functions that
control HOW each field is updated when a node returns a partial state dict
(append vs overwrite).

This is the concrete implementation of the STATE MANAGEMENT requirement:
conversation history, referenced tasks (for pronoun resolution like "the
second one"), previous tool outputs, and user preferences all live here
and persist across a session via LangGraph's checkpointer (see `graph.py`).

How it interacts with the rest of the system
-----------------------------------------------
- `agent/nodes.py` — every node function has signature
  `def node(state: AgentState) -> dict` and returns a PARTIAL state update.
- `agent/graph.py` — builds `StateGraph(AgentState)` using this schema and
  attaches a checkpointer keyed by `session_id` so state survives across
  multiple `/api/chat` calls in the same conversation, and across the
  approval-interrupt pause described in `nodes.py`.
- `services/memory_service.py` — reads/writes the `referenced_tasks` and
  `preferences` fields to resolve follow-up references like "mark the
  second one complete".
"""

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from app.schemas.tool_models import ApprovalDecision, ApprovalRequest, ToolCall, ToolResult


def _keep_latest(_old: Any, new: Any) -> Any:
    """Reducer: always overwrite with the newest value (default LangGraph behavior, made explicit)."""
    return new


class ConversationTurn(TypedDict):
    """One turn in the conversation history."""

    role: str  # "user" | "assistant"
    content: str


class AgentState(TypedDict, total=False):
    """
    The complete state threaded through the LangGraph agent graph.

    Fields using `Annotated[..., operator.add]` ACCUMULATE across nodes
    within a single run (e.g. tool_calls executed in sequence); all other
    fields are overwritten by whichever node sets them last.
    """

    # --- Identity ---
    session_id: str
    run_id: str

    # --- Conversation & input ---
    conversation: Annotated[List[ConversationTurn], operator.add]
    user_message: str

    # --- Session memory (STATE MANAGEMENT requirement) ---
    referenced_tasks: List[Dict[str, Any]]  # last task list shown to the user, for "the second one"
    preferences: Dict[str, Any]  # e.g. {"default_priority": "high"}
    llm_provider: Optional[str]
    llm_model: Optional[str]

    # --- Agent reasoning ---
    intent: str  # "chat_only" | "tool_required" | "clarification_needed"
    clarification_question: Optional[str]

    # --- Tool execution pipeline ---
    tool_calls: Annotated[List[ToolCall], operator.add]
    tool_results: Annotated[List[ToolResult], operator.add]

    # --- Approval gate ---
    pending_approval: Optional[ApprovalRequest]
    approval_decision: Optional[ApprovalDecision]

    # --- Control flow / safety ---
    step_count: int
    max_steps_exceeded: bool
    retry_count: int

    # --- Output ---
    final_response: str
    status: str  # "completed" | "awaiting_approval" | "error" | "clarification_needed"
    error: Optional[str]


def new_state(session_id: str, run_id: str, user_message: str) -> AgentState:
    """
    Factory for a fresh `AgentState` at the start of a run. Existing
    session fields (`conversation`, `referenced_tasks`, `preferences`)
    are intentionally NOT reset here — the caller (see `agent/graph.py` /
    `services/memory_service.py`) merges this with any prior session state
    before invoking the graph, so memory persists across turns.
    """
    return AgentState(
        session_id=session_id,
        run_id=run_id,
        conversation=[{"role": "user", "content": user_message}],
        user_message=user_message,
        referenced_tasks=[],
        preferences={},
        llm_provider=None,
        llm_model=None,
        intent="",
        clarification_question=None,
        tool_calls=[],
        tool_results=[],
        pending_approval=None,
        approval_decision=None,
        step_count=0,
        max_steps_exceeded=False,
        retry_count=0,
        final_response="",
        status="in_progress",
        error=None,
    )
