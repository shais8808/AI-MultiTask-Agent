"""
tests/test_agent_approval.py
==============================
Covers: Approval (REQUIRED test category).

Verifies the full pause-for-approval / resume-after-decision cycle
through the LangGraph agent, using `fake_llm` to deterministically select
the sensitive `complete_task` tool.
"""

from app.agent.graph import resume_run, run_new_conversation_turn
from app.schemas.tool_models import ApprovalDecision, ApprovalStatus
from app.tools.task_tools import create_task


def test_sensitive_tool_call_pauses_for_approval(test_db, fake_llm, new_run_id):
    """A run selecting a sensitive tool (complete_task) must pause with status=awaiting_approval."""
    created = create_task({"title": "Needs approval"}, "seed-1")
    task_id = created.output["task"]["id"]

    fake_llm.next_json = {"intent": "tool_required"}
    fake_llm.json_queue = [
        {"intent": "tool_required"},
        {
            "tool_calls": [{"tool_name": "complete_task", "arguments": {"task_id": task_id}}],
            "needs_clarification": False,
            "clarification_question": None,
        },
    ]

    result = run_new_conversation_turn(
        session_id="s1",
        run_id=new_run_id,
        user_message="mark it complete",
        conversation=[],
        referenced_tasks=[],
        preferences={},
    )

    assert result["status"] == "awaiting_approval"
    assert result["pending_approval"] is not None
    assert result["pending_approval"].tool_call.tool_name == "complete_task"
    # Tool must NOT have executed yet.
    assert result.get("tool_results", []) == []


def test_approval_resume_executes_the_tool(test_db, fake_llm, new_run_id):
    """After an APPROVED decision, resuming the run must actually execute the tool."""
    created = create_task({"title": "Approve me"}, "seed-2")
    task_id = created.output["task"]["id"]

    fake_llm.json_queue = [
        {"intent": "tool_required"},
        {
            "tool_calls": [{"tool_name": "complete_task", "arguments": {"task_id": task_id}}],
            "needs_clarification": False,
            "clarification_question": None,
        },
    ]

    paused = run_new_conversation_turn(
        session_id="s2",
        run_id=new_run_id,
        user_message="complete it",
        conversation=[],
        referenced_tasks=[],
        preferences={},
    )
    assert paused["status"] == "awaiting_approval"

    decision = ApprovalDecision(
        approval_id=paused["pending_approval"].approval_id, decision=ApprovalStatus.APPROVED
    )
    resumed = resume_run(new_run_id, decision)

    assert resumed["status"] == "completed"
    assert len(resumed["tool_results"]) == 1
    assert resumed["tool_results"][0].success is True
    assert resumed["tool_results"][0].output["task"]["status"] == "completed"


def test_approval_rejection_does_not_execute_the_tool(test_db, fake_llm, new_run_id):
    """After a REJECTED decision, the tool must never execute."""
    created = create_task({"title": "Reject me"}, "seed-3")
    task_id = created.output["task"]["id"]

    fake_llm.json_queue = [
        {"intent": "tool_required"},
        {
            "tool_calls": [{"tool_name": "complete_task", "arguments": {"task_id": task_id}}],
            "needs_clarification": False,
            "clarification_question": None,
        },
    ]

    paused = run_new_conversation_turn(
        session_id="s3",
        run_id=new_run_id,
        user_message="complete it",
        conversation=[],
        referenced_tasks=[],
        preferences={},
    )
    decision = ApprovalDecision(
        approval_id=paused["pending_approval"].approval_id, decision=ApprovalStatus.REJECTED
    )
    resumed = resume_run(new_run_id, decision)

    assert resumed["status"] == "completed"
    assert resumed.get("tool_results", []) == []
    assert "won't proceed" in resumed["final_response"].lower()
