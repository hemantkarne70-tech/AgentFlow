from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _completed_run(name=None):
    response = client.post(
        "/workflows/execute",
        json={
            "name": name or f"Dashboard filter {uuid4().hex[:8]}",
            "steps": [{"tool": "uppercase", "input": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_dashboard_returns_run_and_schedule_summary():
    _completed_run()
    response = client.get("/workflows/dashboard?recent_limit=5")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_runs"] >= 1
    assert data["status_counts"]["completed"] >= 1
    assert set(data["schedules"]) == {"total", "enabled", "paused"}
    assert isinstance(data["recent_runs"], list)
    assert len(data["recent_runs"]) <= 5
    assert isinstance(data["recent_failures"], list)


def test_run_list_filters_by_name_and_outcome():
    name = f"Dashboard searchable {uuid4().hex[:8]}"
    created = _completed_run(name)
    response = client.get(
        "/workflows/runs",
        params={"search": name, "outcome": "success", "page": 1, "per_page": 10},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert any(item["id"] == created["run_id"] for item in data["items"])
    assert data["filters"]["outcome"] == "success"


def test_run_list_supports_date_bounds():
    created = _completed_run(f"Date range {uuid4().hex[:8]}")
    history = client.get(f"/workflows/runs/{created['run_id']}")
    assert history.status_code == 200, history.text
    created_at = history.json()["created_at"]

    matching = client.get(
        "/workflows/runs",
        params={"date_from": created_at, "date_to": created_at, "search": history.json()["workflow"]},
    )
    assert matching.status_code == 200, matching.text
    assert any(item["id"] == created["run_id"] for item in matching.json()["items"])


def test_run_list_rejects_invalid_date_range_and_outcome():
    invalid_range = client.get(
        "/workflows/runs",
        params={"date_from": "2026-10-22T12:00:00+00:00", "date_to": "2026-10-21T12:00:00+00:00"},
    )
    assert invalid_range.status_code == 422

    invalid_outcome = client.get("/workflows/runs?outcome=unknown")
    assert invalid_outcome.status_code == 422


def test_cancelled_status_can_be_filtered():
    response = client.get("/workflows/runs?status=cancelled")
    assert response.status_code == 200, response.text
    assert response.json()["filters"]["status"] == "cancelled"
