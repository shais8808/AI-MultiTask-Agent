"""
routes/chat.py
================

Why this file exists
---------------------
The primary entrypoint into the agent: `POST /api/chat`. Wraps
`agent/graph.run_new_conversation_turn()` with the HTTP/session
plumbing — pulling prior session memory before the run and persisting
updated memory after it, plus registering any pending approval with
`services/approval_service.py` so the frontend's Approval Modal can
subsequently fetch and resolve it.

How it interacts with the rest of the system
-----------------------------------------------
- Reads/writes `services/memory_service.py` for conversation history,
  referenced tasks, and preferences (STATE MANAGEMENT requirement).
- Calls `agent/graph.run_new_conversation_turn()` to execute the graph.
- On `awaiting_approval`, registers the pending approval and returns it
  in the response so the frontend can render the Approval Modal
  immediately, without a second round-trip.
- Every error path returns a structured `ChatResponse` with
  `status="error"` rather than letting an exception bubble up to
  `main.py`'s generic 500 handler — chat-specific errors deserve a
  chat-shaped response.
"""

import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.agent.graph import run_new_conversation_turn
from app.schemas.tool_models import ChatMessage, ChatResponse
from app.services.approval_service import get_approval_service
from app.services.memory_service import get_memory_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(message: ChatMessage) -> ChatResponse:
    """
    Process one user message through the agent graph and return the
    result. If the agent selected a sensitive tool, the response's
    `status` will be `"awaiting_approval"` and `pending_approval` will be
    populated — the frontend should render the Approval Modal and later
    call `POST /api/approvals/{approval_id}`.
    """
    memory = get_memory_service()
    approvals = get_approval_service()

    session = memory.get_or_create(message.session_id)
    run_id = str(uuid.uuid4())

    try:
        result = run_new_conversation_turn(
            session_id=message.session_id,
            run_id=run_id,
            user_message=message.message,
            conversation=session.conversation,
            referenced_tasks=session.referenced_tasks,
            preferences=session.preferences,
            llm_provider=message.llm_provider,
            llm_model=message.model,
        )
    except Exception as exc:  # noqa: BLE001 - agent-level failure, not a server bug
        logger.error("Agent run failed for run_id=%s: %s", run_id, exc, exc_info=True)
        return ChatResponse(
            run_id=run_id,
            session_id=message.session_id,
            reply="Something went wrong while processing your request. Please try again.",
            status="error",
        )

    # Persist updated session memory regardless of outcome.
    memory.append_turn(message.session_id, "user", message.message)
    if result.get("final_response"):
        memory.append_turn(message.session_id, "assistant", result["final_response"])
    if result.get("referenced_tasks"):
        memory.set_referenced_tasks(message.session_id, result["referenced_tasks"])

    pending_approval = result.get("pending_approval")
    if pending_approval is not None and result.get("status") == "awaiting_approval":
        approvals.register_pending(pending_approval)

    return ChatResponse(
        run_id=run_id,
        session_id=message.session_id,
        reply=result.get("final_response", ""),
        tool_calls=result.get("tool_calls", []),
        tool_results=result.get("tool_results", []),
        pending_approval=pending_approval,
        status=result.get("status", "completed"),
    )
