"""
services/memory_service.py
============================

Why this file exists
---------------------
Implements the STATE MANAGEMENT requirement outside of LangGraph's own
checkpointer: this is the durable, explicit session store the rest of the
app reasons about directly (e.g. the FastAPI chat route needs to know
"does session X already exist" before deciding whether to seed a fresh
`AgentState` or continue an existing one).

Session data held here:
- `conversation`: full turn history (mirrors `AgentState.conversation`)
- `referenced_tasks`: the last task list shown, for ordinal resolution
  ("the second one")
- `preferences`: lightweight user preferences the agent has picked up
  (e.g. a default priority) — deliberately simple key-value, no schema,
  since preferences are advisory hints, not validated domain data.

Design note on persistence: like `approval_service.py`, this is an
in-memory, process-local store. It is intentionally NOT the same thing as
the `ExecutionLog` audit trail in the database — that is permanent and
per-run; this is ephemeral and per-session, cleared on process restart.
For a production deployment this would be swapped for a Redis-backed
store without changing any caller — see `docs/architecture.md`.

How it interacts with the rest of the system
-----------------------------------------------
- `agent/graph.py` reads/writes this at the start/end of every `/api/chat`
  call to seed and persist `AgentState.conversation`, `.referenced_tasks`,
  `.preferences` across turns within a session.
- `router.py`'s `resolve_referenced_task()` consumes `referenced_tasks`
  fetched from here.
"""

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_CONVERSATION_TURNS = 40  # bounded history to keep prompts small


class SessionMemory:
    """In-memory representation of a single conversation session's state."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.conversation: List[Dict[str, str]] = []
        self.referenced_tasks: List[Dict[str, Any]] = []
        self.preferences: Dict[str, Any] = {}


class MemoryService:
    """Thread-safe registry of `SessionMemory` objects, keyed by session_id."""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionMemory] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> SessionMemory:
        """Fetch a session's memory, creating a fresh one if it doesn't exist yet."""
        with self._lock:
            if session_id not in self._sessions:
                logger.info("Creating new session memory for session_id=%s", session_id)
                self._sessions[session_id] = SessionMemory(session_id)
            return self._sessions[session_id]

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        """Append a conversation turn, trimming to the most recent N turns."""
        with self._lock:
            session = self._sessions.setdefault(session_id, SessionMemory(session_id))
            session.conversation.append({"role": role, "content": content})
            if len(session.conversation) > _MAX_CONVERSATION_TURNS:
                session.conversation = session.conversation[-_MAX_CONVERSATION_TURNS:]

    def set_referenced_tasks(self, session_id: str, tasks: List[Dict[str, Any]]) -> None:
        """
        Record the most recent task list shown to the user, enabling
        follow-up ordinal references like "mark the second one complete".
        """
        with self._lock:
            session = self._sessions.setdefault(session_id, SessionMemory(session_id))
            session.referenced_tasks = tasks

    def get_referenced_tasks(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(session_id)
            return list(session.referenced_tasks) if session else []

    def update_preference(self, session_id: str, key: str, value: Any) -> None:
        with self._lock:
            session = self._sessions.setdefault(session_id, SessionMemory(session_id))
            session.preferences[key] = value

    def get_preferences(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            return dict(session.preferences) if session else {}

    def get_conversation(self, session_id: str) -> List[Dict[str, str]]:
        with self._lock:
            session = self._sessions.get(session_id)
            return list(session.conversation) if session else []

    def clear(self, session_id: str) -> None:
        """Clear a session's memory entirely — e.g. for a 'start over' command."""
        with self._lock:
            self._sessions.pop(session_id, None)


_memory_service_singleton: Optional[MemoryService] = None


def get_memory_service() -> MemoryService:
    """Process-wide singleton accessor for `MemoryService`."""
    global _memory_service_singleton
    if _memory_service_singleton is None:
        _memory_service_singleton = MemoryService()
    return _memory_service_singleton
