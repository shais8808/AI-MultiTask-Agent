"""
tests/test_meeting_extraction.py
==================================
Covers: Meeting Extraction (REQUIRED test category).
"""

from app.tools.report_tools import convert_meeting_notes_to_tasks, extract_meeting_actions


def test_extract_meeting_actions_returns_structured_items(test_db, fake_llm):
    """extract_meeting_actions parses the LLM's JSON into validated ActionItem objects."""
    fake_llm.next_json = {
        "actions": [
            {
                "title": "Send follow-up deck",
                "description": "Share the updated slides with the client",
                "priority": "high",
                "owner_hint": "Sara",
            },
            {
                "title": "Update budget sheet",
                "description": "Reflect the new Q3 numbers",
                "priority": "medium",
                "owner_hint": None,
            },
        ]
    }

    result = extract_meeting_actions(
        {"meeting_text": "We discussed the client deck and the Q3 budget..."}, "tc-extract"
    )

    assert result.success is True
    assert result.output["count"] == 2
    titles = {a["title"] for a in result.output["actions"]}
    assert titles == {"Send follow-up deck", "Update budget sheet"}


def test_extract_meeting_actions_handles_llm_error_gracefully(test_db, fake_llm):
    """If the LLM call fails, the tool returns success=False rather than raising."""
    fake_llm.raise_error = "simulated LLM outage"
    result = extract_meeting_actions({"meeting_text": "Some notes."}, "tc-extract-2")
    assert result.success is False
    assert "LLM error" in result.error


def test_convert_meeting_notes_to_tasks_persists_all_actions(test_db):
    """convert_meeting_notes_to_tasks actually creates one Task row per action item."""
    actions = [
        {"title": "Action One", "description": "d1", "priority": "high", "owner_hint": None},
        {"title": "Action Two", "description": "d2", "priority": "low", "owner_hint": "Ali"},
    ]
    result = convert_meeting_notes_to_tasks({"actions": actions, "source": "meeting_notes"}, "tc-convert")

    assert result.success is True
    assert result.output["count"] == 2
    created_titles = {t["title"] for t in result.output["created_tasks"]}
    assert created_titles == {"Action One", "Action Two"}
    assert all(t["source"] == "meeting_notes" for t in result.output["created_tasks"])
