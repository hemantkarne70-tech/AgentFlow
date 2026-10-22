from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _create_completed_run(name="Operations timeline test"):
    response = client.post(
        "/workflows/execute",
        json={"name": name, "steps": [{"tool": "uppercase", "input": "hello"}]},
    )
    assert response.status_code == 200
    return response.json()["run_id"]


def test_operations_summary_includes_status_counts_and_durations():
    run_id = _create_completed_run()
    response = client.get("/workflows/operations/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["total_runs"] >= 1
    assert set(data["status_counts"]) >= {
        "queued", "running", "completed", "failed", "cancelled"
    }
    assert data["status_counts"]["completed"] >= 1
    assert data["average_duration_seconds"] is None or data["average_duration_seconds"] >= 0
    assert isinstance(data["recent_failures"], list)


def test_workflow_timeline_is_chronological_and_contains_step_diagnostics():
    run_id = _create_completed_run("Timeline chronological test")
    response = client.get(f"/workflows/runs/{run_id}/timeline")

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] == run_id
    assert data["status"] == "completed"
    assert [event["event_type"] for event in data["events"]] == [
        "created", "started", "completed"
    ]
    elapsed = [event["elapsed_seconds"] for event in data["events"]]
    assert elapsed == sorted(elapsed)
    assert len(data["step_diagnostics"]) == 1
    assert data["step_diagnostics"][0]["tool"] == "uppercase"


def test_workflow_timeline_returns_404_for_unknown_run():
    response = client.get("/workflows/runs/999999999/timeline")
    assert response.status_code == 404
