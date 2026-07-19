"""
tests/test_agent_loop.py
==========================
Covers: Step Limit, Loop Prevention (REQUIRED test categories).

Verifies that a run exceeding `settings.max_agent_steps` is forcibly
terminated with a clear error rather than looping indefinitely.
"""

import app.agent.nodes as nodes_module
from app.agent.graph import run_new_conversation_turn


def test_run_stops_when_max_steps_exceeded(test_db, fake_llm, new_run_id, monkeypatch):
    """
    With max_agent_steps set artificially low, a normal tool-required
    flow (which needs more than that many node visits) must terminate
    with status='error' instead of running indefinitely or crashing.
    """
    monkeypatch.setattr(nodes_module.settings, "max_agent_steps", 2)

    fake_llm.json_queue = [
        {"intent": "tool_required"},  # step 1: intent
        {  # step 2: tool_selection -- would need validation/approval/execution after,
            "tool_calls": [{"tool_name": "list_tasks", "arguments": {}}],
            "needs_clarification": False,
            "clarification_question": None,
        },
    ]

    result = run_new_conversation_turn(
        session_id="loop-test",
        run_id=new_run_id,
        user_message="show my tasks",
        conversation=[],
        referenced_tasks=[],
        preferences={},
    )

    assert result["status"] == "error"
    assert result["max_steps_exceeded"] is True
    assert "too many steps" in result["final_response"].lower() or result.get("error")
    assert result["step_count"] > 2  # confirms it did NOT run away indefinitely; stopped right after limit


def test_step_counter_increments_across_nodes(test_db, fake_llm, new_run_id):
    """A normal chat-only run (2 nodes: intent -> response_generation) increments step_count correctly."""
    fake_llm.next_json = {"intent": "chat_only"}
    fake_llm.next_text = "Hello there!"

    result = run_new_conversation_turn(
        session_id="step-test",
        run_id=new_run_id,
        user_message="hello, what can you do?",
        conversation=[],
        referenced_tasks=[],
        preferences={},
    )

    assert result["status"] == "completed"
    # intent + response_generation = 2 step increments.
    # logging_node deliberately does NOT increment step_count — it's an
    # audit/bookkeeping step, not an agent reasoning step subject to the
    # loop-prevention limit.
    assert result["step_count"] == 2
