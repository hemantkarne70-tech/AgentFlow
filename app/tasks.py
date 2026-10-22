import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from celery import Task
from sqlalchemy import and_, update
from sqlalchemy.exc import InterfaceError, OperationalError

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.models import WorkflowRun, WorkflowStepTrace, WorkflowSchedule
from app.audit import record_audit_event
from app.tools import execute_tool

from app.workflow_runtime import (
    execute_workflow_steps,
    WorkflowStepExecutionError,
    WorkflowCancelledError,
)

configure_logging()
logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def utc_now():
    return datetime.now(timezone.utc)


def finish_run(run, status: str, error: str | None = None) -> None:
    """Apply a terminal state and record execution timing."""
    finished_at = utc_now()
    started_at = getattr(run, "started_at", None)

    if started_at is None:
        started_at = finished_at

    # SQLite may return a naive datetime despite timezone=True.
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    run.started_at = started_at
    run.status = status
    run.error = error
    run.finished_at = finished_at
    run.duration_seconds = round(
        max(0.0, (finished_at - started_at).total_seconds()),
        3,
    )


class ReliableWorkflowTask(Task):
    """Record final worker failures when the database is available."""

    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        run_id = args[0] if args else kwargs.get("run_id")
        if run_id is None:
            logger.error(
                "Celery task %s failed without a workflow run ID: %s",
                task_id,
                exc,
            )
            return

        db = SessionLocal()
        try:
            run = db.get(WorkflowRun, run_id)

            # Never overwrite a terminal result if Celery reports a later
            # delivery/failure for a workflow that has already finished.
            if run is not None and run.status in ACTIVE_STATUSES:
                finish_run(
                    run,
                    "failed",
                    f"Background task failed: {type(exc).__name__}: {exc}",
                )
                record_audit_event(
                    db, run.id, "failed", "Workflow failed in the background worker.",
                    details={"error": f"{type(exc).__name__}: {exc}"},
                )
                db.commit()
                logger.exception(
                    "Workflow %s failed in Celery task %s",
                    run_id,
                    task_id,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
        except Exception:
            db.rollback()
            logger.exception(
                "Could not record final failure for workflow %s",
                run_id,
            )
        finally:
            db.close()


@celery_app.task(
    bind=True,
    base=ReliableWorkflowTask,
    name="app.tasks.execute_workflow_task",
    autoretry_for=(OperationalError, InterfaceError),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_workflow_task(self, run_id: int, steps: list[dict]):
    db = SessionLocal()

    try:
        run = db.get(WorkflowRun, run_id)

        if run is None:
            logger.error("Workflow run %s was not found", run_id)
            return {"run_id": run_id, "status": "not_found"}

        # Do not execute duplicate deliveries for completed or failed runs.
        if run.status in TERMINAL_STATUSES:
            logger.info(
                "Ignoring duplicate delivery for terminal workflow %s (status=%s)",
                run_id,
                run.status,
            )
            return {
                "run_id": run.id,
                "status": run.status,
                "message": "Workflow is already in a terminal state.",
            }

        if run.status not in ACTIVE_STATUSES:
            logger.warning(
                "Workflow %s has unsupported status %s; execution skipped",
                run_id,
                run.status,
            )
            return {"run_id": run.id, "status": run.status}

        if getattr(run, "started_at", None) is None:
            run.started_at = utc_now()

        run.status = "running"
        run.error = None
        record_audit_event(
            db, run.id, "started", "Workflow execution started by worker.",
            details={"task_id": getattr(self.request, "id", None)},
        )
        db.commit()

        logger.info(
            "Workflow %s started (Celery task ID=%s)",
            run_id,
            getattr(self.request, "id", None),
        )

        def cancellation_requested() -> bool:
            # Refresh the persisted flag so requests from the API are observed
            # between steps, even though this worker has an open DB session.
            db.expire_all()
            current_run = db.get(WorkflowRun, run_id)
            return current_run is None or bool(getattr(current_run, "cancel_requested", False))

        def persist_step_trace(trace: dict) -> None:
            db.add(WorkflowStepTrace(run_id=run_id, **trace))
            # Persist each step as it finishes so diagnostics survive later failures.
            db.commit()

        try:
            results = execute_workflow_steps(
                steps, execute_tool, trace_callback=persist_step_trace,
                should_cancel=cancellation_requested,
            )
        except WorkflowCancelledError as exc:
            run.results = exc.partial_results
            run.total_steps = len(exc.partial_results)
            finish_run(run, "cancelled", str(exc))
            record_audit_event(
                db, run.id, "cancelled", "Workflow execution cancelled.",
                details={"next_step_number": exc.next_step_number},
            )
            db.commit()
            logger.info("Workflow %s cancelled before step %s", run_id, exc.next_step_number)
            return {
                "run_id": run.id,
                "status": "cancelled",
                "total_steps": run.total_steps,
                "results": run.results,
                "error": run.error,
                "finished_at": run.finished_at.isoformat(),
                "duration_seconds": run.duration_seconds,
            }
        except WorkflowStepExecutionError as exc:
            run.results = exc.partial_results
            run.total_steps = len(exc.partial_results)
            # Preserve the established worker-level wording for unexpected
            # runtime failures while retaining step details for other errors.
            if isinstance(exc.cause, RuntimeError):
                error_message = f"Unexpected worker error: {exc.cause}"
            else:
                error_message = str(exc)
            finish_run(run, "failed", error_message)
            record_audit_event(
                db, run.id, "failed", "Workflow execution failed.",
                details={"error": error_message, "total_steps": run.total_steps,
                         "duration_seconds": run.duration_seconds},
            )
            db.commit()

            logger.warning(
                "Workflow %s failed at step %s: %s",
                run_id,
                exc.step_number,
                exc,
            )
            return {
                "run_id": run.id,
                "status": "failed",
                "error": run.error,
                "total_steps": run.total_steps,
                "results": run.results,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat(),
                "duration_seconds": run.duration_seconds,
            }

        run.results = results
        run.total_steps = len(results)
        finish_run(run, "completed")
        record_audit_event(
            db, run.id, "completed", "Workflow execution completed successfully.",
            details={"total_steps": run.total_steps, "duration_seconds": run.duration_seconds},
        )
        db.commit()

        logger.info(
            "Workflow %s completed successfully (%s steps, %.3f seconds)",
            run_id,
            run.total_steps,
            run.duration_seconds or 0.0,
        )
        return {
            "run_id": run.id,
            "status": "completed",
            "total_steps": run.total_steps,
            "results": run.results,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat(),
            "duration_seconds": run.duration_seconds,
        }

    except (OperationalError, InterfaceError):
        db.rollback()
        logger.warning(
            "Temporary database error for workflow %s; Celery may retry",
            run_id,
            exc_info=True,
        )
        raise
    except Exception:
        db.rollback()
        logger.exception("Unhandled worker error for workflow %s", run_id)
        raise
    finally:
        db.close()


def _next_schedule_occurrence(due_at, recurrence, timezone_name, interval_minutes):
    """Calculate the next run in UTC, preserving local wall time for daily/weekly schedules."""
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    else:
        due_at = due_at.astimezone(timezone.utc)
    if recurrence == "interval":
        step = timedelta(minutes=interval_minutes or 1)
        candidate = due_at + step
        now = utc_now()
        while candidate <= now:
            candidate += step
        return candidate

    zone = ZoneInfo(timezone_name)
    local_due = due_at.astimezone(zone)
    delta = timedelta(days=1 if recurrence == "daily" else 7)
    candidate = (local_due + delta).astimezone(timezone.utc)
    now = utc_now()
    # Skip missed occurrences after a long outage instead of creating a backlog.
    while candidate <= now:
        local_due = candidate.astimezone(zone)
        candidate = (local_due + delta).astimezone(timezone.utc)
    return candidate


@celery_app.task(name="app.tasks.dispatch_due_workflow_schedules")
def dispatch_due_workflow_schedules():
    """Claim due recurring schedules and enqueue one workflow run per claimed occurrence.

    Run by Celery Beat every 30 seconds. A conditional database update prevents
    concurrent beat processes from claiming the same occurrence.
    """
    now = utc_now()
    db = SessionLocal()
    dispatched = 0
    try:
        due_schedules = (
            db.query(WorkflowSchedule)
            .filter(
                WorkflowSchedule.enabled.is_(True),
                WorkflowSchedule.next_run_at <= now,
            )
            .order_by(WorkflowSchedule.next_run_at.asc(), WorkflowSchedule.id.asc())
            .limit(100)
            .all()
        )

        for schedule in due_schedules:
            due_at = schedule.next_run_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=timezone.utc)
            else:
                due_at = due_at.astimezone(timezone.utc)
            next_at = _next_schedule_occurrence(
                due_at, schedule.recurrence, schedule.timezone, schedule.interval_minutes
            )

            # Compare-and-set claim: only one beat process can advance this due occurrence.
            claim = db.execute(
                update(WorkflowSchedule)
                .where(
                    WorkflowSchedule.id == schedule.id,
                    WorkflowSchedule.enabled.is_(True),
                    WorkflowSchedule.next_run_at == schedule.next_run_at,
                )
                .values(next_run_at=next_at, last_run_at=due_at)
            )
            if claim.rowcount != 1:
                db.rollback()
                continue
            db.commit()

            run = WorkflowRun(
                name=schedule.name,
                status="queued",
                total_steps=0,
                results=[],
                error=None,
                scheduled_for=due_at,
            )
            db.add(run)
            db.flush()
            record_audit_event(
                db, run.id, "created", "Recurring workflow run created.",
                details={"schedule_id": schedule.id, "scheduled_for": due_at.isoformat()},
            )
            db.commit()
            db.refresh(run)

            try:
                task = execute_workflow_task.delay(run.id, schedule.steps)
                run.celery_task_id = task.id
                db.commit()
                dispatched += 1
            except Exception as exc:
                db.rollback()
                failed_run = db.get(WorkflowRun, run.id)
                if failed_run is not None and failed_run.status == "queued":
                    finished_at = utc_now()
                    failed_run.status = "failed"
                    failed_run.error = f"Could not enqueue scheduled workflow: {type(exc).__name__}: {exc}"
                    failed_run.started_at = finished_at
                    failed_run.finished_at = finished_at
                    failed_run.duration_seconds = 0.0
                    record_audit_event(
                        db, failed_run.id, "failed", "Recurring workflow could not be queued.",
                        details={"schedule_id": schedule.id, "error": failed_run.error},
                        created_at=finished_at,
                    )
                    db.commit()
                logger.exception("Could not enqueue scheduled workflow for schedule %s", schedule.id)
        return {"dispatched": dispatched, "checked": len(due_schedules)}
    finally:
        db.close()
