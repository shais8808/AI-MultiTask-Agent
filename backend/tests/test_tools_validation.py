"""
tests/test_tools_validation.py
================================
Covers: Validation, Persistence (REQUIRED test categories).

These tests exercise the tool functions directly (bypassing the API
layer) to confirm the tools' own Pydantic validation and repository
persistence work correctly in isolation.
"""

from app.tools.task_tools import complete_task, create_task, list_tasks, update_task


def test_create_task_tool_validates_arguments(test_db):
    """create_task returns success=False (not an exception) for invalid arguments."""
    result = create_task({"title": ""}, "tc-1")
    assert result.success is False
    assert "Invalid arguments" in result.error


def test_create_task_tool_persists_to_database(test_db):
    """A tool-created task is retrievable via a separate list_tasks call — proves persistence."""
    create_result = create_task({"title": "Persisted task", "priority": "critical"}, "tc-2")
    assert create_result.success is True

    list_result = list_tasks({}, "tc-3")
    assert list_result.success is True
    assert list_result.output["count"] == 1
    assert list_result.output["tasks"][0]["title"] == "Persisted task"
    assert list_result.output["tasks"][0]["priority"] == "critical"


def test_update_task_tool_rejects_unknown_field_types(test_db):
    """Passing an invalid priority value fails Pydantic validation, not the DB layer."""
    created = create_task({"title": "Some task"}, "tc-4")
    task_id = created.output["task"]["id"]

    result = update_task({"task_id": task_id, "priority": "not-a-real-priority"}, "tc-5")
    assert result.success is False
    assert "Invalid arguments" in result.error


def test_update_task_tool_missing_task_id_fails_validation(test_db):
    """task_id is required by TaskUpdate; omitting it must fail validation, not crash."""
    result = update_task({"status": "completed"}, "tc-6")
    assert result.success is False


def test_complete_task_tool_unknown_id_returns_clean_error(test_db):
    """complete_task on a nonexistent ID returns a ToolResult failure, never an unhandled exception."""
    result = complete_task({"task_id": "totally-fake-id"}, "tc-7")
    assert result.success is False
    assert "not found" in result.error.lower()
