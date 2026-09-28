"""Code-registered tool catalog. Names the catalog does not contain cannot run."""

from __future__ import annotations

from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.platform.tools.definition import ToolDefinition
from backend.platform.tools.errors import fail
from services.logging import redact_text


class PlatformToolRegistry:
    """The model cannot register a tool. Only server code calls `register`."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if not isinstance(tool, ToolDefinition):
            raise fail("INVALID_DEFINITION", "Tool definition is required.")
        if tool.name in INSTAGRAM_PUBLISHING_TOOLS:
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=tool.name,
            )
        if tool.name in self._tools:
            raise fail("TOOL_ALREADY_REGISTERED", "Tool is already registered.", tool=tool.name)
        self._tools[tool.name] = tool

    def find(self, name: str) -> ToolDefinition | None:
        if not isinstance(name, str) or not name.strip():
            return None
        return self._tools.get(name.strip())

    def get(self, name: str) -> ToolDefinition:
        if not isinstance(name, str) or not name.strip():
            raise fail("UNKNOWN_TOOL", "Unknown tool.")
        tool_name = name.strip()
        if tool_name in INSTAGRAM_PUBLISHING_TOOLS:
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=tool_name,
            )
        tool = self._tools.get(tool_name)
        if tool is None:
            raise fail("UNKNOWN_TOOL", "Unknown tool.", tool=redact_text(tool_name)[:80])
        return tool

    def catalog(self) -> list[dict[str, object]]:
        listed = [tool.catalog_entry() for tool in self._tools.values()]
        listed.sort(key=lambda item: str(item["name"]))
        return listed
