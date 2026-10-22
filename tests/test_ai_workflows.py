from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.ai_planner import (
    AIConfigurationError,
    AIPlanningError,
    PlannedStep,
    WorkflowPlan,
)

client = TestClient(main.app)


def sample_plan():
    return WorkflowPlan(
        name="Uppercase a phrase",
        steps=[PlannedStep(tool="uppercase", input="hello agentflow")],
    )


def test_plan_endpoint_returns_plan_without_execution(monkeypatch):
    monkeypatch.setattr(main, "generate_workflow_plan", lambda prompt: sample_plan())

    response = client.post(
        "/workflows/plan",
        json={"prompt": "Make hello agentflow uppercase"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "workflow": "Uppercase a phrase",
        "steps": [{"tool": "uppercase", "input": "hello agentflow"}],
        "total_steps": 1,
        "execution_started": False,
    }


def test_plan_endpoint_reports_missing_ai_configuration(monkeypatch):
    def raise_configuration_error(prompt):
        raise AIConfigurationError("AI is not configured.")

    monkeypatch.setattr(main, "generate_workflow_plan", raise_configuration_error)
    response = client.post("/workflows/plan", json={"prompt": "Count these words"})

    assert response.status_code == 503
    assert response.json()["detail"] == "AI is not configured."


def test_plan_endpoint_reports_provider_error(monkeypatch):
    def raise_planning_error(prompt):
        raise AIPlanningError("Provider unavailable.")

    monkeypatch.setattr(main, "generate_workflow_plan", raise_planning_error)
    response = client.post("/workflows/plan", json={"prompt": "Count these words"})

    assert response.status_code == 502
    assert "Provider unavailable." in response.json()["detail"]


def test_blank_prompt_is_rejected():
    response = client.post("/workflows/plan", json={"prompt": "   "})
    assert response.status_code == 422


def test_execute_ai_uses_existing_async_workflow_path(monkeypatch):
    monkeypatch.setattr(main, "generate_workflow_plan", lambda prompt: sample_plan())
    queued_response = {"run_id": 42, "status": "queued", "status_url": "/workflows/runs/42"}
    enqueue = MagicMock(return_value=queued_response)
    monkeypatch.setattr(main, "execute_workflow_async", enqueue)

    response = client.post(
        "/workflows/execute-ai",
        json={"prompt": "Make hello agentflow uppercase"},
    )

    assert response.status_code == 202
    assert response.json() == queued_response
    enqueue.assert_called_once()
    workflow = enqueue.call_args.kwargs["workflow"]
    assert workflow.name == "Uppercase a phrase"
    assert [step.model_dump() for step in workflow.steps] == [
        {"tool": "uppercase", "input": "hello agentflow"}
    ]


def test_planner_rejects_unknown_tool():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        main.WorkflowStep(tool="shell", input="whoami")


def test_max_steps_configuration_is_bounded(monkeypatch):
    from app.ai_planner import _max_steps, AIConfigurationError

    monkeypatch.setenv("AI_MAX_STEPS", "21")
    with pytest.raises(AIConfigurationError):
        _max_steps()
