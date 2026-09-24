"""Allowlisted MCP tool registry and argument checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.mcp.errors import MalformedArguments, UnknownTool
from backend.mcp.permissions import assert_tool_allowed
from backend.mcp.tenant_isolation import TenantContext

ToolHandler = Callable[[TenantContext, dict[str, Any]], Awaitable[dict[str, Any]]]

_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
}


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    server: str
    handler: ToolHandler


def object_schema(
    properties: dict[str, dict[str, Any]] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") != "object":
        raise MalformedArguments("Tool arguments must be an object.")
    if not isinstance(arguments, dict):
        raise MalformedArguments("Tool arguments must be an object.")
    properties: dict[str, dict[str, Any]] = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    for key in required:
        if key not in arguments or arguments[key] is None:
            raise MalformedArguments(f"Missing required argument '{key}'.")
    if schema.get("additionalProperties") is False:
        unexpected = [key for key in arguments if key not in properties]
        if unexpected:
            raise MalformedArguments("Tool arguments are not valid.")
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in properties:
            continue
        if value is None and key not in required:
            continue
        cleaned[key] = _check_property(key, value, properties[key])
    return cleaned


def _check_property(key: str, value: Any, spec: dict[str, Any]) -> Any:
    expected = spec.get("type")
    if expected is not None:
        check = _TYPE_CHECKS.get(expected)
        if check is None or not check(value):
            raise MalformedArguments(f"Argument '{key}' has an invalid type.")
    if expected == "string":
        minimum = spec.get("minLength")
        maximum = spec.get("maxLength")
        if minimum is not None and len(value) < int(minimum):
            raise MalformedArguments(f"Argument '{key}' is too short.")
        if maximum is not None and len(value) > int(maximum):
            raise MalformedArguments(f"Argument '{key}' is too long.")
    if expected == "integer":
        if "minimum" in spec and value < int(spec["minimum"]):
            raise MalformedArguments(f"Argument '{key}' is out of range.")
        if "maximum" in spec and value > int(spec["maximum"]):
            raise MalformedArguments(f"Argument '{key}' is out of range.")
    enum = spec.get("enum")
    if enum is not None and value not in enum:
        raise MalformedArguments(f"Argument '{key}' is not an allowed value.")
    return value


class MCPRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, MCPTool] = {}

    def register(self, tool: MCPTool) -> None:
        name = assert_tool_allowed(tool.name)
        if name in self._tools:
            raise UnknownTool(f"Tool '{name}' is already registered.")
        self._tools[name] = tool

    def get(self, name: str) -> MCPTool:
        tool_name = assert_tool_allowed(name)
        tool = self._tools.get(tool_name)
        if tool is None:
            raise UnknownTool(f"Unknown tool '{tool_name}'.")
        return tool

    def discover(self) -> list[dict[str, Any]]:
        listed: list[dict[str, Any]] = []
        for tool in self._tools.values():
            assert_tool_allowed(tool.name)
            listed.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "server": tool.server,
                    "input_schema": tool.input_schema,
                }
            )
        listed.sort(key=lambda item: item["name"])
        return listed
