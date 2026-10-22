from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.audit import record_audit_event
from app.db import SessionLocal
from app.main import app
from app.models import WorkflowRun

client = TestClient(app)


def _failed_run():
    db = SessionLocal()
    try:
        run = WorkflowRun(
            name=f"Retry test {uuid4().hex[:8]}",
            status="failed",
            error="Simulated original failure",
            results=[],
            total_steps=0,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            finished_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        )
        db.add(run)
        db.flush()
        record_audit_event(db, run.id, "created", "Test run created.")
        record_audit_event(db, run.id, "failed", "Simulated failure.")
        db.commit()
        return run.id
    finally:
        db.close()


def test_stuck_endpoint_lists_old_queued_and_running_workflows():
    db = SessionLocal()
    try:
        queued = WorkflowRun(
            name=f"Stuck queued {uuid4().hex[:8]}",
            status="queued",
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            results=[],
        )
        running = WorkflowRun(
            name=f"Stuck running {uuid4().hex[:8]}",
            status="running",
            created_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            results=[],
        )
        db.add_all([queued, running])
        db.commit()
        ids = {queued.id, running.id}
    finally:
        db.close()

    response = client.get("/workflows/operations/stuck?threshold_seconds=600")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 2
    assert ids.issubset({item["run_id"] for item in data["workflows"]})


def test_retry_failed_workflow_creates_new_run_and_preserves_original(monkeypatch):
    source_id = _failed_run()
    calls = []
    monkeypatch.setattr(
        "app.main.execute_workflow_task.delay",
        lambda run_id, steps: (calls.append((run_id, steps)) or SimpleNamespace(id="mock-task-1")),
    )
    body = {
        "name": "Retry attempt",
        "steps": [{"tool": "uppercase", "input": "hello"}],
    }
    key = f"retry-{uuid4().hex}"

    first = client.post(
        f"/workflows/runs/{source_id}/retry",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202, first.text
    first_data = first.json()
    assert first_data["source_run_id"] == source_id
    assert first_data["status"] == "queued"
    assert first_data["duplicate_request"] is False

    second = client.post(
        f"/workflows/runs/{source_id}/retry",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 202, second.text
    assert second.json()["run_id"] == first_data["run_id"]
    assert second.json()["duplicate_request"] is True
    assert len(calls) == 1

    db = SessionLocal()
    try:
        original = db.get(WorkflowRun, source_id)
        retry_run = db.get(WorkflowRun, first_data["run_id"])
        assert original.status == "failed"
        assert retry_run.status == "queued"
    finally:
        db.close()


def test_retry_rejects_non_failed_workflow():
    db = SessionLocal()
    try:
        run = WorkflowRun(
            name=f"Queued retry test {uuid4().hex[:8]}",
            status="queued",
            results=[],
        )
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    response = client.post(
        f"/workflows/runs/{run_id}/retry",
        json={"name": "Should not retry", "steps": [{"tool": "uppercase", "input": "hello"}]},
        headers={"Idempotency-Key": f"retry-{uuid4().hex}"},
    )
    assert response.status_code == 409


def test_retry_returns_404_for_unknown_run():
    response = client.post(
        "/workflows/runs/999999999/retry",
        json={"name": "Unknown", "steps": [{"tool": "uppercase", "input": "hello"}]},
        headers={"Idempotency-Key": f"retry-{uuid4().hex}"},
    )
    assert response.status_code == 404
