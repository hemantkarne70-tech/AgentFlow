import logging

from celery import Task
from sqlalchemy.exc import InterfaceError, OperationalError
from datetime import datetime, timezone
from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import WorkflowRun
from app.tools import execute_tool
from app.logging_config import configure_logging


configure_logging()
logger = logging.getLogger(__name__)

def finalize_workflow_timing(run):
    """Record workflow completion time and elapsed duration."""
    finished_at = datetime.now(timezone.utc)
    run.finished_at = finished_at

    if run.started_at is not None:
        started_at = run.started_at

        # SQLite may return a naive datetime.
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        run.duration_seconds = round(
            max(0.0, (finished_at - started_at).total_seconds()),
            3,
        )

class ReliableWorkflowTask(Task):
    """Record a final task failure when possible."""

    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        run_id = args[0] if args else None

        if run_id is None:
            return

        db = SessionLocal()

        try:
            run = db.get(WorkflowRun, run_id)

            if run and run.status in ("queued", "running"):
                run.status = "failed"
                run.error = f"Background task failed: {exc}"
                finalize_workflow_timing(run)
                db.commit()

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

        if run.status not in ("queued", "running"):
            logger.info("Workflow %s started", run_id)
            return {
                "run_id": run.id,
                "status": run.status,
                "message": "Workflow is no longer eligible for execution.",
            }

        # Reset saved progress before reprocessing.
        # This is safe for the current deterministic, side-effect-free tools.
        run.status = "running"
        run.error = None
        run.results = []
        run.total_steps = 0
        run.started_at = datetime.now(timezone.utc)
        run.finished_at = None
        run.duration_seconds = None
        db.commit()
        logger.info("Workflow %s started", run_id)

        results = []

        for index, step in enumerate(steps, start=1):
            try:
                result = execute_tool(step["tool"], step["input"])

            except (ValueError, SyntaxError, ArithmeticError) as exc:
                run.status = "failed"
                run.error = f"Step {index} failed: {exc}"
                run.results = results
                run.total_steps = len(results)
                finalize_workflow_timing(run)
                db.commit()

                logger.warning(
                    "Workflow %s failed at step %s: %s",
                    run_id,
                    index,
                    exc,
                )

                return {
                    "run_id": run.id,
                    "status": "failed",
                    "error": run.error,
                }

            results.append({
                "step": index,
                "tool": step["tool"],
                "result": result,
            })

            run.results = results
            run.total_steps = len(results)
            db.commit()

        run.status = "completed"
        run.error = None
        finalize_workflow_timing(run)
        db.commit()

        logger.info(
            "Workflow %s completed successfully in %.3f seconds",
            run_id,
            run.duration_seconds,
        )

        return {
            "run_id": run.id,
            "status": "completed",
            "total_steps": run.total_steps,
            "results": run.results,
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
