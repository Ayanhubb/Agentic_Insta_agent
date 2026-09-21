"""Allowlisted tool registry. The Agent never imports tools ad hoc at runtime."""

from __future__ import annotations

from models.errors import AppError, ErrorCode
from tools.base import Tool

from agent.planner import ALLOWED_TOOL_SET


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name not in ALLOWED_TOOL_SET:
            raise AppError(
                ErrorCode.TOOL_NOT_ALLOWED,
                "Refusing to register a tool outside the Instagram image-publishing allowlist.",
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in ALLOWED_TOOL_SET:
            raise AppError(
                ErrorCode.TOOL_NOT_ALLOWED,
                "Requested tool is not in the allowlist.",
            )
        tool = self._tools.get(name)
        if tool is None:
            raise AppError(ErrorCode.TOOL_NOT_ALLOWED, f"Tool '{name}' is not registered.")
        return tool

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def contains(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())
