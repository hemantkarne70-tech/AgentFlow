import logging
import re

from fastapi.testclient import TestClient

from app import main


def test_response_has_generated_request_id(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    with TestClient(main.app) as client:
        response = client.get("/health")
    request_id = response.headers.get("X-Request-ID", "")
    assert response.status_code == 200
    assert re.fullmatch(r"[a-f0-9]{32}", request_id)


def test_valid_client_request_id_is_preserved(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    with TestClient(main.app) as client:
        response = client.get("/health", headers={"X-Request-ID": "trace-test_123.abc"})
    assert response.headers["X-Request-ID"] == "trace-test_123.abc"


def test_invalid_client_request_id_is_replaced(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    with TestClient(main.app) as client:
        response = client.get("/health", headers={"X-Request-ID": "bad id"})
    request_id = response.headers["X-Request-ID"]
    assert response.status_code == 200
    assert re.fullmatch(r"[a-f0-9]{32}", request_id)


def test_request_id_is_added_to_auth_failures(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_API_KEY", "test-only-secret")
    with TestClient(main.app) as client:
        response = client.get("/workflows/runs")
    assert response.status_code == 401
    assert response.headers.get("X-Request-ID")


def test_request_completion_is_logged(monkeypatch, caplog):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    with caplog.at_level(logging.INFO, logger=main.__name__):
        with TestClient(main.app) as client:
            response = client.get("/health")
    assert response.status_code == 200
    record = next(r for r in caplog.records if r.getMessage() == "http_request_completed")
    assert record.http_method == "GET"
    assert record.http_path == "/health"
    assert record.http_status_code == 200
    assert record.request_id == response.headers["X-Request-ID"]
    assert record.duration_ms >= 0
