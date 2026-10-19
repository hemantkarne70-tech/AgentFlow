
import ast
import math
import operator
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import WorkflowRun


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
    input: str = Field(max_length=500)


class WorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=20)


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
        "application": "AgentFlow",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/workflows/execute")
def execute_workflow(
    workflow: WorkflowRequest,
    db: Session = Depends(get_db),
):
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

    status = "failed" if error_message else "completed"

    run = WorkflowRun(
        name=workflow.name,
        status=status,
        total_steps=len(results),
        results=results,
        error=error_message,
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
            },
        )

    return {
        "run_id": run.id,
        "workflow": run.name,
        "status": run.status,
        "total_steps": run.total_steps,
        "results": run.results,
        "created_at": run.created_at,
    }
    
@app.get("/workflows/runs")
def list_workflow_runs(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    if not 1 <= limit <= 100:
        raise HTTPException(
            status_code=422,
            detail="Limit must be between 1 and 100.",
        )

    if offset < 0:
        raise HTTPException(
            status_code=422,
            detail="Offset cannot be negative.",
        )

    runs = (
        db.query(WorkflowRun)
        .order_by(WorkflowRun.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "count": len(runs),
        "runs": [
            {
                "run_id": run.id,
                "workflow": run.name,
                "status": run.status,
                "total_steps": run.total_steps,
                "error": run.error,
                "created_at": run.created_at,
            }
            for run in runs
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

    return {
        "run_id": run.id,
        "workflow": run.name,
        "status": run.status,
        "total_steps": run.total_steps,
        "results": run.results,
        "error": run.error,
        "created_at": run.created_at,
    }