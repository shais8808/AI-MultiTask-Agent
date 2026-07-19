"""
tests/test_tasks.py
=====================
Covers: Task Creation, Listing, Update, Invalid Task (REQUIRED test categories).
"""


def test_create_task_success(client):
    """A valid task creation request returns 201 with the created task."""
    resp = client.post("/api/tasks", json={"title": "Write report", "priority": "high"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Write report"
    assert data["priority"] == "high"
    assert data["status"] == "pending"
    assert data["id"]


def test_create_task_invalid_blank_title(client):
    """A blank title is rejected by Pydantic validation with a 422."""
    resp = client.post("/api/tasks", json={"title": "   "})
    assert resp.status_code == 422


def test_list_tasks_returns_created_task(client):
    """Listing tasks reflects tasks previously created."""
    client.post("/api/tasks", json={"title": "Task A"})
    client.post("/api/tasks", json={"title": "Task B"})
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    titles = {t["title"] for t in data["tasks"]}
    assert titles == {"Task A", "Task B"}


def test_list_tasks_filter_by_priority(client):
    """Filtering tasks by priority only returns matching tasks."""
    client.post("/api/tasks", json={"title": "Low prio", "priority": "low"})
    client.post("/api/tasks", json={"title": "High prio", "priority": "high"})
    resp = client.get("/api/tasks", params={"priority": "high"})
    data = resp.json()
    assert data["count"] == 1
    assert data["tasks"][0]["title"] == "High prio"


def test_update_task_success(client):
    """Updating an existing task changes only the supplied fields."""
    created = client.post("/api/tasks", json={"title": "Original", "priority": "low"}).json()
    resp = client.put(f"/api/tasks/{created['id']}", json={"task_id": created["id"], "status": "in_progress"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "in_progress"
    assert data["title"] == "Original"  # unchanged
    assert data["priority"] == "low"  # unchanged


def test_update_task_invalid_id_returns_404(client):
    """Updating a non-existent task_id returns a 404 with a clear error."""
    resp = client.put("/api/tasks/does-not-exist", json={"task_id": "does-not-exist", "status": "completed"})
    assert resp.status_code == 404


def test_complete_task_success(client):
    """Completing a task sets its status to completed."""
    created = client.post("/api/tasks", json={"title": "Finish this"}).json()
    resp = client.post(f"/api/tasks/{created['id']}/complete")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_complete_task_invalid_id_returns_404(client):
    """Completing an unknown task_id returns a 404, not a 500."""
    resp = client.post("/api/tasks/nonexistent-id/complete")
    assert resp.status_code == 404
