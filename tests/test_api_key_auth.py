
from fastapi.testclient import TestClient

from app.main import app


def test_api_key_disabled_when_environment_key_is_unset(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200


def test_health_endpoints_remain_public_when_api_key_is_enabled(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_API_KEY", "test-secret-key")
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200


def test_protected_endpoint_rejects_missing_key(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_API_KEY", "test-secret-key")
    response = TestClient(app).get("/workflows/runs")
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid API key."


def test_protected_endpoint_rejects_wrong_key(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_API_KEY", "test-secret-key")
    response = TestClient(app).get(
        "/workflows/runs", headers={"X-API-Key": "wrong-key"}
    )
    assert response.status_code == 401


def test_valid_api_key_allows_protected_endpoint(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_API_KEY", "test-secret-key")
    response = TestClient(app).get(
        "/workflows/runs", headers={"X-API-Key": "test-secret-key"}
    )
    assert response.status_code == 200
