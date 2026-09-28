"""Adapt the in-process MCP allowlist into platform tool definitions.

The adapter does not register Instagram publishing tools. It calls the MCP
handler that is already on the allowlist, which calls the existing service.
"""

from __future__ import annotations

from typing import Any

from backend.mcp.client import DEFAULT_TIMEOUT_SECONDS
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.mcp.registry import MCPRegistry, MCPTool
from backend.mcp.tenant_isolation import TenantContext
from backend.platform.tools.definition import AuditRequirements, Idempotency, ToolDefinition
from backend.platform.tools.errors import fail
from backend.platform.tools.registry import PlatformToolRegistry

MCP_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}


class MCPAdapter:
    def __init__(self, mcp_registry: MCPRegistry, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._mcp = mcp_registry
        self.timeout_seconds = timeout_seconds

    def register_into(self, platform: PlatformToolRegistry) -> None:
        for spec in self._mcp.discover():
            tool = self._mcp.get(str(spec["name"]))
            platform.register(self.definition_for(tool))

    def definition_for(self, tool: MCPTool) -> ToolDefinition:
        if tool.name in INSTAGRAM_PUBLISHING_TOOLS:
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=tool.name,
            )
        return ToolDefinition(
            name=tool.name,
            version="1",
            description=tool.description,
            input_schema=tool.input_schema,
            output_schema=MCP_OUTPUT_SCHEMA,
            permissions=frozenset({"read"}),
            tenant_scope="tenant",
            classification="read",
            timeout_seconds=self.timeout_seconds,
            idempotency=Idempotency(idempotent=True),
            audit=AuditRequirements(required=True, record_arguments=True, record_result_keys=True),
            handler=tool.handler,
            plane="mcp_read",
        )

    async def invoke(self, definition: ToolDefinition, tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if definition.plane != "mcp_read" or definition.classification != "read":
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=definition.name,
            )
        if definition.name in INSTAGRAM_PUBLISHING_TOOLS:
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=definition.name,
            )
        tool = self._mcp.get(definition.name)
        raw = await tool.handler(tenant, arguments)
        if not isinstance(raw, dict):
            raise fail("OUTPUT_INVALID", "Tool result did not match the output schema.", tool=definition.name)
        return raw


def build_platform_registry(
    gateway: Any,
    *,
    settings: Any | None = None,
    reader: Any | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[PlatformToolRegistry, MCPAdapter]:
    """Register the current MCP read tools on a platform registry."""
    from backend.mcp.servers import build_registry as build_mcp_registry

    mcp = build_mcp_registry(gateway, settings=settings, reader=reader)
    platform = PlatformToolRegistry()
    adapter = MCPAdapter(mcp, timeout_seconds=timeout_seconds)
    adapter.register_into(platform)
    return platform, adapter
