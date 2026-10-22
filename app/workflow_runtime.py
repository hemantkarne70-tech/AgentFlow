"""Safe, deterministic step execution with references to earlier results."""

import json
from typing import Any, Callable


class WorkflowInputError(ValueError):
    """Raised when a step references an invalid or unavailable result."""


class WorkflowStepExecutionError(RuntimeError):
    """Execution failure that retains the successful results before the failure."""

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


def resolve_step_input(step: dict, completed_results: list[dict], current_step: int) -> str:
    """Resolve either a literal input or {step: N, field: 'result.x'} reference.

    Step numbers are 1-based and must refer to an earlier successful step.
    Field paths start at the result value, e.g. `result.words` is expressed as
    `words` because the reference already selects the result field.
    """
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
            f"Step {source_step} resolved to an empty input; "
            "check the referenced result and field path."
        )
    return resolved_value


def execute_workflow_steps(
    steps: list[dict], execute_tool: Callable[[str, str], Any]
) -> list[dict]:
    """Execute steps in order and make successful results available downstream."""
    results: list[dict] = []
    for index, step in enumerate(steps, start=1):
        try:
            tool = step.get("tool")
            if not isinstance(tool, str) or not tool:
                raise WorkflowInputError("Each step must specify a tool.")
            resolved_input = resolve_step_input(step, results, index)
            result = execute_tool(tool, resolved_input)
            results.append({"step": index, "tool": tool, "result": result})
        except Exception as exc:
            raise WorkflowStepExecutionError(index, exc, results) from exc
    return results
