import os

from fastapi.testclient import TestClient

from app import main

def force_local_rate_limiter(*args, **kwargs):
    raise main.redis.ConnectionError(
        "Force local fallback for legacy tests"
    )

def reset_buckets():
    with main._RATE_LIMIT_LOCK:
        main._RATE_LIMIT_BUCKETS.clear()


def test_requests_include_rate_limit_headers(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    monkeypatch.setenv("AGENTFLOW_RATE_LIMIT", "5")
    monkeypatch.setenv("AGENTFLOW_RATE_WINDOW_SECONDS", "60")
    monkeypatch.setattr(main, "_redis_rate_limit", force_local_rate_limiter)
    reset_buckets()
    with TestClient(main.app) as client:
        response = client.get("/workflows/runs")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "5"
    assert response.headers["X-RateLimit-Remaining"] == "4"


def test_rate_limit_returns_429_after_limit(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    monkeypatch.setenv("AGENTFLOW_RATE_LIMIT", "2")
    monkeypatch.setenv("AGENTFLOW_RATE_WINDOW_SECONDS", "60")
    monkeypatch.setattr(main, "_redis_rate_limit", force_local_rate_limiter)
    reset_buckets()
    with TestClient(main.app) as client:
        first = client.get("/workflows/runs")
        second = client.get("/workflows/runs")
        third = client.get("/workflows/runs")
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["detail"] == "Rate limit exceeded. Please retry later."
    assert int(third.headers["Retry-After"]) >= 1
    assert third.headers["X-RateLimit-Remaining"] == "0"


def test_health_endpoint_is_exempt_from_rate_limit(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    monkeypatch.setenv("AGENTFLOW_RATE_LIMIT", "1")
    reset_buckets()
    with TestClient(main.app) as client:
        first = client.get("/health")
        second = client.get("/health")
    assert first.status_code == 200
    assert second.status_code == 200


def test_api_key_is_checked_before_rate_limit(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_API_KEY", "test-only-secret")
    monkeypatch.setenv("AGENTFLOW_RATE_LIMIT", "1")
    reset_buckets()
    with TestClient(main.app) as client:
        response = client.get("/workflows/runs")
    assert response.status_code == 401


def test_invalid_rate_limit_settings_use_safe_defaults(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_RATE_LIMIT", "not-a-number")
    monkeypatch.setenv("AGENTFLOW_RATE_WINDOW_SECONDS", "0")
    assert main._rate_limit_settings() == (120, 1)
