"""Common tool contract. Tools may be sync or async; the executor normalizes them."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from models.errors import ErrorBody
from models.observations import Observation
from models.state import AgentState


@dataclass
class ToolResult:
    success: bool
    message: str = ""
    updates: dict[str, Any] = field(default_factory=dict)
    error: ErrorBody | None = None
    event: str | None = None

    def to_observation(self, tool_name: str) -> Observation:
        if self.success:
            return Observation(success=True, tool=tool_name, data=self.updates or None)
        return Observation(success=False, tool=tool_name, error=self.error, data=self.updates or None)


class Tool(ABC):
    name: str
    purpose: str = ""
    description: str = ""

    def execute(self, state: AgentState) -> Observation | ToolResult:
        raise NotImplementedError
