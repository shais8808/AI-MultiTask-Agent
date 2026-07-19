"""
services/approval_service.py
==============================

Why this file exists
---------------------
The Approval Gate node (`agent/nodes.py`) needs somewhere to register a
pending approval so the FastAPI `/api/approvals/{approval_id}` endpoint
(Phase 6) can look it up, and somewhere to resolve it once the human
responds. This service owns that in-memory registry.

Design note on persistence: pending approvals are inherently short-lived
(seconds to minutes, bounded by a human being present to respond) and
process-local, so an in-memory dict keyed by `approval_id` is sufficient
and matches the single-process deployment target of this project (see
`docs/architecture.md`). The FINAL decision, once made, IS persisted —
via `ExecutionLog.approval_status` written by `logging/run_logger.py`.

How it interacts with the rest of the system
-----------------------------------------------
- `agent/nodes.py`'s approval gate node calls `register_pending()` when a
  sensitive tool call is detected, then the graph pauses (see `graph.py`).
- Phase 6's `/api/approvals` routes call `get_pending()` to render the
  Approval Modal and `resolve()` when the user clicks Approve/Reject/Edit.
"""

import logging
import threading
from typing import Dict, Optional

from app.schemas.tool_models import ApprovalDecision, ApprovalRequest, ApprovalStatus

logger = logging.getLogger(__name__)


class ApprovalServiceError(Exception):
    """Base class for approval service errors."""


class ApprovalNotFoundError(ApprovalServiceError):
    """Raised when an approval_id does not correspond to a known pending approval."""


class ApprovalService:
    """Thread-safe in-memory registry of pending human-approval requests."""

    def __init__(self) -> None:
        self._pending: Dict[str, ApprovalRequest] = {}
        self._decisions: Dict[str, ApprovalDecision] = {}
        self._lock = threading.Lock()

    def register_pending(self, request: ApprovalRequest) -> None:
        """Register a new approval request as awaiting human decision."""
        with self._lock:
            self._pending[request.approval_id] = request
        logger.info(
            "Registered pending approval id=%s tool=%s run_id=%s",
            request.approval_id,
            request.tool_call.tool_name,
            request.run_id,
        )

    def get_pending(self, approval_id: str) -> ApprovalRequest:
        """Fetch a pending approval by ID. Raises `ApprovalNotFoundError` if unknown."""
        with self._lock:
            req = self._pending.get(approval_id)
        if req is None:
            raise ApprovalNotFoundError(f"No pending approval with id={approval_id!r}.")
        return req

    def list_pending(self) -> list:
        """List all currently pending approvals — powers a frontend polling fallback."""
        with self._lock:
            return list(self._pending.values())

    def resolve(self, decision: ApprovalDecision) -> ApprovalRequest:
        """
        Record the human's decision and remove the approval from the
        pending registry. Returns the updated `ApprovalRequest` (with
        `status` set) so the caller can log/display it.

        Raises
        ------
        ApprovalNotFoundError
            If `decision.approval_id` does not match a pending approval
            (e.g. already resolved, or an invalid ID from the client).
        ValueError
            If `decision.decision == EDITED` but no `edited_arguments`
            were supplied.
        """
        if decision.decision == ApprovalStatus.EDITED and not decision.edited_arguments:
            raise ValueError("edited_arguments is required when decision is EDITED.")

        with self._lock:
            req = self._pending.pop(decision.approval_id, None)
            if req is None:
                raise ApprovalNotFoundError(
                    f"No pending approval with id={decision.approval_id!r}."
                )
            req.status = decision.decision
            if decision.decision == ApprovalStatus.EDITED and decision.edited_arguments:
                req.tool_call.arguments = decision.edited_arguments
            self._decisions[decision.approval_id] = decision

        logger.info(
            "Resolved approval id=%s decision=%s", decision.approval_id, decision.decision.value
        )
        return req

    def get_decision(self, approval_id: str) -> Optional[ApprovalDecision]:
        """Fetch a previously recorded decision, if any (used by resumed graph runs)."""
        with self._lock:
            return self._decisions.get(approval_id)


_approval_service_singleton: Optional[ApprovalService] = None


def get_approval_service() -> ApprovalService:
    """Process-wide singleton accessor for `ApprovalService`."""
    global _approval_service_singleton
    if _approval_service_singleton is None:
        _approval_service_singleton = ApprovalService()
    return _approval_service_singleton
