
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
