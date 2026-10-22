import logging
from datetime import datetime, timezone

from celery import Task
from sqlalchemy.exc import InterfaceError, OperationalError

from app.celery_app import celery_app
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.models import WorkflowRun
from app.tools import execute_tool

from app.workflow_runtime import (
    execute_workflow_steps,
    WorkflowStepExecutionError,
)

configure_logging()
logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "failed"}


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
        db.commit()

        logger.info(
            "Workflow %s started (Celery task ID=%s)",
            run_id,
            getattr(self.request, "id", None),
        )

        try:
            results = execute_workflow_steps(steps, execute_tool)
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
