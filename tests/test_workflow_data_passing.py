import pytest

from app.workflow_runtime import (
    WorkflowInputError,
    WorkflowStepExecutionError,
    execute_workflow_steps,
    resolve_step_input,
)


def test_later_step_can_consume_prior_string_result():
    results = execute_workflow_steps(
        [
            {"tool": "uppercase", "input": "hello agentflow"},
            {"tool": "uppercase", "input_from": {"step": 1}},
        ],
        lambda tool, value: value.upper() if tool == "uppercase" else value,
    )
    assert results[1]["result"] == "HELLO AGENTFLOW"


def test_reference_can_select_field_from_dict_result():
    results = execute_workflow_steps(
        [
            {"tool": "word_count", "input": "hello agentflow"},
            {"tool": "uppercase", "input_from": {"step": 1, "field": "words"}},
        ],
        lambda tool, value: (
            {"words": len(value.split()), "characters": len(value)}
            if tool == "word_count"
            else value.upper()
        ),
    )
    assert results[1]["result"] == "2"


def test_literal_input_workflows_remain_compatible():
    results = execute_workflow_steps(
        [{"tool": "uppercase", "input": "hello"}],
        lambda tool, value: value.upper(),
    )
    assert results == [{"step": 1, "tool": "uppercase", "result": "HELLO"}]


def test_future_reference_is_rejected_and_partial_results_preserved():
    with pytest.raises(WorkflowStepExecutionError) as error:
        execute_workflow_steps(
            [
                {"tool": "uppercase", "input": "hello"},
                {"tool": "uppercase", "input_from": {"step": 2}},
            ],
            lambda tool, value: value.upper(),
        )
    assert error.value.step_number == 2
    assert error.value.partial_results[0]["result"] == "HELLO"
    assert isinstance(error.value.cause, WorkflowInputError)


def test_missing_field_is_rejected():
    with pytest.raises(WorkflowInputError):
        resolve_step_input(
            {"input_from": {"step": 1, "field": "missing"}},
            [{"step": 1, "tool": "word_count", "result": {"words": 2}}],
            2,
        )
