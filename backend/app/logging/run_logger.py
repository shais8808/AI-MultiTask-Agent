"""
logging/run_logger.py
=======================

Why this file exists
---------------------
Implements the LOGGING requirement: every agent run gets a persisted
`ExecutionLog` row capturing run_id, prompt, model, tools used, arguments,
results, approval status, errors, timing, and final outcome.

This is the ONLY module allowed to write to `LogRepository` — centralizing
it here means there is exactly one place that could leak a secret into
logs, and exactly one place we need to audit to guarantee we never do.
`_scrub()` is the enforcement point: every dict passed to `finalize()` is
scrubbed for known secret-like keys before serialization, regardless of
what the caller passed in.

How it interacts with the rest of the system
-----------------------------------------------
- `agent/graph.py` calls `start_run()` when a graph run begins and
  `finalize_run()` in a `finally` block when it ends (success, error, or
  paused-for-approval), guaranteeing every run is logged exactly once.
- Uses `database/connection.get_db_session` directly, matching the
  pattern in `tools/*.py` since this also runs outside FastAPI's request
  cycle (it's invoked from within graph nodes).
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database.connection import get_db_session
from app.database.repository import LogRepository

logger = logging.getLogger(__name__)

# Keys that must NEVER be written to a persisted log, regardless of where
# they appear in a nested arguments/results dict.
_SECRET_KEY_MARKERS = {"api_key", "apikey", "gemini_api_key", "password", "token", "secret"}


def _scrub(value: Any) -> Any:
    """
    Recursively scrub dict values whose key looks secret-like, replacing
    them with a redaction marker. Applied to `arguments` and `results`
    before every write to the database.
    """
    if isinstance(value, dict):
        scrubbed = {}
        for k, v in value.items():
            if isinstance(k, str) and any(marker in k.lower() for marker in _SECRET_KEY_MARKERS):
                scrubbed[k] = "***REDACTED***"
            else:
                scrubbed[k] = _scrub(v)
        return scrubbed
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


class RunLogger:
    """Tracks timing and writes the start/finalize rows for a single agent run."""

    def __init__(self, run_id: str, prompt: str, selected_model: str):
        self.run_id = run_id
        self.prompt = prompt
        self.selected_model = selected_model
        self.started_at = datetime.now(timezone.utc)
        self._start_monotonic = time.monotonic()
        self._write_start()

    def _write_start(self) -> None:
        try:
            with get_db_session() as db:
                repo = LogRepository(db)
                repo.create(
                    run_id=self.run_id,
                    prompt=self.prompt,
                    selected_model=self.selected_model,
                    started_at=self.started_at,
                )
        except Exception as exc:  # noqa: BLE001
            # Logging failure must never crash the agent run itself.
            logger.error("Failed to write start log for run_id=%s: %s", self.run_id, exc, exc_info=True)

    def finalize(
        self,
        tools_used: List[str],
        arguments: Dict[str, Any],
        results: Dict[str, Any],
        approval_status: str = "not_required",
        errors: str = "",
        final_outcome: str = "completed",
    ) -> None:
        """
        Write the final state of the run. Safe to call exactly once per
        run — callers (typically `agent/graph.py`) are responsible for
        ensuring this happens in a `finally` block so partially-completed
        runs are still logged with `final_outcome="error"` if needed.
        """
        duration_ms = int((time.monotonic() - self._start_monotonic) * 1000)
        ended_at = datetime.now(timezone.utc)
        try:
            with get_db_session() as db:
                repo = LogRepository(db)
                repo.finalize(
                    run_id=self.run_id,
                    tools_used=tools_used,
                    arguments=_scrub(arguments),
                    results=_scrub(results),
                    approval_status=approval_status,
                    errors=errors,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    final_outcome=final_outcome,
                )
            logger.info(
                "Run %s finalized: outcome=%s duration_ms=%d tools=%s",
                self.run_id, final_outcome, duration_ms, tools_used,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to write finalize log for run_id=%s: %s", self.run_id, exc, exc_info=True)


_run_logger_registry: Dict[str, RunLogger] = {}
_registry_lock = threading.Lock()


def get_or_create_run_logger(run_id: str, prompt: str, selected_model: str) -> RunLogger:
    """
    Return the existing `RunLogger` for `run_id` if this run has already
    started (e.g. this is a resumed graph run after an approval decision),
    or create a brand-new one (which writes the initial DB row) otherwise.

    This registry-based approach guarantees `LogRepository.create()` is
    called exactly once per run_id, even though `agent/nodes.py`'s
    `logging_node` may execute more than once across a pause/resume cycle
    (once when the run pauses for approval, once when it completes).
    Process-local by design, matching `approval_service.py` and
    `memory_service.py`'s in-memory, single-process scope.
    """
    with _registry_lock:
        if run_id not in _run_logger_registry:
            _run_logger_registry[run_id] = RunLogger(run_id, prompt, selected_model)
        return _run_logger_registry[run_id]


def list_recent_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Fetch the most recent execution logs as plain dicts — used by the
    `/api/logs` route (Phase 6) to power the frontend's Execution Logs panel.
    """
    with get_db_session() as db:
        repo = LogRepository(db)
        logs = repo.list_recent(limit=limit)
        return [
            {
                "run_id": log.run_id,
                "prompt": log.prompt,
                "selected_model": log.selected_model,
                "tools_used": log.tools_used,
                "arguments": log.arguments,
                "results": log.results,
                "approval_status": log.approval_status,
                "errors": log.errors,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "ended_at": log.ended_at.isoformat() if log.ended_at else None,
                "duration_ms": log.duration_ms,
                "final_outcome": log.final_outcome,
            }
            for log in logs
        ]
