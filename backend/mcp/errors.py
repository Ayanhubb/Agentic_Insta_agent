"""MCP failures. Messages stay generic so they cannot leak secrets or other tenants."""

from __future__ import annotations


class MCPError(Exception):
    code = "MCP_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class TenantContextRequired(MCPError):
    code = "TENANT_CONTEXT_REQUIRED"


class UnknownTool(MCPError):
    code = "UNKNOWN_TOOL"


class ToolNotAllowed(MCPError):
    code = "TOOL_NOT_ALLOWED"


class MalformedArguments(MCPError):
    code = "MALFORMED_ARGUMENTS"


class MCPTimeout(MCPError):
    code = "TIMEOUT"


class AssetAccessDenied(MCPError):
    code = "ASSET_ACCESS_DENIED"
