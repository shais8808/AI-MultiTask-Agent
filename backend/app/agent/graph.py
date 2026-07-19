"""
agent/graph.py
================

Why this file exists
---------------------
Wires the node functions in `nodes.py` into an executable LangGraph
`StateGraph`, matching the ARCHITECTURE diagram exactly:

    Intent Analysis -> Tool Selection -> Validation -> Approval Gate
    -> Tool Execution -> Response Generation -> Execution Logging -> END

Key design decision: APPROVAL RESUME WITHOUT RE-RUNNING EARLIER NODES.
When a sensitive tool call needs approval, the graph reaches END early
with `status="awaiting_approval"`. The full `AgentState` (including the
already-selected, already-validated `tool_calls`) is preserved by the
`MemorySaver` checkpointer, keyed by `run_id` as the LangGraph "thread_id".
When the human's decision arrives (Phase 6's `/api/approvals` route calls
`resume_run()` below), we do NOT restart from `intent_node` — that would
needlessly re-call the LLM and burn steps against `max_agent_steps`.
Instead, a conditional entry point (`_entry_router`) detects that
`approval_decision` is already set on the incoming state and jumps
straight to `approval_gate_node`, which then proceeds to execution.

How it interacts with the rest of the system
-----------------------------------------------
- `routes/chat.py` (Phase 6) calls `run_new_conversation_turn()` to start
  a fresh run.
- `routes/approvals.py` (Phase 6) calls `resume_run()` after recording an
  `ApprovalDecision` via `services/approval_service.py`.
- Both entry points return the final `AgentState`, from which the route
  builds the `ChatResponse` schema.
"""

import logging
from typing import Any, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    approval_gate_node,
    intent_node,
    logging_node,
    response_generation_node,
    tool_execution_node,
    tool_selection_node,
    validation_node,
)
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


def _entry_router(state: AgentState) -> str:
    """
    Conditional entry point: resumed runs (where an approval decision has
    just been recorded) skip straight to the approval gate instead of
    re-running intent analysis / tool selection / validation.
    """
    if state.get("approval_decision") is not None:
        return "approval_gate_node"
    return "intent_node"


def _after_intent(state: AgentState) -> str:
    if state.get("status") == "error":
        return "response_generation"
    intent = state.get("intent")
    if intent in ("chat_only", "clarification_needed"):
        return "response_generation"
    return "tool_selection"


def _after_tool_selection(state: AgentState) -> str:
    if state.get("status") == "error":
        return "response_generation"
    if state.get("intent") == "clarification_needed":
        return "response_generation"
    return "validation"


def _after_validation(state: AgentState) -> str:
    if state.get("status") == "error":
        return "response_generation"
    return "approval_gate"


def _after_approval_gate(state: AgentState) -> str:
    if state.get("status") in ("error", "awaiting_approval"):
        return "logging"
    decision = state.get("approval_decision")
    if decision is not None and decision.decision.value == "rejected":
        # Rejected: skip tool_execution entirely. response_generation_node
        # will pass through the "won't proceed" message already set on
        # final_response by approval_gate_node.
        return "response_generation"
    return "tool_execution"


def build_graph():
    """
    Construct and compile the agent StateGraph with an in-memory
    checkpointer. See module docstring for the pause/resume design.
    """
    graph = StateGraph(AgentState)

    graph.add_node("intent_node", intent_node)
    graph.add_node("tool_selection_node", tool_selection_node)
    graph.add_node("validation_node", validation_node)
    graph.add_node("approval_gate_node", approval_gate_node)
    graph.add_node("tool_execution_node", tool_execution_node)
    graph.add_node("response_generation_node", response_generation_node)
    graph.add_node("logging_node", logging_node)

    graph.set_conditional_entry_point(
        _entry_router, {"intent_node": "intent_node", "approval_gate_node": "approval_gate_node"}
    )

    graph.add_conditional_edges(
        "intent_node", _after_intent, {"tool_selection": "tool_selection_node", "response_generation": "response_generation_node"}
    )
    graph.add_conditional_edges(
        "tool_selection_node",
        _after_tool_selection,
        {"validation": "validation_node", "response_generation": "response_generation_node"},
    )
    graph.add_conditional_edges(
        "validation_node", _after_validation, {"approval_gate": "approval_gate_node", "response_generation": "response_generation_node"}
    )
    graph.add_conditional_edges(
        "approval_gate_node",
        _after_approval_gate,
        {"tool_execution": "tool_execution_node", "logging": "logging_node", "response_generation": "response_generation_node"},
    )
    graph.add_edge("tool_execution_node", "response_generation_node")
    graph.add_edge("response_generation_node", "logging_node")
    graph.add_edge("logging_node", END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


# Module-level compiled graph — built once per process, reused across requests.
_compiled_graph = None


def get_graph():
    """Return the process-wide compiled graph, building it on first use."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def run_new_conversation_turn(
    session_id: str,
    run_id: str,
    user_message: str,
    conversation: list,
    referenced_tasks: list,
    preferences: dict,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Start a fresh agent run for one user message. Seeds `AgentState` with
    prior session memory (conversation history, referenced tasks,
    preferences) supplied by `services/memory_service.py`.

    Returns the resulting state dict (via `graph.invoke`'s return value),
    which the caller (Phase 6's `/api/chat` route) converts into a
    `ChatResponse`.
    """
    from app.agent.state import new_state

    initial_state = new_state(session_id, run_id, user_message)
    initial_state["conversation"] = conversation + [{"role": "user", "content": user_message}]
    initial_state["referenced_tasks"] = referenced_tasks
    initial_state["preferences"] = preferences
    initial_state["llm_provider"] = llm_provider
    initial_state["llm_model"] = llm_model

    graph = get_graph()
    config = {"configurable": {"thread_id": run_id}}
    logger.info("Starting new run_id=%s for session_id=%s", run_id, session_id)
    result = graph.invoke(initial_state, config=config)
    return result


def resume_run(run_id: str, approval_decision) -> Optional[Dict[str, Any]]:
    """
    Resume a previously-paused run after a human approval decision.

    IMPORTANT: only the DELTA (`approval_decision`, `status` reset) is
    passed as input to `graph.invoke()` — never the full checkpointed
    state. `tool_calls` and `tool_results` use an `operator.add` reducer
    (see `state.py`), so re-submitting the full state dict (which already
    contains those accumulated lists) would cause LangGraph to append them
    to themselves, silently duplicating every previously-executed tool
    call. Passing only the delta lets LangGraph merge it onto the existing
    checkpoint exactly once.

    The `_entry_router` conditional entry point then sees
    `approval_decision` is set on the merged state and jumps straight to
    `approval_gate_node`, skipping intent analysis / tool selection /
    validation entirely.

    Returns None if `run_id` has no checkpointed state (e.g. invalid ID
    or the run already completed and its state was never paused).
    """
    graph = get_graph()
    config = {"configurable": {"thread_id": run_id}}

    checkpoint = graph.get_state(config)
    if not checkpoint or not checkpoint.values:
        logger.warning("resume_run: no checkpointed state found for run_id=%s", run_id)
        return None

    resume_input = {"approval_decision": approval_decision, "status": "in_progress"}

    logger.info("Resuming run_id=%s after approval decision=%s", run_id, approval_decision.decision.value)
    result = graph.invoke(resume_input, config=config)
    return result
