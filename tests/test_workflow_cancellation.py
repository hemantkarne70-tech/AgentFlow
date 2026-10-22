import pytest

from app.workflow_runtime import WorkflowCancelledError, execute_workflow_steps


def test_cancellation_between_steps_preserves_completed_results():
    calls = []
    cancel_checks = iter([False, False, True])

    def execute_tool(tool, value):
        calls.append((tool, value))
        return value.upper()

    steps = [
        {"tool": "uppercase", "input": "first"},
        {"tool": "uppercase", "input": "second"},
        {"tool": "uppercase", "input": "third"},
    ]

    with pytest.raises(WorkflowCancelledError) as caught:
        execute_workflow_steps(
            steps,
            execute_tool,
            should_cancel=lambda: next(cancel_checks),
        )

    assert caught.value.next_step_number == 3
    assert caught.value.partial_results == [
        {"step": 1, "tool": "uppercase", "result": "FIRST"},
        {"step": 2, "tool": "uppercase", "result": "SECOND"},
    ]
    assert len(calls) == 2


def test_cancel_before_first_step_runs_no_tools():
    calls = []
    with pytest.raises(WorkflowCancelledError) as caught:
        execute_workflow_steps(
            [{"tool": "uppercase", "input": "hello"}],
            lambda tool, value: calls.append(value),
            should_cancel=lambda: True,
        )
    assert caught.value.next_step_number == 1
    assert caught.value.partial_results == []
    assert calls == []


def test_existing_calls_work_without_cancellation_callback():
    result = execute_workflow_steps(
        [{"tool": "uppercase", "input": "hello"}],
        lambda tool, value: value.upper(),
    )
    assert result == [{"step": 1, "tool": "uppercase", "result": "HELLO"}]
