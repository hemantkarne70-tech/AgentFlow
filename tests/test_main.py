
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_endpoint():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["application"] == "AgentFlow"


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_execute_workflow():
    payload = {
        "name": "Test Workflow",
        "steps": [
            {"tool": "calculator", "input": "10 + 5"},
            {"tool": "uppercase", "input": "hello"},
            {"tool": "word_count", "input": "hello world"},
        ],
    }

    response = client.post("/workflows/execute", json=payload)

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "completed"
    assert data["total_steps"] == 3
    assert data["results"][0]["result"] == 15
    assert data["results"][1]["result"] == "HELLO"
    assert data["results"][2]["result"]["words"] == 2


def test_invalid_calculation_returns_error():
    payload = {
        "name": "Invalid Calculation",
        "steps": [
            {"tool": "calculator", "input": "1 / 0"}
        ],
    }

    response = client.post("/workflows/execute", json=payload)

    assert response.status_code == 400


def test_unknown_tool_is_rejected():
    payload = {
        "name": "Invalid Tool",
        "steps": [
            {"tool": "delete_everything", "input": "test"}
        ],
    }

    response = client.post("/workflows/execute", json=payload)

    assert response.status_code == 422
    
def test_workflow_run_is_saved():
    payload = {
        "name": "Persistent Workflow",
        "steps": [
            {"tool": "calculator", "input": "8 * 8"}
        ],
    }

    execution = client.post("/workflows/execute", json=payload)

    assert execution.status_code == 200
    run_id = execution.json()["run_id"]

    history = client.get(f"/workflows/runs/{run_id}")

    assert history.status_code == 200
    assert history.json()["workflow"] == "Persistent Workflow"
    assert history.json()["results"][0]["result"] == 64


def test_failed_workflow_is_saved():
    payload = {
        "name": "Failed Workflow",
        "steps": [
            {"tool": "calculator", "input": "1 / 0"}
        ],
    }

    execution = client.post("/workflows/execute", json=payload)

    assert execution.status_code == 400
    run_id = execution.json()["detail"]["run_id"]

    history = client.get(f"/workflows/runs/{run_id}")

    assert history.status_code == 200
    assert history.json()["status"] == "failed"
    assert "Step 1 failed" in history.json()["error"]
