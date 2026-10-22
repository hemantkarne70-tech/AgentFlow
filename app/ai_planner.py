"""AI-powered workflow planning with strict tool allow-list validation."""

import json
import os
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator
from dotenv import load_dotenv

load_dotenv(override=False)

ALLOWED_TOOLS = {"calculator", "uppercase", "word_count"}
DEFAULT_MAX_STEPS = 10


class AIConfigurationError(RuntimeError):
    """Raised when AI provider configuration is missing or invalid."""


class AIPlanningError(RuntimeError):
    """Raised when the AI provider fails or returns an invalid plan."""


class PlannedStep(BaseModel):
    tool: Literal["calculator", "uppercase", "word_count"]
    input: str = Field(min_length=1, max_length=500)

    @field_validator("input")
    @classmethod
    def input_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Step input cannot be blank.")
        return value


class WorkflowPlan(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    steps: list[PlannedStep] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Workflow name cannot be blank.")
        return value.strip()


def _max_steps() -> int:
    raw_value = os.getenv("AI_MAX_STEPS", str(DEFAULT_MAX_STEPS))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise AIConfigurationError("AI_MAX_STEPS must be an integer.") from exc
    if not 1 <= value <= 20:
        raise AIConfigurationError("AI_MAX_STEPS must be between 1 and 20.")
    return value


def generate_workflow_plan(prompt: str) -> WorkflowPlan:
    """Ask the configured AI model for a plan; never execute tools here."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise AIConfigurationError(
            "AI is not configured. Set GROQ_API_KEY in your local .env file."
        )
        
    base_url = os.getenv(
        "AI_BASE_URL",
        "https://api.groq.com/openai/v1",
    ).strip()

    model = os.getenv("OPENAI_MODEL", "openai/gpt-oss-120b").strip()
    if not model:
        raise AIConfigurationError("OPENAI_MODEL cannot be blank.")

    max_steps = _max_steps()
    try:
        base_url = os.getenv(
            "AI_BASE_URL",
            "https://api.groq.com/openai/v1",
        ).strip()

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=30.0,
            max_retries=1,
        )
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are AgentFlow's workflow planner. Convert the user's "
                        "request into a small, deterministic sequence of tool steps. "
                        "Return only a JSON object with keys 'name' and 'steps'. "
                        "Each step must have exactly 'tool' and 'input'. Allowed "
                        "tools: calculator, uppercase, word_count. Use calculator "
                        "only for a single arithmetic expression; uppercase for "
                        "text transformation; word_count for counting words and "
                        "characters. Do not invent tools, execute actions, access "
                        "the network, write code, or claim a tool can do something "
                        "outside its stated purpose. If the request cannot be "
                        "performed using the allowed tools, return a valid plan "
                        "containing one word_count step whose input briefly states "
                        "that the request is unsupported. Keep each input under "
                        "500 characters and the workflow name under 100 characters."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Create no more than {max_steps} steps for this request:\n"
                        f"{prompt}"
                    ),
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise AIPlanningError("The AI provider returned an empty response.")

        payload = json.loads(content)
        plan = WorkflowPlan.model_validate(payload)
        if len(plan.steps) > max_steps:
            raise AIPlanningError(
                f"The generated plan exceeds the configured limit of {max_steps} steps."
            )
        return plan
    except AIPlanningError:
        raise
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AIPlanningError("The AI provider returned an invalid workflow plan.") from exc
    except Exception as exc:
        raise AIPlanningError(
            f"{type(exc).__name__}: AI provider request could not be completed."
        ) from exc
