from fastapi.testclient import TestClient

from app.main import app
from app.metrics import reset_metrics

client = TestClient(app)


def test_metrics_endpoint_returns_prometheus_text_format():
    reset_metrics()
    response = client.get("/health/live")
    assert response.status_code == 200

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers["content-type"]
    assert "# TYPE agentflow_http_requests_total counter" in metrics.text
    assert 'route="/health/live"' in metrics.text
    assert "agentflow_http_request_duration_seconds_count" in metrics.text


def test_metrics_endpoint_does_not_count_its_own_scrapes():
    reset_metrics()
    first = client.get("/health/live")
    assert first.status_code == 200

    metrics_before = client.get("/metrics").text
    metrics_after = client.get("/metrics").text
    assert metrics_before == metrics_after


def test_route_labels_use_route_templates_not_run_ids():
    reset_metrics()
    # A missing run is sufficient to exercise a parameterized route template.
    response = client.get("/workflows/runs/987654321")
    assert response.status_code == 404

    metrics = client.get("/metrics").text
    assert 'route="/workflows/runs/{run_id}"' in metrics
