"""
routes/logs.py
================

Why this file exists
---------------------
Exposes `logging/run_logger.list_recent_logs()` over HTTP so the frontend's
Execution Logs panel can display run history: prompt, tools used,
arguments, results, approval status, timing, and outcome for each run.
"""

import logging

from fastapi import APIRouter

from app.logging.run_logger import list_recent_logs

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def get_logs(limit: int = 50):
    """Return the most recent execution logs, newest first."""
    return {"logs": list_recent_logs(limit=limit)}
