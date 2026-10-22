import ast
import csv
import io
import math
import operator
from typing import Any, Literal
from datetime import datetime, timedelta, timezone
import logging
import hmac
import os
import hashlib
import math as _math
import threading
import time
from collections import deque

import redis

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, text

from app.db import Base, SessionLocal, engine
from app.models import WorkflowRun, WorkflowStepTrace, WorkflowTemplate
from app.tasks import execute_workflow_task
from app.ai_planner import (
    AIConfigurationError,
    AIPlanningError,
    generate_workflow_plan,
)

from app.workflow_runtime import (
    execute_workflow_steps,
    WorkflowStepExecutionError,
)

from app.config import APP_NAME, APP_VERSION, REDIS_URL

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AgentFlow API",
    description="A lightweight workflow execution engine.",
    version="0.1.0",
)

# Optional API-key protection and Redis-backed sliding-window rate limiting.
# Redis shares counters across FastAPI workers/instances. If Redis is unavailable,
# the middleware falls back to a per-process limiter to preserve availability.
_PUBLIC_PATHS = {"/", "/health", "/health/live", "/health/ready"}
_RATE_LIMIT_BUCKETS: dict[str, deque[float]] = {}
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local cutoff_ms = tonumber(ARGV[2])
local member = ARGV[3]
local window_ms = tonumber(ARGV[4])
local limit = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, oldest[2]}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, limit - count - 1}
"""
_RATE_LIMIT_REDIS = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def _rate_limit_settings() -> tuple[int, int]:
    """Return (request limit, window seconds), with safe positive defaults."""
    try:
        limit = int(os.getenv("AGENTFLOW_RATE_LIMIT", "120"))
    except (TypeError, ValueError):
        limit = 120
    try:
        window = int(os.getenv("AGENTFLOW_RATE_WINDOW_SECONDS", "60"))
    except (TypeError, ValueError):
        window = 60
    return max(1, min(limit, 100_000)), max(1, min(window, 86_400))


def _rate_limit_identity(request: Request, configured_key: str) -> str:
    supplied_key = request.headers.get("X-API-Key", "")
    if configured_key and supplied_key:
        # Never retain the raw API key in memory or Redis keys.
        return "key:" + hashlib.sha256(supplied_key.encode("utf-8")).hexdigest()
    client_host = request.client.host if request.client else "unknown"
    return "ip:" + client_host


def _local_rate_limit(identity: str, limit: int, window: int, now: float) -> tuple[bool, int, int]:
    """Per-process fallback; returns (allowed, remaining, retry_after_seconds)."""
    with _RATE_LIMIT_LOCK:
        bucket = _RATE_LIMIT_BUCKETS.setdefault(identity, deque())
        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(_math.ceil(window - (now - bucket[0]))))
            return False, 0, retry_after
        bucket.append(now)
        return True, max(0, limit - len(bucket)), 0


def _redis_rate_limit(identity: str, limit: int, window: int) -> tuple[bool, int, int]:
    """Atomically check/update a sliding window in Redis across app workers."""
    import uuid

    now_ms = int(time.time() * 1000)
    window_ms = window * 1000
    # Hash the identity so neither raw client-controlled values nor API keys are
    # stored in Redis key names.
    redis_key = "agentflow:rate-limit:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    result = _RATE_LIMIT_REDIS.eval(
        _RATE_LIMIT_LUA,
        1,
        redis_key,
        now_ms,
        now_ms - window_ms,
        f"{now_ms}:{uuid.uuid4().hex}",
        window_ms,
        limit,
    )
    allowed = bool(int(result[0]))
    if allowed:
        return True, max(0, int(result[1])), 0
    oldest_ms = int(float(result[1]))
    retry_after = max(1, int(_math.ceil((oldest_ms + window_ms - now_ms) / 1000)))
    return False, 0, retry_after


@app.middleware("http")
async def api_key_and_rate_limit_middleware(request: Request, call_next):
    configured_key = os.getenv("AGENTFLOW_API_KEY", "").strip()

    if configured_key and request.url.path not in _PUBLIC_PATHS:
        supplied_key = request.headers.get("X-API-Key", "")
        if not supplied_key or not hmac.compare_digest(supplied_key, configured_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key."},
                headers={"WWW-Authenticate": "ApiKey"},
            )

    # Health endpoints stay available for container orchestration and don't
    # consume the application request budget.
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    limit, window = _rate_limit_settings()
    identity = _rate_limit_identity(request, configured_key)
    try:
        allowed, remaining, retry_after = _redis_rate_limit(identity, limit, window)
    except redis.RedisError:
        logger.warning("rate_limit_redis_unavailable_using_local_fallback", exc_info=True)
        allowed, remaining, retry_after = _local_rate_limit(
            identity, limit, window, time.monotonic()
        )

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Please retry later."},
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


@app.middleware("http")
async def request_id_and_access_log_middleware(request: Request, call_next):
    """Attach a safe correlation ID and log each HTTP request's outcome."""
    import re
    import uuid

    supplied_id = request.headers.get("X-Request-ID", "").strip()
    if supplied_id and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_id):
        request_id = supplied_id
    else:
        request_id = uuid.uuid4().hex

    request.state.request_id = request_id
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        logger.exception(
            "http_request_failed",
            extra={
                "request_id": request_id,
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        raise
    finally:
        logger.info(
            "http_request_completed",
            extra={
                "request_id": request_id,
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

Base.metadata.create_all(bind=engine)


def ensure_cancellation_schema() -> None:
    """Add the cancellation flag to existing databases without a migration tool."""
    columns = {column["name"] for column in inspect(engine).get_columns("workflow_runs")}
    if "cancel_requested" in columns:
        return

    # SQLite requires a constant default for adding a NOT NULL column to an
    # existing table. PostgreSQL uses its native boolean literal.
    default_value = "0" if engine.dialect.name == "sqlite" else "FALSE"
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE workflow_runs ADD COLUMN cancel_requested "
            f"BOOLEAN NOT NULL DEFAULT {default_value}"
        ))
    logger.info("Added missing workflow_runs.cancel_requested column")


ensure_cancellation_schema()


def ensure_scheduling_schema() -> None:
    """Add scheduling metadata columns to databases created by earlier milestones."""
    columns = {column["name"] for column in inspect(engine).get_columns("workflow_runs")}
    statements = []
    if "scheduled_for" not in columns:
        timestamp_type = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
        statements.append(f"ALTER TABLE workflow_runs ADD COLUMN scheduled_for {timestamp_type} NULL")
    if "celery_task_id" not in columns:
        statements.append("ALTER TABLE workflow_runs ADD COLUMN celery_task_id VARCHAR(100) NULL")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        logger.info("Added missing workflow scheduling metadata columns")


ensure_scheduling_schema()


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

class StepInputReference(BaseModel):
    step: int = Field(ge=1)
    field: str | None = Field(default=None, max_length=100)

    @field_validator("field")
    @classmethod
    def field_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Reference field cannot be blank.")
        return value

class WorkflowStep(BaseModel):
    tool: Literal["calculator", "uppercase", "word_count"]
    input: str = Field(default="", max_length=500)
    input_from: StepInputReference | None = None

    def model_dump(self, *args, **kwargs):
        # Keep absent optional fields out of the default serialized shape.
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def validate_input_source(self):
        if self.input_from is None and not self.input.strip():
            raise ValueError("Provide a nonblank input or input_from reference.")
        if self.input_from is not None and self.input.strip():
            raise ValueError("Provide either input or input_from, not both.")
        return self


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

    @model_validator(mode="after")
    def validate_step_references(self):
        for index, step in enumerate(self.steps, start=1):
            if step.input_from and step.input_from.step >= index:
                raise ValueError("A step can reference only an earlier step.")
        return self



class ScheduledWorkflowRequest(WorkflowRequest):
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def validate_scheduled_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone, for example +05:30 or Z.")
        now = datetime.now(timezone.utc)
        scheduled_utc = value.astimezone(timezone.utc)
        if scheduled_utc <= now:
            raise ValueError("scheduled_at must be in the future.")
        if scheduled_utc > now + timedelta(days=7):
            raise ValueError("scheduled_at cannot be more than 7 days in the future.")
        return scheduled_utc


class WorkflowTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def template_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Template name cannot be blank.")
        return value.strip()

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_template_step_references(self):
        for index, step in enumerate(self.steps, start=1):
            if step.input_from and step.input_from.step >= index:
                raise ValueError("A step can reference only an earlier step.")
        return self


def template_payload(template: WorkflowTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "steps": template.steps,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }

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
        "steps": [step.model_dump(exclude_none=True) for step in plan.steps],
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
        steps=[step.model_dump(exclude_none=True) for step in plan.steps],
    )

    # Reuse the established async enqueue path and run tracking.
    return execute_workflow_async(workflow=workflow, db=db)


@app.post("/workflows/execute")
def execute_workflow(
    workflow: WorkflowRequest,
    db: Session = Depends(get_db),
):
    """Execute a workflow synchronously and persist its run record."""
    started_at = datetime.now(timezone.utc)
    steps = [step.model_dump(exclude_none=True) for step in workflow.steps]
    error_message = None
    step_traces = []

    try:
        results = execute_workflow_steps(
            steps, execute_tool, trace_callback=step_traces.append
        )
    except WorkflowStepExecutionError as exc:
        results = exc.partial_results
        error_message = str(exc)

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
    db.flush()
    for trace in step_traces:
        db.add(WorkflowStepTrace(run_id=run.id, **trace))
    db.commit()
    db.refresh(run)

    if error_message:
        raise HTTPException(
            status_code=400,
            detail={
                "run_id": run.id,
                "message": error_message,
                "results": run.results,
                "started_at": (
                    run.started_at.isoformat() if run.started_at else None
                ),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
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
    """Persist a queued run and hand execution to Celery."""
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

    steps = [step.model_dump(exclude_none=True) for step in workflow.steps]
    try:
        task = execute_workflow_task.delay(run.id, steps)
    except Exception as exc:
        logger.exception("Could not enqueue workflow run %s", run.id)
        finished_at = datetime.now(timezone.utc)
        started_at = getattr(run, "started_at", None) or run.created_at or finished_at
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

        raise HTTPException(
            status_code=503,
            detail={
                "run_id": run.id,
                "message": (
                    "Unable to queue workflow. Check Redis and Celery configuration."
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


@app.post("/workflows/schedule", status_code=202)
def schedule_workflow(
    workflow: ScheduledWorkflowRequest,
    db: Session = Depends(get_db),
):
    """Schedule a workflow for a timezone-aware time within the next seven days."""
    run = WorkflowRun(
        name=workflow.name,
        status="queued",
        total_steps=0,
        results=[],
        error=None,
        scheduled_for=workflow.scheduled_at,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    steps = [step.model_dump(exclude_none=True) for step in workflow.steps]
    try:
        task = execute_workflow_task.apply_async(
            args=[run.id, steps],
            eta=workflow.scheduled_at,
        )
    except Exception as exc:
        logger.exception("Could not schedule workflow run %s", run.id)
        now = datetime.now(timezone.utc)
        started_at = run.created_at or now
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        run.status = "failed"
        run.error = f"Could not schedule workflow: {type(exc).__name__}: {exc}"
        run.started_at = started_at
        run.finished_at = now
        run.duration_seconds = round(max(0.0, (now - started_at).total_seconds()), 3)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "run_id": run.id,
                "message": "Unable to schedule workflow. Check Redis and Celery configuration.",
                "status": run.status,
            },
        ) from exc

    run.celery_task_id = task.id
    db.commit()
    db.refresh(run)
    return {
        "run_id": run.id,
        "task_id": task.id,
        "workflow": run.name,
        "status": run.status,
        "scheduled_for": run.scheduled_for,
        "status_url": f"/workflows/runs/{run.id}",
        "message": "Workflow scheduled successfully.",
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


@app.get("/workflows/analytics")
def workflow_analytics(db: Session = Depends(get_db)):
    """Return aggregate workflow execution metrics for dashboards."""
    status_rows = (
        db.query(WorkflowRun.status, func.count(WorkflowRun.id))
        .group_by(WorkflowRun.status)
        .all()
    )
    status_counts = {status: int(count) for status, count in status_rows}
    total_runs = sum(status_counts.values())
    completed = status_counts.get("completed", 0)
    failed = status_counts.get("failed", 0)
    cancelled = status_counts.get("cancelled", 0)
    terminal_runs = completed + failed + cancelled

    average_duration = db.query(func.avg(WorkflowRun.duration_seconds)).filter(
        WorkflowRun.duration_seconds.isnot(None)
    ).scalar()
    total_steps = db.query(func.coalesce(func.sum(WorkflowRun.total_steps), 0)).scalar()
    latest = db.query(WorkflowRun).order_by(WorkflowRun.id.desc()).first()

    return {
        "total_runs": total_runs,
        "status_counts": status_counts,
        "success_rate_percent": round((completed / terminal_runs) * 100, 2) if terminal_runs else 0.0,
        "average_duration_seconds": round(float(average_duration), 3) if average_duration is not None else None,
        "total_steps": int(total_steps or 0),
        "latest_run": {
            "id": latest.id,
            "name": latest.name,
            "status": latest.status,
            "created_at": latest.created_at,
        } if latest else None,
    }


@app.get("/workflows/runs/export")
def export_workflow_runs(
    status: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    """Export up to 10,000 workflow runs as CSV, optionally filtered."""
    allowed_statuses = {"queued", "running", "completed", "failed", "cancelled"}
    if status is not None:
        status = status.strip().lower()
        if status not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail={"message": "Invalid workflow status.", "allowed_statuses": sorted(allowed_statuses)},
            )

    query = db.query(WorkflowRun)
    if status:
        query = query.filter(WorkflowRun.status == status)
    if search and search.strip():
        query = query.filter(WorkflowRun.name.ilike(f"%{search.strip()}%"))

    runs = query.order_by(WorkflowRun.id.desc()).limit(10_000).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "id", "workflow", "status", "total_steps", "created_at",
        "started_at", "finished_at", "duration_seconds",
    ])
    for run in runs:
        # Prevent spreadsheet formula execution from user-controlled workflow names.
        safe_name = run.name
        if safe_name and safe_name[0] in "=+-@\t\r":
            safe_name = "'" + safe_name
        writer.writerow([
            run.id, safe_name, run.status, run.total_steps, run.created_at,
            run.started_at, run.finished_at, run.duration_seconds,
        ])

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="agentflow-workflow-runs.csv"'},
    )


@app.post("/workflows/runs/{run_id}/cancel")
def cancel_workflow_run(run_id: int, db: Session = Depends(get_db)):
    """Cancel a queued run or request cooperative cancellation of a running run."""
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found.")

    if run.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail=f"Workflow is already in terminal state '{run.status}'.",
        )

    if run.status == "queued":
        now = datetime.now(timezone.utc)
        started_at = run.started_at or run.created_at or now
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        run.cancel_requested = True
        run.status = "cancelled"
        run.error = "Workflow cancelled before execution started."
        run.started_at = started_at
        run.finished_at = now
        run.duration_seconds = round(max(0.0, (now - started_at).total_seconds()), 3)
        db.commit()
        db.refresh(run)
        return {
            "run_id": run.id,
            "status": run.status,
            "message": "Queued workflow cancelled; the worker will skip execution if the task is delivered.",
        }

    if run.status == "running":
        run.cancel_requested = True
        db.commit()
        return {
            "run_id": run.id,
            "status": run.status,
            "cancel_requested": True,
            "message": "Cancellation requested. The current step may finish before cancellation takes effect.",
        }

    raise HTTPException(status_code=409, detail=f"Workflow status '{run.status}' cannot be cancelled.")


@app.get("/workflows/runs/{run_id}/traces")
def get_workflow_run_traces(
    run_id: int,
    db: Session = Depends(get_db),
):
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found.")

    traces = (
        db.query(WorkflowStepTrace)
        .filter(WorkflowStepTrace.run_id == run_id)
        .order_by(WorkflowStepTrace.step_number.asc())
        .all()
    )
    return {
        "run_id": run_id,
        "total_traces": len(traces),
        "items": [
            {
                "step_number": trace.step_number,
                "tool": trace.tool,
                "status": trace.status,
                "input_preview": trace.input_preview,
                "output_preview": trace.output_preview,
                "error": trace.error,
                "started_at": trace.started_at,
                "finished_at": trace.finished_at,
                "duration_seconds": trace.duration_seconds,
            }
            for trace in traces
        ],
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

    traces = (
        db.query(WorkflowStepTrace)
        .filter(WorkflowStepTrace.run_id == run.id)
        .order_by(WorkflowStepTrace.step_number.asc())
        .all()
    )

    return {
        "run_id": run.id,
        "workflow": run.name,
        "status": run.status,
        "total_steps": run.total_steps,
        "results": run.results,
        "step_traces": [
            {
                "step_number": trace.step_number,
                "tool": trace.tool,
                "status": trace.status,
                "input_preview": trace.input_preview,
                "output_preview": trace.output_preview,
                "error": trace.error,
                "started_at": trace.started_at,
                "finished_at": trace.finished_at,
                "duration_seconds": trace.duration_seconds,
            }
            for trace in traces
        ],
        "error": run.error,
        "created_at": run.created_at,
        "scheduled_for": getattr(run, "scheduled_for", None),
        "celery_task_id": getattr(run, "celery_task_id", None),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": run.duration_seconds,
    }


# Workflow templates: reusable definitions that can be executed repeatedly.
@app.post("/workflows/templates", status_code=201)
def create_workflow_template(
    template_request: WorkflowTemplateRequest,
    db: Session = Depends(get_db),
):
    existing = db.query(WorkflowTemplate).filter(
        WorkflowTemplate.name == template_request.name
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="A template with this name already exists.")

    template = WorkflowTemplate(
        name=template_request.name,
        description=template_request.description,
        steps=[step.model_dump(exclude_none=True) for step in template_request.steps],
    )
    db.add(template)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not create workflow template")
        raise HTTPException(status_code=500, detail="Could not save workflow template.")
    db.refresh(template)
    return template_payload(template)


@app.get("/workflows/templates")
def list_workflow_templates(
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(WorkflowTemplate)
    if search and search.strip():
        term = search.strip()
        query = query.filter(WorkflowTemplate.name.ilike(f"%{term}%"))
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    templates = (
        query.order_by(WorkflowTemplate.updated_at.desc(), WorkflowTemplate.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return {
        "items": [template_payload(item) for item in templates],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_items": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_previous": page > 1 and total > 0,
        },
    }


@app.get("/workflows/templates/{template_id}")
def get_workflow_template(template_id: int, db: Session = Depends(get_db)):
    template = db.get(WorkflowTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow template not found.")
    return template_payload(template)


@app.put("/workflows/templates/{template_id}")
def update_workflow_template(
    template_id: int,
    template_request: WorkflowTemplateRequest,
    db: Session = Depends(get_db),
):
    template = db.get(WorkflowTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow template not found.")

    duplicate = db.query(WorkflowTemplate).filter(
        WorkflowTemplate.name == template_request.name,
        WorkflowTemplate.id != template_id,
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="A template with this name already exists.")

    template.name = template_request.name
    template.description = template_request.description
    template.steps = [step.model_dump(exclude_none=True) for step in template_request.steps]
    template.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(template)
    return template_payload(template)


@app.delete("/workflows/templates/{template_id}", status_code=204)
def delete_workflow_template(template_id: int, db: Session = Depends(get_db)):
    template = db.get(WorkflowTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow template not found.")
    db.delete(template)
    db.commit()
    return None


@app.post("/workflows/templates/{template_id}/execute", status_code=202)
def execute_workflow_template(
    template_id: int,
    db: Session = Depends(get_db),
):
    template = db.get(WorkflowTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Workflow template not found.")

    workflow = WorkflowRequest(name=template.name, steps=template.steps)
    return execute_workflow_async(workflow=workflow, db=db)
