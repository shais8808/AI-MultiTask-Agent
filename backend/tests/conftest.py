"""
tests/conftest.py
===================

Why this file exists
---------------------
Shared pytest fixtures for the entire test suite:

- `test_db`: a fresh, isolated SQLite database per test (avoids test
  pollution between runs — each test gets a clean schema).
- `client`: a FastAPI `TestClient` wired to that isolated database.
- `fake_llm`: a deterministic stand-in for `LLMService` so agent/graph
  tests don't require a real Gemini API key or network access, and run
  fast and deterministically.

Environment variables are set BEFORE any `app.*` module is imported,
since `app.config.get_settings()` is an `lru_cache`d singleton read at
import time by several modules.
"""

import os
import uuid

os.environ.setdefault("GEMINI_API_KEY", "test-key-for-pytest")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("LOG_FILE", "logs/test_agent_runs.log")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database.connection import Base


@pytest.fixture()
def test_db(monkeypatch):
    """
    Provide a fresh in-memory SQLite database for a single test, and
    monkeypatch `database.connection`'s engine/session factory to use it.
    `StaticPool` keeps the same in-memory DB alive across multiple
    connections within one test (SQLite's default in-memory behavior is
    one DB per connection, which would otherwise break the app's
    multi-connection request-scoped session pattern).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    import app.database.connection as conn_module

    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(conn_module, "engine", engine)
    monkeypatch.setattr(conn_module, "SessionLocal", TestSessionLocal)

    yield TestSessionLocal

    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(test_db):
    """FastAPI TestClient wired to the isolated `test_db`."""
    from app.main import app

    with TestClient(app) as c:
        yield c


class FakeLLMService:
    """
    Deterministic stand-in for `services.llm_service.LLMService`.
    Tests configure `.next_json` / `.next_text` to control what the
    "LLM" returns, so agent-graph tests are fast and don't need network
    access or a real API key.
    """

    def __init__(self):
        self.next_json = {"intent": "chat_only"}
        self.next_text = "OK."
        self.calls = []
        self.json_queue = []
        self.raise_error = None

    def invoke(self, prompt, system_prompt=None):
        self.calls.append(("invoke", prompt))
        if self.raise_error:
            from app.services.llm_service import LLMResponseError

            raise LLMResponseError(self.raise_error)
        return self.next_text

    def generate_json(self, prompt, system_prompt=None):
        self.calls.append(("generate_json", prompt))
        if self.raise_error:
            from app.services.llm_service import LLMResponseError

            raise LLMResponseError(self.raise_error)
        if self.json_queue:
            return self.json_queue.pop(0)
        return self.next_json


@pytest.fixture()
def fake_llm(monkeypatch):
    """Patch `get_llm_service` everywhere it's imported to return a `FakeLLMService`."""
    fake = FakeLLMService()

    import app.agent.nodes as nodes_module
    import app.services.llm_service as llm_module
    import app.tools.planning_tools as planning_module
    import app.tools.report_tools as report_module

    monkeypatch.setattr(llm_module, "get_llm_service", lambda *a, **k: fake)
    monkeypatch.setattr(nodes_module, "get_llm_service", lambda *a, **k: fake)
    monkeypatch.setattr(planning_module, "get_llm_service", lambda *a, **k: fake)
    monkeypatch.setattr(report_module, "get_llm_service", lambda *a, **k: fake)

    return fake


@pytest.fixture()
def new_run_id():
    return str(uuid.uuid4())
