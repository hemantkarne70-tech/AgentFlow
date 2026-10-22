from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import WorkflowRun

client = TestClient(app)


def _create_run(status: str) -> int:
    db = SessionLocal()
    try:
        run = WorkflowRun(
            name=f"Bulk cancel {status} {uuid4().hex[:8]}",
            status=status,
            total_steps=1,
            results=[],
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc) if status == "running" else None,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        db.close()


def _status(run_id: int) -> str:
    db = SessionLocal()
    try:
        return db.get(WorkflowRun, run_id).status
    finally:
        db.close()


def test_bulk_cancel_dry_run_does_not_mutate_runs():
    queued_id = _create_run("queued")
    running_id = _create_run("running")
    response = client.post(
        "/workflows/runs/bulk-cancel",
        json={"run_ids": [queued_id, running_id], "dry_run": True},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["dry_run"] is True
    assert data["counts"] == {
        "would_cancel": 1,
        "would_request_cancellation": 1,
    }
    assert _status(queued_id) == "queued"
    assert _status(running_id) == "running"


def test_bulk_cancel_handles_queued_running_terminal_and_missing_runs():
    queued_id = _create_run("queued")
    running_id = _create_run("running")
    completed_id = _create_run("completed")
    missing_id = max(queued_id, running_id, completed_id) + 100000

    response = client.post(
        "/workflows/runs/bulk-cancel",
        json={"run_ids": [queued_id, running_id, completed_id, missing_id]},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requested"] == 4
    assert data["counts"]["cancelled"] == 1
    assert data["counts"]["cancellation_requested"] == 1
    assert data["counts"]["already_terminal"] == 1
    assert data["counts"]["not_found"] == 1
    assert _status(queued_id) == "cancelled"
    assert _status(running_id) == "running"
    assert _status(completed_id) == "completed"


def test_bulk_cancel_rejects_duplicate_or_invalid_ids():
    duplicate = client.post(
        "/workflows/runs/bulk-cancel",
        json={"run_ids": [1, 1]},
    )
    invalid = client.post(
        "/workflows/runs/bulk-cancel",
        json={"run_ids": [0]},
    )

    assert duplicate.status_code == 422
    assert invalid.status_code == 422
