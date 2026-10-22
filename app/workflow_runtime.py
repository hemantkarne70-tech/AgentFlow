"""Safe workflow execution with data passing and per-step tracing."""

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable


class WorkflowInputError(ValueError):
    """Raised when a step references an invalid or unavailable result."""


class WorkflowCancelledError(RuntimeError):
    """Raised when cancellation is requested between workflow steps."""

    def __init__(self, next_step_number: int, partial_results: list[dict]):
        self.next_step_number = next_step_number
        self.partial_results = partial_results
        super().__init__(f"Workflow cancellation requested before step {next_step_number}.")


class WorkflowStepExecutionError(RuntimeError):
    """Execution failure that retains successful results and trace context."""

    def __init__(self, step_number: int, cause: Exception, partial_results: list[dict]):
        self.step_number = step_number
        self.cause = cause
        self.partial_results = partial_results
        super().__init__(f"Step {step_number} failed: {cause}")


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _preview(value: Any, limit: int = 2000) -> str | None:
    """Create a bounded trace preview without changing the actual tool input/output."""
    if value is None:
        return None
    try:
        rendered = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str
        )
    except Exception:
        rendered = str(value)
    if len(rendered) > limit:
        return rendered[:limit] + "...[truncated]"
    return rendered


def resolve_step_input(step: dict, completed_results: list[dict], current_step: int) -> str:
    """Resolve a literal input or a reference to an earlier step's result."""
    reference = step.get("input_from")
    literal = step.get("input", "")

    if reference is None:
        if not isinstance(literal, str) or not literal.strip():
            raise WorkflowInputError("Step input must be a nonblank string or use input_from.")
        return literal

    if not isinstance(reference, dict):
        raise WorkflowInputError("input_from must be an object with step and optional field.")

    source_step = reference.get("step")
    if type(source_step) is not int or source_step < 1:
        raise WorkflowInputError("input_from.step must be a positive, 1-based step number.")
    if source_step >= current_step:
        raise WorkflowInputError("A step can reference only an earlier step.")
    if source_step > len(completed_results):
        raise WorkflowInputError(f"Step {source_step} has no completed result to reference.")

    value: Any = completed_results[source_step - 1]["result"]
    field_path = reference.get("field")
    if field_path is not None:
        if not isinstance(field_path, str) or not field_path.strip():
            raise WorkflowInputError("input_from.field must be a nonblank string when provided.")
        for part in field_path.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise WorkflowInputError(f"Referenced result field '{field_path}' was not found.")

    resolved_value = _stringify(value)
    if not resolved_value.strip():
        raise WorkflowInputError(
            f"Step {source_step} resolved to an empty input; check the referenced result and field path."
        )
    return resolved_value


def execute_workflow_steps(
    steps: list[dict],
    execute_tool: Callable[[str, str], Any],
    trace_callback: Callable[[dict], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict]:
    """Execute steps in order, preserving result shape and emitting a trace per step."""
    results: list[dict] = []

    for index, step in enumerate(steps, start=1):
        if should_cancel is not None and should_cancel():
            raise WorkflowCancelledError(index, results)

        tool = step.get("tool")
        started_at = datetime.now(timezone.utc)
        monotonic_start = time.perf_counter()
        resolved_input = None

        try:
            if not isinstance(tool, str) or not tool:
                raise WorkflowInputError("Each step must specify a tool.")

            resolved_input = resolve_step_input(step, results, index)
            result = execute_tool(tool, resolved_input)
            finished_at = datetime.now(timezone.utc)
            duration = round(max(0.0, time.perf_counter() - monotonic_start), 6)
            results.append({"step": index, "tool": tool, "result": result})

            if trace_callback is not None:
                trace_callback({
                    "step_number": index,
                    "tool": tool,
                    "status": "completed",
                    "input_preview": _preview(resolved_input),
                    "output_preview": _preview(result),
                    "error": None,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_seconds": duration,
                })
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            duration = round(max(0.0, time.perf_counter() - monotonic_start), 6)
            if trace_callback is not None:
                trace_callback({
                    "step_number": index,
                    "tool": tool if isinstance(tool, str) else "unknown",
                    "status": "failed",
                    "input_preview": _preview(
                        resolved_input if resolved_input is not None
                        else step.get("input_from", step.get("input", ""))
                    ),
                    "output_preview": None,
                    "error": f"{type(exc).__name__}: {exc}"[:4000],
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_seconds": duration,
                })
            raise WorkflowStepExecutionError(index, exc, results) from exc

    return results
