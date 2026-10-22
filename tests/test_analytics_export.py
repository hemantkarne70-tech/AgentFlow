from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_workflow_analytics_returns_dashboard_metrics():
    response = client.get("/workflows/analytics")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["total_runs"], int)
    assert isinstance(data["status_counts"], dict)
    assert 0 <= data["success_rate_percent"] <= 100
    assert isinstance(data["total_steps"], int)
    assert "average_duration_seconds" in data
    assert "latest_run" in data


def test_workflow_run_csv_export_has_download_headers_and_csv_columns():
    unique_name = "Milestone21 CSV Export Check"
    execution = client.post(
        "/workflows/execute",
        json={"name": unique_name, "steps": [{"tool": "uppercase", "input": "hello"}]},
    )
    assert execution.status_code == 200

    response = client.get("/workflows/runs/export", params={"search": unique_name})

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment;" in response.headers["content-disposition"]
    assert "workflow" in response.text
    assert unique_name in response.text
    assert "total_steps" in response.text


def test_workflow_run_csv_export_can_filter_by_status():
    response = client.get("/workflows/runs/export", params={"status": "completed"})

    assert response.status_code == 200
    lines = response.text.lstrip("\ufeff").splitlines()
    assert lines[0].startswith("id,workflow,status,")
    assert all(",completed," in line for line in lines[1:])


def test_workflow_run_csv_export_rejects_invalid_status():
    response = client.get("/workflows/runs/export", params={"status": "unknown"})

    assert response.status_code == 422
