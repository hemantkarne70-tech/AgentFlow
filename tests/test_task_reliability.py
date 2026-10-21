import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.tasks import execute_workflow_task


def test_workflow_step_failure_is_saved(monkeypatch):
    """A tool error should mark the workflow failed and preserve its history."""
    run = SimpleNamespace(
        id=123,
        status="queued",
        error=None,
        results=[],
        total_steps=0,
    )

    db = MagicMock()
    db.get.return_value = run

    monkeypatch.setattr(
        "app.tasks.SessionLocal",
        lambda: db,
    )

    result = execute_workflow_task.run(
        123,
        [
            {
                "tool": "calculator",
                "input": "1 / 0",
            }
        ],
    )

    assert result["status"] == "failed"
    assert run.status == "failed"
    assert "Step 1 failed" in run.error
    assert run.total_steps == 0
    db.commit.assert_called()
    db.close.assert_called_once()


def test_celery_task_has_reliability_settings():
    """Verify bounded retries and worker-loss handling are enabled."""
    assert execute_workflow_task.max_retries == 3
    assert execute_workflow_task.acks_late is True
    assert execute_workflow_task.reject_on_worker_lost is True


def test_database_error_triggers_automatic_retry(monkeypatch):
    """A temporary database error should trigger Celery's retry mechanism."""

    db = MagicMock()
    db.get.side_effect = OperationalError(
        "SELECT * FROM workflow_runs",
        {},
        Exception("Temporary database connection failure"),
    )

    monkeypatch.setattr("app.tasks.SessionLocal", lambda: db)

    # Prevent the test from publishing a real retry message to Redis.
    retry_signal = Retry("Retry requested")
    monkeypatch.setattr(
        execute_workflow_task,
        "retry",
        MagicMock(side_effect=retry_signal),
    )

    with pytest.raises(Retry, match="Retry requested"):
        execute_workflow_task.run(
            123,
            [{"tool": "uppercase", "input": "hello"}],
        )

    execute_workflow_task.retry.assert_called_once()
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_partial_results_are_preserved_when_later_step_fails(monkeypatch):
    """Keep successful step results when a later step fails."""

    run = SimpleNamespace(
        id=124,
        status="queued",
        error=None,
        results=[],
        total_steps=0,
    )

    db = MagicMock()
    db.get.return_value = run
    monkeypatch.setattr("app.tasks.SessionLocal", lambda: db)

    execute_tool_mock = MagicMock(
        side_effect=["HELLO", ValueError("Simulated step failure")]
    )
    monkeypatch.setattr("app.tasks.execute_tool", execute_tool_mock)

    result = execute_workflow_task.run(
        124,
        [
            {"tool": "uppercase", "input": "hello"},
            {"tool": "calculator", "input": "invalid"},
        ],
    )

    assert result["status"] == "failed"
    assert run.status == "failed"
    assert "Step 2 failed" in run.error
    assert run.total_steps == 1
    assert run.results == [
        {"step": 1, "tool": "uppercase", "result": "HELLO"}
    ]

    db.commit.assert_called()
    db.close.assert_called_once()