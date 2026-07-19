"""
schemas/tool_models.py
=======================

Why this file exists
---------------------
Task and Note schemas describe DOMAIN data. This file describes AGENT
PROTOCOL data — the generic envelopes used by every tool call regardless
of which specific tool is invoked: `ToolCall`, `ToolResult`,
`ApprovalRequest`, `ApprovalDecision`, and `ChatMessage`.

Keeping these separate from domain schemas means the agent/graph layer can
reason about "a tool call happened" and "approval is needed" without
knowing anything about tasks or notes specifically — new tools (e.g. a
future calendar tool) plug into this same protocol with zero changes here.

How it interacts with the rest of the system
-----------------------------------------------
- `agent/state.py` uses `ToolCall` / `ToolResult` / `ApprovalRequest` as
  the typed contents of the agent's state lists.
- `agent/nodes.py` constructs `ToolCall` after tool selection, `ToolResult`
  after execution, and `ApprovalRequest` when a sensitive tool is detected.
- `services/approval_service.py` consumes `ApprovalDecision` to resolve a
  pending `ApprovalRequest`.
- `routes/chat.py` (Phase 6) uses `ChatMessage` for the request/response
  body of the `/api/chat` endpoint.
"""

import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    """Lifecycle of a sensitive-action approval request."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


class ToolCall(BaseModel):
    """
    Represents the agent's decision to invoke a specific tool with specific
    arguments — produced by the Tool Selection node, consumed by the
    Approval Gate and Tool Execution nodes.
    """

    tool_call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = Field(..., description="Registered name of the tool to invoke.")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = Field(
        default=False, description="True if this tool is on the sensitive-action list."
    )


class ToolResult(BaseModel):
    """Represents the outcome of executing a `ToolCall`."""

    tool_call_id: str
    tool_name: str
    success: bool
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None


class ApprovalRequest(BaseModel):
    """
    Presented to the user when a `ToolCall.requires_approval` is True.
    The frontend renders this as the Approval Modal (action, tool,
    arguments, approve/reject/edit).
    """

    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    tool_call: ToolCall
    action_summary: str = Field(
        ..., description="Human-readable description of what will happen, e.g. "
        "'Mark task \"Finish report\" as completed'."
    )
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApprovalDecision(BaseModel):
    """Input schema for `POST /api/approvals/{approval_id}` — the human's decision."""

    approval_id: str
    decision: ApprovalStatus = Field(
        ..., description="Must be one of APPROVED, REJECTED, or EDITED."
    )
    edited_arguments: Optional[Dict[str, Any]] = Field(
        default=None, description="Required if decision == EDITED; replaces the tool call arguments."
    )


class ChatMessage(BaseModel):
    """Request/response body for the `/api/chat` endpoint."""

    session_id: str = Field(..., description="Stable ID identifying the user's conversation session.")
    message: str = Field(..., min_length=1, max_length=10000)
    llm_provider: Optional[str] = Field(
        default=None,
        description="Override the LLM provider for this request (gemini, github, or openrouter).",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the model identifier for this request.",
    )


class ChatResponse(BaseModel):
    """Response body returned by `/api/chat`."""

    run_id: str
    session_id: str
    reply: str
    tool_calls: List[ToolCall] = Field(default_factory=list)
    tool_results: List[ToolResult] = Field(default_factory=list)
    pending_approval: Optional[ApprovalRequest] = None
    status: str = Field(
        default="completed",
        description="One of: completed, awaiting_approval, error, clarification_needed.",
    )
