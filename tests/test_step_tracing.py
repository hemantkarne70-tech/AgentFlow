import pytest

from app.workflow_runtime import (
    WorkflowStepExecutionError,
    execute_workflow_steps,
)


def test_successful_steps_emit_timing_and_io_traces_without_changing_results():
    traces = []
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
        trace_callback=traces.append,
    )

    assert results == [
        {"step": 1, "tool": "word_count", "result": {"words": 2, "characters": 15}},
        {"step": 2, "tool": "uppercase", "result": "2"},
    ]
    assert len(traces) == 2
    assert [trace["status"] for trace in traces] == ["completed", "completed"]
    assert traces[0]["input_preview"] == "hello agentflow"
    assert traces[0]["output_preview"] == '{"characters": 15, "words": 2}'
    assert traces[1]["input_preview"] == "2"
    assert traces[1]["output_preview"] == "2"
    for trace in traces:
        assert trace["started_at"] is not None
        assert trace["finished_at"] >= trace["started_at"]
        assert trace["duration_seconds"] >= 0
        assert trace["error"] is None


def test_failed_step_emits_failure_trace_and_preserves_prior_results():
    traces = []

    def execute_tool(tool, value):
        if value == "bad":
            raise ValueError("invalid input")
        return value.upper()

    with pytest.raises(WorkflowStepExecutionError) as error:
        execute_workflow_steps(
            [
                {"tool": "uppercase", "input": "hello"},
                {"tool": "calculator", "input": "bad"},
            ],
            execute_tool,
            trace_callback=traces.append,
        )

    assert len(error.value.partial_results) == 1
    assert error.value.partial_results[0]["result"] == "HELLO"
    assert [trace["status"] for trace in traces] == ["completed", "failed"]
    assert traces[1]["step_number"] == 2
    assert "ValueError: invalid input" in traces[1]["error"]
    assert traces[1]["finished_at"] >= traces[1]["started_at"]


def test_trace_previews_are_bounded():
    traces = []
    execute_workflow_steps(
        [{"tool": "uppercase", "input": "x" * 3000}],
        lambda tool, value: value,
        trace_callback=traces.append,
    )
    assert len(traces[0]["input_preview"]) < 2100
    assert traces[0]["input_preview"].endswith("...[truncated]")
