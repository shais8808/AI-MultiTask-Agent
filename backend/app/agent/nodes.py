"""
agent/nodes.py
================

Why this file exists
---------------------
Implements every node in the LangGraph agent graph as a plain function
`(state: AgentState) -> dict[str, Any]` (LangGraph convention: nodes
return a PARTIAL state update, merged into the full state by the graph
runtime according to each field's reducer in `state.py`).

Each node maps directly to one box in the ARCHITECTURE diagram:
  Intent Analysis -> Tool Selection -> Validation -> Human Approval Node
  -> Tool Execution -> Response Generation -> Execution Logging

How it interacts with the rest of the system
-----------------------------------------------
- Uses `agent/prompts.py` for all LLM prompt text.
- Uses `services/llm_service.py` for LLM calls, `router.py` for tool
  dispatch/description/approval lookups, and `logging/run_logger.py` for
  the final Execution Logging node.
- `agent/graph.py` wires these functions into a `StateGraph` and defines
  the conditional edges between them (which this file does NOT do — nodes
  only decide WHAT happened, `graph.py` decides WHERE to go next).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.agent.prompts import (
    CHAT_ONLY_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    INTENT_USER_TEMPLATE,
    RESPONSE_GENERATION_SYSTEM_PROMPT,
    RESPONSE_GENERATION_USER_TEMPLATE,
    TOOL_SELECTION_SYSTEM_PROMPT,
    TOOL_SELECTION_USER_TEMPLATE,
)
from app.agent.state import AgentState
from app.config import get_settings
from app.logging.run_logger import get_or_create_run_logger
from app.router import (
    dispatch_tool,
    get_tool_descriptions,
    is_greeting_only,
    resolve_referenced_task,
    tool_requires_approval,
)
from app.schemas.tool_models import ApprovalRequest, ApprovalStatus, ToolCall
from app.services.llm_service import LLMServiceError, get_llm_service

logger = logging.getLogger(__name__)
settings = get_settings()


def _format_history(conversation: List[Dict[str, str]]) -> str:
    """Render conversation turns as a simple readable transcript for prompts."""
    if not conversation:
        return "(no prior conversation)"
    return "\n".join(f"{turn['role']}: {turn['content']}" for turn in conversation[-10:])


def _increment_step(state: AgentState) -> Dict[str, Any]:
    """
    Shared step-limit bookkeeping (LOOP PREVENTION: max_agent_steps=8).
    Every node calls this FIRST; if the limit is already exceeded, the
    caller should short-circuit to an error response instead of doing
    further work.
    """
    step_count = state.get("step_count", 0) + 1
    exceeded = step_count > settings.max_agent_steps
    if exceeded:
        logger.warning("Run %s exceeded max_agent_steps=%d", state.get("run_id"), settings.max_agent_steps)
    return {"step_count": step_count, "max_steps_exceeded": exceeded}


# ---------------------------------------------------------------------
# Node 1: Intent Analysis
# ---------------------------------------------------------------------
def intent_node(state: AgentState) -> Dict[str, Any]:
    """
    Classify the user's message as chat_only / tool_required /
    clarification_needed. Short-circuits obvious greetings via
    `router.is_greeting_only()` to avoid an unnecessary LLM call.
    """
    step_update = _increment_step(state)
    if step_update["max_steps_exceeded"]:
        return {**step_update, "status": "error", "error": "Maximum agent steps exceeded."}

    message = state["user_message"]

    if is_greeting_only(message):
        return {**step_update, "intent": "chat_only"}

    try:
        llm = get_llm_service(provider=state.get("llm_provider"), model=state.get("llm_model"))
        prompt = INTENT_USER_TEMPLATE.format(
            history=_format_history(state.get("conversation", [])),
            referenced_tasks=state.get("referenced_tasks", []),
            message=message,
        )
        result = llm.generate_json(prompt, system_prompt=INTENT_SYSTEM_PROMPT)
        intent = result.get("intent", "chat_only")
        if intent not in ("chat_only", "tool_required", "clarification_needed"):
            intent = "chat_only"
        return {
            **step_update,
            "intent": intent,
            "clarification_question": result.get("clarification_question"),
        }
    except LLMServiceError as exc:
        logger.error("intent_node LLM error for run_id=%s: %s", state.get("run_id"), exc)
        return {
            **step_update,
            "status": "error",
            "error": f"Could not analyze your request due to an LLM error: {exc}",
        }


# ---------------------------------------------------------------------
# Node 2: Tool Selection
# ---------------------------------------------------------------------
def tool_selection_node(state: AgentState) -> Dict[str, Any]:
    """
    For `tool_required` intent, ask the LLM to select tool(s) and build
    arguments, resolving any ordinal task references ("the second one")
    against `state["referenced_tasks"]` first.
    """
    step_update = _increment_step(state)
    if step_update["max_steps_exceeded"]:
        return {**step_update, "status": "error", "error": "Maximum agent steps exceeded."}

    try:
        llm = get_llm_service(provider=state.get("llm_provider"), model=state.get("llm_model"))
        prompt = TOOL_SELECTION_USER_TEMPLATE.format(
            history=_format_history(state.get("conversation", [])),
            referenced_tasks=state.get("referenced_tasks", []),
            message=state["user_message"],
        )
        system_prompt = TOOL_SELECTION_SYSTEM_PROMPT.format(
            tool_descriptions=get_tool_descriptions(),
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d (%A)"),
        )
        result = llm.generate_json(prompt, system_prompt=system_prompt)
    except LLMServiceError as exc:
        logger.error("tool_selection_node LLM error for run_id=%s: %s", state.get("run_id"), exc)
        return {
            **step_update,
            "status": "error",
            "error": f"Could not determine which action to take due to an LLM error: {exc}",
        }

    if result.get("needs_clarification"):
        return {
            **step_update,
            "intent": "clarification_needed",
            "clarification_question": result.get("clarification_question", "Could you clarify what you'd like me to do?"),
        }

    raw_calls = result.get("tool_calls", [])
    tool_calls: List[ToolCall] = []
    referenced_tasks = state.get("referenced_tasks", [])

    for raw in raw_calls:
        tool_name = raw.get("tool_name")
        # Drop explicit nulls: the LLM sometimes emits {"limit": null} for
        # an argument it has no opinion on. Every tool schema treats "field
        # omitted" and "field is null" identically (both fall back to the
        # schema default), but Pydantic rejects an explicit None against a
        # non-Optional field type (e.g. `limit: int = 20`) instead of
        # applying the default. Stripping nulls here makes omission the
        # only representation, matching what the schemas actually expect.
        arguments = {k: v for k, v in raw.get("arguments", {}).items() if v is not None}

        # Resolve any ordinal task reference the LLM flagged with a
        # "task_ref" convenience key (e.g. {"task_ref": "second one"})
        # into a concrete task_id before validation.
        if "task_ref" in arguments and "task_id" not in arguments:
            resolved = resolve_referenced_task(str(arguments.pop("task_ref")), referenced_tasks)
            if resolved is None:
                return {
                    **step_update,
                    "intent": "clarification_needed",
                    "clarification_question": (
                        "I couldn't tell which task you meant — could you specify the task title or ID?"
                    ),
                }
            arguments["task_id"] = resolved

        tool_calls.append(
            ToolCall(
                tool_name=tool_name,
                arguments=arguments,
                requires_approval=tool_requires_approval(tool_name),
            )
        )

    return {**step_update, "tool_calls": tool_calls}


# ---------------------------------------------------------------------
# Node 3: Validation
# ---------------------------------------------------------------------
def validation_node(state: AgentState) -> Dict[str, Any]:
    """
    Validate every pending tool call's arguments against its registered
    Pydantic input schema BEFORE approval/execution — catches malformed
    LLM-generated arguments early with a clear error instead of failing
    deep inside a tool.
    """
    step_update = _increment_step(state)
    if step_update["max_steps_exceeded"]:
        return {**step_update, "status": "error", "error": "Maximum agent steps exceeded."}

    from app.router import GLOBAL_TOOL_REGISTRY

    errors = []
    for call in state.get("tool_calls", []):
        entry = GLOBAL_TOOL_REGISTRY.get(call.tool_name)
        if entry is None:
            errors.append(f"Unknown tool: {call.tool_name}")
            continue
        try:
            entry["input_schema"].model_validate(call.arguments)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{call.tool_name}: {exc}")

    if errors:
        logger.warning("validation_node found errors for run_id=%s: %s", state.get("run_id"), errors)
        return {
            **step_update,
            "status": "error",
            "error": "Invalid tool arguments: " + "; ".join(errors),
        }

    return step_update


# ---------------------------------------------------------------------
# Node 4: Human Approval Gate
# ---------------------------------------------------------------------
def approval_gate_node(state: AgentState) -> Dict[str, Any]:
    """
    Check whether any selected tool call requires approval and none has
    been granted yet. If so, build an `ApprovalRequest` and set status to
    "awaiting_approval" — `agent/graph.py` routes this to END, and the
    FastAPI approval route (Phase 6) resumes the graph once a decision
    is recorded in `state["approval_decision"]`.
    """
    step_update = _increment_step(state)
    if step_update["max_steps_exceeded"]:
        return {**step_update, "status": "error", "error": "Maximum agent steps exceeded."}

    decision = state.get("approval_decision")
    tool_calls = state.get("tool_calls", [])

    sensitive_calls = [c for c in tool_calls if c.requires_approval]
    if not sensitive_calls:
        return step_update

    if decision is not None:
        # A decision has been recorded (this is a resumed run).
        if decision.decision == ApprovalStatus.REJECTED:
            return {
                **step_update,
                "status": "completed",
                "final_response": "Okay, I won't proceed with that action.",
            }
        if decision.decision == ApprovalStatus.EDITED and decision.edited_arguments:
            # Mutate the existing ToolCall object in place rather than
            # returning a new `tool_calls` list — that field uses an
            # additive reducer (see state.py), so returning a list here
            # would APPEND rather than replace, duplicating the call.
            sensitive_calls[0].arguments = decision.edited_arguments
        # APPROVED or EDITED — fall through to execution.
        return step_update

    # No decision yet — pause here.
    first_sensitive = sensitive_calls[0]
    approval_request = ApprovalRequest(
        run_id=state["run_id"],
        tool_call=first_sensitive,
        action_summary=_summarize_action(first_sensitive),
    )
    return {
        **step_update,
        "pending_approval": approval_request,
        "status": "awaiting_approval",
    }


def _summarize_action(call: ToolCall) -> str:
    """Build a human-readable summary of a sensitive action for the Approval Modal."""
    summaries = {
        "update_task": f"Update task {call.arguments.get('task_id', '?')} with new field values.",
        "complete_task": f"Mark task {call.arguments.get('task_id', '?')} as completed.",
        "convert_meeting_notes_to_tasks": (
            f"Create {len(call.arguments.get('actions', []))} new task(s) from meeting notes."
        ),
    }
    return summaries.get(call.tool_name, f"Execute {call.tool_name} with the given arguments.")


# ---------------------------------------------------------------------
# Node 5: Tool Execution
# ---------------------------------------------------------------------
def tool_execution_node(state: AgentState) -> Dict[str, Any]:
    """
    Execute every tool call in `state["tool_calls"]` in sequence via
    `router.dispatch_tool`. If any call yields a list of tasks (from
    `list_tasks` or `detect_overdue_tasks`), record it as
    `referenced_tasks` for future ordinal resolution.
    """
    step_update = _increment_step(state)
    if step_update["max_steps_exceeded"]:
        return {**step_update, "status": "error", "error": "Maximum agent steps exceeded."}

    tool_calls = state.get("tool_calls", [])
    if not tool_calls:
        return step_update

    results = []
    new_referenced_tasks = state.get("referenced_tasks", [])

    for call in tool_calls:
        result = dispatch_tool(call.tool_name, call.arguments, call.tool_call_id)
        results.append(result)

        if result.success and result.output:
            if "tasks" in result.output:
                new_referenced_tasks = result.output["tasks"]
            elif "overdue_tasks" in result.output:
                new_referenced_tasks = result.output["overdue_tasks"]
            elif "task" in result.output:
                new_referenced_tasks = [result.output["task"]]

    return {
        **step_update,
        "tool_results": results,
        "referenced_tasks": new_referenced_tasks,
    }


# ---------------------------------------------------------------------
# Node 6: Response Generation
# ---------------------------------------------------------------------
def response_generation_node(state: AgentState) -> Dict[str, Any]:
    """
    Compose the final natural-language reply. For `chat_only` intent,
    responds directly. For tool-based runs, summarizes tool results. For
    `clarification_needed`, surfaces the clarification question as-is.
    """
    step_update = _increment_step(state)
    if step_update["max_steps_exceeded"]:
        return {
            **step_update,
            "status": "error",
            "final_response": (
                "This request required too many steps to complete safely and was stopped. "
                "Please try breaking it into smaller requests."
            ),
        }

    if state.get("status") == "error":
        return {**step_update, "final_response": state.get("error", "An error occurred.")}

    if state.get("status") == "awaiting_approval":
        return step_update  # response is generated after resumption

    if state.get("final_response"):
        # Already set (e.g. by approval_gate_node on rejection).
        return step_update

    intent = state.get("intent")

    if intent == "clarification_needed":
        return {**step_update, "status": "completed", "final_response": state.get(
            "clarification_question", "Could you clarify your request?"
        )}

    try:
        llm = get_llm_service(provider=state.get("llm_provider"), model=state.get("llm_model"))
        if intent == "chat_only":
            reply = llm.invoke(state["user_message"], system_prompt=CHAT_ONLY_SYSTEM_PROMPT)
        else:
            results_summary = [
                {
                    "tool": r.tool_name,
                    "success": r.success,
                    "output": r.output,
                    "error": r.error,
                }
                for r in state.get("tool_results", [])
            ]
            prompt = RESPONSE_GENERATION_USER_TEMPLATE.format(
                message=state["user_message"], tool_results=results_summary
            )
            reply = llm.invoke(prompt, system_prompt=RESPONSE_GENERATION_SYSTEM_PROMPT)
        return {**step_update, "status": "completed", "final_response": reply}
    except LLMServiceError as exc:
        logger.error("response_generation_node LLM error for run_id=%s: %s", state.get("run_id"), exc)
        # Graceful degradation: still return whatever tool results we have,
        # in raw form, rather than failing the whole run.
        fallback = _fallback_summary(state)
        return {**step_update, "status": "completed", "final_response": fallback}


def _fallback_summary(state: AgentState) -> str:
    """Deterministic, non-LLM summary used if response generation's LLM call fails."""
    results = state.get("tool_results", [])
    if not results:
        return "I completed the request, but couldn't generate a summary right now."
    parts = []
    for r in results:
        if r.success:
            parts.append(f"{r.tool_name} succeeded.")
        else:
            parts.append(f"{r.tool_name} failed: {r.error}")
    return " ".join(parts)


# ---------------------------------------------------------------------
# Node 7: Execution Logging
# ---------------------------------------------------------------------
def logging_node(state: AgentState) -> Dict[str, Any]:
    """
    Write the final `ExecutionLog` row for this run. Always runs as the
    last node before END (for completed/error runs) — see `graph.py`.
    Note: runs paused at `awaiting_approval` log a partial record here
    too, and a second `logging_node` pass on resumption overwrites it
    with the final outcome (repository `finalize()` is idempotent per run_id).
    """
    run_logger = get_or_create_run_logger(
        run_id=state["run_id"],
        prompt=state["user_message"],
        selected_model=get_settings().active_model_name,
    )
    tools_used = [c.tool_name for c in state.get("tool_calls", [])]
    arguments = {c.tool_call_id: c.arguments for c in state.get("tool_calls", [])}
    results = {
        r.tool_call_id: {"success": r.success, "output": r.output, "error": r.error}
        for r in state.get("tool_results", [])
    }
    approval = state.get("pending_approval")
    decision = state.get("approval_decision")
    approval_status = (
        decision.decision.value if decision else (approval.status.value if approval else "not_required")
    )

    run_logger.finalize(
        tools_used=tools_used,
        arguments=arguments,
        results=results,
        approval_status=approval_status,
        errors=state.get("error") or "",
        final_outcome=state.get("status", "completed"),
    )
    return {}
