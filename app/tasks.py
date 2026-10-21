
import logging

from celery import Task
from sqlalchemy.exc import InterfaceError, OperationalError

from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import WorkflowRun
from app.tools import execute_tool


logger = logging.getLogger(__name__)


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
        db.commit()

        results = []

        for index, step in enumerate(steps, start=1):
            try:
                result = execute_tool(step["tool"], step["input"])

            except (ValueError, SyntaxError, ArithmeticError) as exc:
                run.status = "failed"
                run.error = f"Step {index} failed: {exc}"
                run.results = results
                run.total_steps = len(results)
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
        db.commit()

        logger.info("Workflow %s completed successfully", run_id)

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
