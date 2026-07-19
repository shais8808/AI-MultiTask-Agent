"""
routes/approvals.py
=====================

Why this file exists
---------------------
Exposes the human-in-the-loop approval workflow over HTTP:
`GET /api/approvals` lists everything currently awaiting a decision
(rendered as the Approval Modal / queue in the frontend), and
`POST /api/approvals/{approval_id}` submits Approve / Reject / Edit and
resumes the paused agent graph.

How it interacts with the rest of the system
-----------------------------------------------
- Uses `services/approval_service.py` to resolve the decision against the
  pending registry.
- Calls `agent/graph.resume_run()` to continue the paused graph exactly
  where it left off (see `graph.py`'s module docstring for why this does
  NOT re-run intent/tool-selection/validation).
- Updates `services/memory_service.py` with the resumed run's final
  response and any new referenced tasks, same as `routes/chat.py`.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.agent.graph import resume_run
from app.schemas.tool_models import ApprovalDecision, ChatResponse
from app.services.approval_service import ApprovalNotFoundError, get_approval_service
from app.services.memory_service import get_memory_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_pending_approvals():
    """List all approvals currently awaiting a human decision."""
    approvals = get_approval_service()
    return {"pending": approvals.list_pending()}


@router.get("/{approval_id}")
async def get_approval(approval_id: str):
    """Fetch a single pending approval by ID — used to render the Approval Modal's details."""
    approvals = get_approval_service()
    try:
        return approvals.get_pending(approval_id)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{approval_id}", response_model=ChatResponse)
async def submit_approval_decision(approval_id: str, decision: ApprovalDecision) -> ChatResponse:
    """
    Submit a human decision (approve/reject/edit) for a pending approval
    and resume the paused agent run.
    """
    if decision.approval_id != approval_id:
        raise HTTPException(
            status_code=400,
            detail="approval_id in the URL and in the request body must match.",
        )

    approvals = get_approval_service()
    try:
        resolved_request = approvals.resolve(decision)
    except ApprovalNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_id = resolved_request.run_id
    try:
        result = resume_run(run_id, decision)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to resume run_id=%s after approval: %s", run_id, exc, exc_info=True)
        return ChatResponse(
            run_id=run_id,
            session_id="",
            reply="The approved action could not be completed due to an internal error.",
            status="error",
        )

    if result is None:
        raise HTTPException(
            status_code=410,
            detail=f"No paused run found for run_id={run_id!r}; it may have already been resumed.",
        )

    session_id = result.get("session_id", "")
    memory = get_memory_service()
    if result.get("final_response"):
        memory.append_turn(session_id, "assistant", result["final_response"])
    if result.get("referenced_tasks"):
        memory.set_referenced_tasks(session_id, result["referenced_tasks"])

    return ChatResponse(
        run_id=run_id,
        session_id=session_id,
        reply=result.get("final_response", ""),
        tool_calls=result.get("tool_calls", []),
        tool_results=result.get("tool_results", []),
        pending_approval=None,
        status=result.get("status", "completed"),
    )
