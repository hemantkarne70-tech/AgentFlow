
import ast
import math
import operator
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(
    title="AgentFlow API",
    description="A lightweight workflow execution engine.",
    version="0.1.0",
)


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
def execute_workflow(workflow: WorkflowRequest):
    results = []

    for index, step in enumerate(workflow.steps, start=1):
        try:
            result = execute_tool(step.tool, step.input)
        except (ValueError, SyntaxError, ArithmeticError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Step {index} failed: {exc}",
            ) from exc

        results.append(
            {
                "step": index,
                "tool": step.tool,
                "result": result,
            }
        )

    return {
        "workflow": workflow.name,
        "status": "completed",
        "total_steps": len(results),
        "results": results,
    }
