import ast
import math
import operator
from typing import Any, Literal
from datetime import datetime, timezone
import logging

import redis

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from fastapi import Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db import Base, SessionLocal, engine
from app.models import WorkflowRun
from app.tasks import execute_workflow_task
from app.ai_planner import (
    AIConfigurationError,
    AIPlanningError,
    generate_workflow_plan,
)

from app.config import APP_NAME, APP_VERSION, REDIS_URL

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AgentFlow API",
    description="A lightweight workflow execution engine.",
    version="0.1.0",
)

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Only these arithmetic operations are permitted.
BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def safe_calculate(expression: str) -> int | float:
    """Evaluate basic arithmetic without executing arbitrary Python code."""
    if not expression.strip() or len(expression) > 100:
        raise ValueError("Expression must contain 1–100 characters.")

    tree = ast.parse(expression, mode="eval")

    def evaluate(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)

        if (
            isinstance(node, ast.Constant)
            and type(node.value) in (int, float)
        ):
            return node.value

        if isinstance(node, ast.UnaryOp):
            operation = UNARY_OPERATORS.get(type(node.op))
            if operation is None:
                raise ValueError("Unsupported unary operator.")
            return operation(evaluate(node.operand))

        if isinstance(node, ast.BinOp):
            operation = BINARY_OPERATORS.get(type(node.op))
            if operation is None:
                raise ValueError("Unsupported arithmetic operator.")

            left = evaluate(node.left)
            right = evaluate(node.right)

            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("Exponent magnitude must not exceed 10.")

            result = operation(left, right)

            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError("Result must be finite.")

            if isinstance(result, int) and result.bit_length() > 1024:
                raise ValueError("Result is too large.")

            return result

        raise ValueError("Expression contains an unsupported operation.")

    return evaluate(tree)


class WorkflowStep(BaseModel):
    tool: Literal["calculator", "uppercase", "word_count"]
    input: str = Field(
        min_length=1,
        max_length=500,
    )

    @field_validator("input")
    @classmethod
    def validate_input_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Step input cannot be blank.")
        return value


class WorkflowRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
    )
    steps: list[WorkflowStep] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("name")
    @classmethod
    def validate_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Workflow name cannot be blank.")
        return value


def execute_tool(tool: str, value: str) -> Any:
    """Run one of the explicitly registered tools."""
    if tool == "calculator":
        return safe_calculate(value)

    if tool == "uppercase":
        return value.upper()

    if tool == "word_count":
        return {
            "words": len(value.split()),
            "characters": len(value),
        }

    raise ValueError(f"Unsupported tool: {tool}")


@app.get("/")
def health_check():
    return {
        "application": APP_NAME,
        "status": "running",
        "version": APP_VERSION,
    }


@app.get("/health")
def health():
    # Backward-compatible liveness endpoint.
    return {"status": "healthy"}


@app.get("/health/live")
def health_live():
    """Confirm that the API process is responding."""
    return {
        "status": "alive",
        "application": APP_NAME,
        "version": APP_VERSION,
    }


@app.get("/health/ready")
def health_ready():
    """Check whether the API's database and Redis are reachable."""
    checks = {}

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("Database readiness check failed")
        checks["database"] = "unavailable"

    try:
        client = redis.Redis.from_url(
            REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        checks["redis"] = "ok"
    except Exception:
        logger.exception("Redis readiness check failed")
        checks["redis"] = "unavailable"

    if any(value != "ok" for value in checks.values()):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "checks": checks,
            },
        )

    return {
        "status": "ready",
        "checks": checks,
    }



class AIWorkflowRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("prompt")
    @classmethod
    def validate_prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt cannot be blank.")
        return value.strip()


@app.post("/workflows/plan")
def plan_ai_workflow(request: AIWorkflowRequest):
    """Generate a validated workflow plan without executing it."""
    try:
        plan = generate_workflow_plan(request.prompt)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AIPlanningError as exc:
        logger.exception("AI workflow planning failed")
        raise HTTPException(
            status_code=502,
            detail=f"AI workflow planning failed: {exc}",
        ) from exc

    return {
        "workflow": plan.name,
        "steps": [step.model_dump() for step in plan.steps],
        "total_steps": len(plan.steps),
        "execution_started": False,
    }


@app.post("/workflows/execute-ai", status_code=202)
def execute_ai_workflow(
    request: AIWorkflowRequest,
    db: Session = Depends(get_db),
):
    """Plan a workflow with AI, then queue it using the normal Celery path."""
    try:
        plan = generate_workflow_plan(request.prompt)
    except AIConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AIPlanningError as exc:
        logger.exception("AI workflow planning failed")
        raise HTTPException(
            status_code=502,
            detail=f"AI workflow planning failed: {exc}",
        ) from exc

    workflow = WorkflowRequest(
        name=plan.name,
        steps=[step.model_dump() for step in plan.steps],
    )

    # Reuse the established async enqueue path and run tracking.
    return execute_workflow_async(workflow=workflow, db=db)


@app.post("/workflows/execute")
def execute_workflow(
    workflow: WorkflowRequest,
    db: Session = Depends(get_db),
):
    started_at = datetime.now(timezone.utc)
    results = []
    error_message = None

    for index, step in enumerate(workflow.steps, start=1):
        try:
            result = execute_tool(step.tool, step.input)
        except (
            ValueError,
            SyntaxError,
            ArithmeticError,
        ) as exc:
            error_message = f"Step {index} failed: {exc}"
            break

        results.append(
            {
                "step": index,
                "tool": step.tool,
                "result": result,
            }
        )

    finished_at = datetime.now(timezone.utc)
    duration_seconds = round(
        max(0.0, (finished_at - started_at).total_seconds()),
        3,
    )

    status = "failed" if error_message else "completed"

    run = WorkflowRun(
        name=workflow.name,
        status=status,
        total_steps=len(results),
        results=results,
        error=error_message,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    if error_message:
        raise HTTPException(
        status_code=400,
        detail={
            "run_id": run.id,
            "message": error_message,
            "started_at": (
                run.started_at.isoformat()
                if run.started_at
                else None
            ),
            "finished_at": (
                run.finished_at.isoformat()
                if run.finished_at
                else None
            ),
            "duration_seconds": run.duration_seconds,
        },
    )

    return {
        "run_id": run.id,
        "workflow": run.name,
        "status": run.status,
        "total_steps": run.total_steps,
        "results": run.results,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": run.duration_seconds,
    }
    

@app.post("/workflows/execute-async", status_code=202)
def execute_workflow_async(
    workflow: WorkflowRequest,
    db: Session = Depends(get_db),
):
    run = WorkflowRun(
        name=workflow.name,
        status="queued",
        total_steps=0,
        results=[],
        error=None,
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    steps = [
        step.model_dump()
        for step in workflow.steps
    ]

    try:
        task = execute_workflow_task.delay(run.id, steps)

    except Exception as exc:
        finished_at = datetime.now(timezone.utc)

        started_at = getattr(run, "started_at", None)
        if started_at is None:
            started_at = getattr(run, "created_at", None)

        if started_at is None:
            started_at = finished_at

        # Normalize SQLite's timezone-naive datetime before subtraction.
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        run.started_at = started_at
        run.status = "failed"
        run.error = f"Could not enqueue workflow: {type(exc).__name__}: {exc}"
        run.finished_at = finished_at
        run.duration_seconds = round(
            max(0.0, (finished_at - started_at).total_seconds()),
            3,
        )
        db.commit()
        db.refresh(run)

        logger.exception("Could not enqueue workflow run %s", run.id)

        raise HTTPException(
            status_code=503,
            detail={
                "run_id": run.id,
                "message": (
                    "Unable to queue workflow. "
                    "Check Redis and Celery configuration."
                ),
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat(),
                "duration_seconds": run.duration_seconds,
            },
        ) from exc

    return {
        "run_id": run.id,
        "task_id": task.id,
        "workflow": run.name,
        "status": run.status,
        "status_url": f"/workflows/runs/{run.id}",
    }

    
@app.get("/workflows/runs")
def list_workflow_runs(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    status: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    allowed_statuses = {"queued", "running", "completed", "failed"}

    if status is not None:
        status = status.strip().lower()
        if status not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Invalid workflow status.",
                    "allowed_statuses": sorted(allowed_statuses),
                },
            )

    query = db.query(WorkflowRun)

    if status:
        query = query.filter(WorkflowRun.status == status)

    if search and search.strip():
        query = query.filter(
            WorkflowRun.name.ilike(f"%{search.strip()}%")
        )

    total = query.count()
    total_pages = (total + per_page - 1) // per_page

    runs = (
        query.order_by(WorkflowRun.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "items": [
            {
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "total_steps": run.total_steps,
                "error": run.error,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_seconds": run.duration_seconds,
            }
            for run in runs
        ],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_items": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1 and total > 0,
        },
        "filters": {
            "status": status,
            "search": search.strip() if search and search.strip() else None,
        },
    }


@app.get("/workflows/runs/{run_id}")
def get_workflow_run(
    run_id: int,
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRun, run_id)

    if run is None:
        raise HTTPException(
            status_code=404,
            detail="Workflow run not found.",
        )

    return {
        "run_id": run.id,
        "workflow": run.name,
        "status": run.status,
        "total_steps": run.total_steps,
        "results": run.results,
        "error": run.error,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": run.duration_seconds,
    }