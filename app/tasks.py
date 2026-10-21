
from app.celery_app import celery_app
from app.db import SessionLocal
from app.models import WorkflowRun
from app.tools import execute_tool


@celery_app.task(name="app.tasks.execute_workflow_task")
def execute_workflow_task(run_id: int, steps: list[dict]):
    db = SessionLocal()

    try:
        run = db.get(WorkflowRun, run_id)

        if run is None:
            return {"status": "failed", "error": "Workflow run not found."}

        if run.status != "queued":
            return {
                "status": run.status,
                "run_id": run.id,
                "message": "Workflow is not queued.",
            }

        run.status = "running"
        db.commit()

        results = []

        for index, step in enumerate(steps, start=1):
            try:
                result = execute_tool(step["tool"], step["input"])

                results.append({
                    "step": index,
                    "tool": step["tool"],
                    "result": result,
                })

                run.results = results
                run.total_steps = len(results)
                db.commit()

            except Exception as exc:
                run.status = "failed"
                run.error = f"Step {index} failed: {exc}"
                run.results = results
                run.total_steps = len(results)
                db.commit()

                return {
                    "run_id": run.id,
                    "status": "failed",
                    "error": run.error,
                }

        run.status = "completed"
        run.results = results
        run.total_steps = len(results)
        run.error = None
        db.commit()

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
