from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_liveness_endpoint():
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_endpoint_when_dependencies_are_available(monkeypatch):
    import app.main as main

    connection = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection

    redis_client = MagicMock()
    redis_client.ping.return_value = True

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(
        main.redis.Redis,
        "from_url",
        lambda *args, **kwargs: redis_client,
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": "ok",
            "redis": "ok",
        },
    }


def test_readiness_returns_503_when_redis_is_unavailable(monkeypatch):
    import app.main as main

    connection = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection

    redis_client = MagicMock()
    redis_client.ping.side_effect = ConnectionError("Redis unavailable")

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(
        main.redis.Redis,
        "from_url",
        lambda *args, **kwargs: redis_client,
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "status": "not_ready",
        "checks": {
            "database": "ok",
            "redis": "unavailable",
        },
    }
