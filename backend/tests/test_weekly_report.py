"""
tests/test_weekly_report.py
=============================
Covers: Multi-step workflow (Weekly Review), Bonus tool coverage.

`generate_weekly_report` returns the narrative summary and the "what to
focus on next week" recommendation as two structurally separate fields
(Workflow C step 5: "Agent recommends priorities for the next week") —
these tests lock in that both are present and independently correct,
with and without a working LLM.
"""

from app.tools.report_tools import generate_weekly_report
from app.tools.task_tools import create_task


def test_generate_weekly_report_includes_recommended_priorities(test_db, fake_llm):
    """The report's output has both a narrative summary and a separate priorities list."""
    create_task({"title": "Overdue critical task", "priority": "critical"}, "seed-1")
    create_task({"title": "Low priority task", "priority": "low"}, "seed-2")

    fake_llm.json_queue = [
        {"summary": "You completed nothing but have two active tasks."},
        {
            "priorities": [
                {"task_id": "x", "title": "Overdue critical task", "reason": "Highest priority."},
            ]
        },
    ]

    result = generate_weekly_report({}, "tc-1")

    assert result.success is True
    assert "summary" in result.output
    assert result.output["summary"] == "You completed nothing but have two active tasks."
    assert result.output["recommended_priorities"] == [
        {"task_id": "x", "title": "Overdue critical task", "reason": "Highest priority."}
    ]


def test_generate_weekly_report_falls_back_when_llm_fails(test_db, fake_llm):
    """
    If the LLM is unavailable, the report still succeeds with a
    deterministic fallback summary AND a deterministic fallback priority
    ranking (overdue-first, then by priority) — never a hard failure.
    """
    create_task({"title": "Critical task", "priority": "critical"}, "seed-3")
    create_task({"title": "Medium task", "priority": "medium"}, "seed-4")

    fake_llm.raise_error = "LLM is down"

    result = generate_weekly_report({}, "tc-2")

    assert result.success is True
    assert result.output["summary"]  # non-empty deterministic fallback text
    priorities = result.output["recommended_priorities"]
    assert len(priorities) == 2
    # Critical-priority task must be ranked ahead of the medium one.
    assert priorities[0]["title"] == "Critical task"


def test_generate_weekly_report_no_active_tasks_returns_empty_priorities(test_db, fake_llm):
    """With no active tasks at all, recommended_priorities is an empty list, not an error."""
    fake_llm.next_json = {"summary": "Nothing going on this week."}

    result = generate_weekly_report({}, "tc-3")

    assert result.success is True
    assert result.output["recommended_priorities"] == []
