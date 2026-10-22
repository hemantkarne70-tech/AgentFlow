from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_sync_workflow_writes_lifecycle_audit_events():
    response = client.post(
        "/workflows/execute",
        json={"name": "Audit Success", "steps": [{"tool": "uppercase", "input": "hello"}]},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    history = client.get("/workflows/audit", params={"run_id": run_id, "per_page": 10})
    assert history.status_code == 200
    payload = history.json()
    event_types = {item["event_type"] for item in payload["items"]}
    assert {"created", "started", "completed"} <= event_types
    assert all(item["run_id"] == run_id for item in payload["items"])
    assert payload["pagination"]["total_items"] >= 3


def test_audit_endpoint_filters_event_type_and_paginates():
    response = client.post(
        "/workflows/execute",
        json={"name": "Audit Filter", "steps": [{"tool": "uppercase", "input": "hello"}]},
    )
    run_id = response.json()["run_id"]

    filtered = client.get(
        "/workflows/audit",
        params={"run_id": run_id, "event_type": "completed", "page": 1, "per_page": 1},
    )
    assert filtered.status_code == 200
    data = filtered.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["event_type"] == "completed"
    assert data["pagination"]["total_items"] == 1


def test_audit_endpoint_rejects_unknown_event_type():
    response = client.get("/workflows/audit", params={"event_type": "deleted"})
    assert response.status_code == 422
