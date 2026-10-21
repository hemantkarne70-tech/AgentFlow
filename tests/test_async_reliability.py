from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.main import app
from app.tasks import execute_workflow_task


def make_run(status="queued"):
    return SimpleNamespace(
        id=123,
        name="Reliability Test",
        status=status,
        total_steps=0,
        results=[],
        error=None,
        started_at=None,
        finished_at=None,
        duration_seconds=None,
    )


def setup_worker_db(monkeypatch, run):
    db = MagicMock()
    db.get.return_value = run
    monkeypatch.setattr("app.tasks.SessionLocal", lambda: db)
    return db


def test_async_worker_completes_with_timing(monkeypatch):
    run = make_run()
    setup_worker_db(monkeypatch, run)
    monkeypatch.setattr(
        "app.tasks.execute_tool",
        lambda tool, value: value.upper(),
    )

    result = execute_workflow_task.run(
        123,
        [{"tool": "uppercase", "input": "hello"}],
    )

    assert result["status"] == "completed"
    assert run.status == "completed"
    assert run.total_steps == 1
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.duration_seconds is not None
    assert run.duration_seconds >= 0


def test_async_worker_records_tool_failure_and_timing(monkeypatch):
    run = make_run()
    setup_worker_db(monkeypatch, run)

    def fail_tool(tool, value):
        raise ValueError("Invalid tool input")

    monkeypatch.setattr("app.tasks.execute_tool", fail_tool)

    result = execute_workflow_task.run(
        123,
        [{"tool": "calculator", "input": "invalid"}],
    )

    assert result["status"] == "failed"
    assert run.status == "failed"
    assert "Step 1 failed" in run.error
    assert run.finished_at is not None
    assert run.duration_seconds is not None


def test_async_worker_records_unexpected_error(monkeypatch):
    run = make_run()
    setup_worker_db(monkeypatch, run)

    def unexpected_error(tool, value):
        raise RuntimeError("Unexpected test failure")

    monkeypatch.setattr("app.tasks.execute_tool", unexpected_error)

    result = execute_workflow_task.run(
        123,
        [{"tool": "uppercase", "input": "hello"}],
    )

    assert result["status"] == "failed"
    assert run.status == "failed"
    assert "Unexpected worker error" in run.error
    assert "Unexpected test failure" in run.error
    assert run.finished_at is not None
    assert run.duration_seconds is not None


def test_async_worker_handles_missing_run(monkeypatch):
    setup_worker_db(monkeypatch, None)

    result = execute_workflow_task.run(
        999999,
        [{"tool": "uppercase", "input": "hello"}],
    )

    assert result["status"] == "not_found"


def test_async_worker_does_not_repeat_completed_run(monkeypatch):
    run = make_run(status="completed")
    setup_worker_db(monkeypatch, run)

    def should_not_execute(tool, value):
        raise AssertionError("A completed workflow must not run again")

    monkeypatch.setattr("app.tasks.execute_tool", should_not_execute)

    result = execute_workflow_task.run(
        123,
        [{"tool": "uppercase", "input": "hello"}],
    )

    assert result["status"] == "completed"
    assert "already in a terminal state" in result["message"]


def test_enqueue_failure_records_terminal_timing(monkeypatch):
    client = TestClient(app)

    def fail_enqueue(*args, **kwargs):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(
        "app.main.execute_workflow_task.delay",
        fail_enqueue,
    )

    response = client.post(
        "/workflows/execute-async",
        json={
            "name": "Enqueue Failure Test",
            "steps": [{"tool": "uppercase", "input": "hello"}],
        },
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["status"] == "failed"
    assert detail["run_id"] is not None
    assert detail["started_at"] is not None
    assert detail["finished_at"] is not None
    assert detail["duration_seconds"] is not None
    assert detail["duration_seconds"] >= 0