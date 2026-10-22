"""Small Prometheus-compatible HTTP metrics collector for AgentFlow.

Metrics are kept in process memory. For multi-worker or multi-container deployments,
Prometheus will see each process's metrics separately unless the deployment is
configured to scrape each instance independently.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import DefaultDict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_LOCK = threading.Lock()
_REQUESTS: DefaultDict[tuple[str, str, int], int] = defaultdict(int)
_DURATION_COUNT: DefaultDict[tuple[str, str], int] = defaultdict(int)
_DURATION_SUM: DefaultDict[tuple[str, str], float] = defaultdict(float)
_DURATION_BUCKETS: DefaultDict[tuple[str, str, str], int] = defaultdict(int)


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def record_request(method: str, route: str, status_code: int, duration: float) -> None:
    """Record a completed HTTP request using low-cardinality route templates."""
    key = (method, route, status_code)
    duration_key = (method, route)
    with _LOCK:
        _REQUESTS[key] += 1
        _DURATION_COUNT[duration_key] += 1
        _DURATION_SUM[duration_key] += duration
        for bucket in _BUCKETS:
            if duration <= bucket:
                _DURATION_BUCKETS[(method, route, str(bucket))] += 1


def reset_metrics() -> None:
    """Reset process metrics; intended for isolated tests only."""
    with _LOCK:
        _REQUESTS.clear()
        _DURATION_COUNT.clear()
        _DURATION_SUM.clear()
        _DURATION_BUCKETS.clear()


def render_metrics() -> str:
    """Render metrics in Prometheus text exposition format."""
    with _LOCK:
        requests = dict(_REQUESTS)
        duration_count = dict(_DURATION_COUNT)
        duration_sum = dict(_DURATION_SUM)
        duration_buckets = dict(_DURATION_BUCKETS)

    lines = [
        "# HELP agentflow_http_requests_total Total HTTP requests handled by AgentFlow.",
        "# TYPE agentflow_http_requests_total counter",
    ]
    for (method, route, status), count in sorted(requests.items()):
        lines.append(
            f'agentflow_http_requests_total{{method="{_label(method)}",route="{_label(route)}",status="{status}"}} {count}'
        )

    lines.extend([
        "# HELP agentflow_http_request_duration_seconds HTTP request duration in seconds.",
        "# TYPE agentflow_http_request_duration_seconds histogram",
    ])
    for method, route in sorted(duration_count):
        labels = f'method="{_label(method)}",route="{_label(route)}"'
        for bucket in _BUCKETS:
            bucket_count = duration_buckets.get((method, route, str(bucket)), 0)
            lines.append(
                f'agentflow_http_request_duration_seconds_bucket{{{labels},le="{bucket:g}"}} {bucket_count}'
            )
        lines.append(
            f'agentflow_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {duration_count[(method, route)]}'
        )
        lines.append(f'agentflow_http_request_duration_seconds_count{{{labels}}} {duration_count[(method, route)]}')
        lines.append(f'agentflow_http_request_duration_seconds_sum{{{labels}}} {duration_sum[(method, route)]:.9f}')

    return "\n".join(lines) + "\n"


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Record request totals and latency without high-cardinality raw URLs."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed = max(0.0, time.perf_counter() - started)
            route_obj = request.scope.get("route")
            route = getattr(route_obj, "path", None) or request.url.path
            record_request(request.method, route, status_code, elapsed)
