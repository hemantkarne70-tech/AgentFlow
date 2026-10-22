from fastapi.testclient import TestClient

from app import main


class FakeRedis:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def eval(self, *args):
        self.calls.append(args)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_redis_limiter_is_used_and_headers_are_returned(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    monkeypatch.setattr(main, "_RATE_LIMIT_REDIS", FakeRedis([[1, 119]]))
    with TestClient(main.app) as client:
        response = client.get("/workflows/runs")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "120"
    assert response.headers["X-RateLimit-Remaining"] == "119"
    assert len(main._RATE_LIMIT_REDIS.calls) == 1


def test_redis_limiter_returns_429_and_retry_after(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    monkeypatch.setattr(main, "_RATE_LIMIT_REDIS", FakeRedis([[0, 1_900_000_000_000]]))
    with TestClient(main.app) as client:
        response = client.get("/workflows/runs")
    assert response.status_code == 429
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert int(response.headers["Retry-After"]) >= 1


def test_redis_failure_uses_local_fallback(monkeypatch):
    monkeypatch.delenv("AGENTFLOW_API_KEY", raising=False)
    fake = FakeRedis([
        main.redis.ConnectionError("redis unavailable"),
        main.redis.ConnectionError("redis unavailable"),
    ])
    monkeypatch.setattr(main, "_RATE_LIMIT_REDIS", fake)
    monkeypatch.setenv("AGENTFLOW_RATE_LIMIT", "1")
    monkeypatch.setenv("AGENTFLOW_RATE_WINDOW_SECONDS", "60")
    main._RATE_LIMIT_BUCKETS.clear()
    with TestClient(main.app) as client:
        first = client.get("/workflows/runs")
        second = client.get("/workflows/runs")
    assert first.status_code == 200
    assert second.status_code == 429


def test_redis_key_does_not_contain_raw_api_key(monkeypatch):
    monkeypatch.setenv("AGENTFLOW_API_KEY", "test-secret-key")
    fake = FakeRedis([[1, 119]])
    monkeypatch.setattr(main, "_RATE_LIMIT_REDIS", fake)
    with TestClient(main.app) as client:
        response = client.get("/workflows/runs", headers={"X-API-Key": "test-secret-key"})
    assert response.status_code == 200
    redis_key = fake.calls[0][2]
    assert "test-secret-key" not in redis_key
    assert redis_key.startswith("agentflow:rate-limit:")
