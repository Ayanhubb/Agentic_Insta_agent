"""Planner interface, deterministic planner, and a non-executing LLM planner stub."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel, Field, model_validator

from models.errors import AppError, ErrorCode
from models.state import AgentState, UI_STEP_LABELS
from tools.base import Tool

ALLOWED_TOOLS: tuple[str, ...] = (
    "validate_image",
    "prepare_image",
    "upload_image",
    "create_instagram_media",
    "publish_instagram_media",
    "verify_publication",
)
ALLOWED_TOOL_SET = frozenset(ALLOWED_TOOLS)
DEFAULT_INSTAGRAM_IMAGE_STEPS = ALLOWED_TOOLS

PLAN_PURPOSES: dict[str, str] = {
    "validate_image": "Validate uploaded image",
    "prepare_image": "Prepare image for publishing",
    "upload_image": "Create publicly accessible image URL",
    "create_instagram_media": "Create Instagram media container",
    "publish_instagram_media": "Publish image",
    "verify_publication": "Verify publication",
}


class PlannedAction(BaseModel):
    tool: str
    purpose: str


class PlanStep(BaseModel):
    name: str
    tool_name: str


class Plan(BaseModel):
    actions: list[PlannedAction] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_views(self) -> "Plan":
        if self.actions and not self.steps:
            self.steps = [
                PlanStep(name=action.tool, tool_name=action.tool) for action in self.actions
            ]
        if self.steps and not self.actions:
            self.actions = [
                PlannedAction(
                    tool=step.tool_name,
                    purpose=UI_STEP_LABELS.get(step.tool_name, step.name),
                )
                for step in self.steps
            ]
        return self

    @property
    def step_names(self) -> list[str]:
        return [step.name for step in self.steps]


class Planner(ABC):
    @abstractmethod
    def create_plan(
        self,
        task: AgentState,
        available_tools: Sequence[Tool] | None = None,
    ) -> Plan:
        raise NotImplementedError


class DeterministicPlanner(Planner):
    def __init__(self, steps: Sequence[str] = DEFAULT_INSTAGRAM_IMAGE_STEPS) -> None:
        self._steps = list(steps)

    def create_plan(
        self,
        task: AgentState,
        available_tools: Sequence[Tool] | None = None,
    ) -> Plan:
        if task.platform != "instagram":
            raise AppError(ErrorCode.INVALID_REQUEST, "Only Instagram image publishing is supported.")
        registered = {tool.name for tool in available_tools} if available_tools else None
        actions: list[PlannedAction] = []
        for name in self._steps:
            if registered is not None and name not in registered:
                continue
            actions.append(PlannedAction(tool=name, purpose=PLAN_PURPOSES.get(name, name)))
        return Plan(actions=actions)


class LLMPlanner(Planner):
    """Interface for a future LLM planner.

    The LLM may only propose tools from ALLOWED_TOOL_SET. It cannot execute
    Python, shell commands, or arbitrary HTTP requests.
    """

    def create_plan(
        self,
        task: AgentState,
        available_tools: Sequence[Tool] | None = None,
        proposed_tools: list[str] | None = None,
        proposed_actions: list[str] | None = None,
    ) -> Plan:
        names = proposed_tools or proposed_actions
        if not names:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "LLM planning is not enabled. The deterministic planner is used in V1.",
                http_status=503,
            )
        actions: list[PlannedAction] = []
        for name in names:
            if name not in ALLOWED_TOOL_SET:
                raise AppError(
                    ErrorCode.TOOL_NOT_ALLOWED,
                    "Planner proposed a tool that is not allowlisted.",
                    http_status=500,
                )
            actions.append(PlannedAction(tool=name, purpose=PLAN_PURPOSES.get(name, name)))
        return Plan(actions=actions)

    def sanitize_actions(self, proposed: Sequence[dict[str, object]]) -> list[PlannedAction]:
        forbidden = {"command", "code", "script", "python", "shell", "url", "http", "https", "token"}
        actions: list[PlannedAction] = []
        for item in proposed:
            tool = item.get("tool")
            if not isinstance(tool, str) or tool not in ALLOWED_TOOL_SET:
                continue
            if forbidden.intersection(item.keys()):
                continue
            actions.append(PlannedAction(tool=tool, purpose=PLAN_PURPOSES.get(tool, tool)))
        return actions
