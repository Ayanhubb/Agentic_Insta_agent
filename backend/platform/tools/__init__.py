"""Generic tool registry above the in-process MCP allowlist."""

from backend.platform.tools.audit import AUDIT_EVENT, AuditEvent, AuditLog
from backend.platform.tools.binding import ToolBinding, bind_agent
from backend.platform.tools.definition import AuditRequirements, Idempotency, ToolDefinition
from backend.platform.tools.errors import ToolExecutionError, ToolFailure
from backend.platform.tools.executor import ToolExecution, ToolExecutor, require_resource_tenant
from backend.platform.tools.mcp_adapter import MCPAdapter, build_platform_registry
from backend.platform.tools.registry import PlatformToolRegistry

__all__ = [
    "AUDIT_EVENT",
    "AuditEvent",
    "AuditLog",
    "AuditRequirements",
    "Idempotency",
    "MCPAdapter",
    "PlatformToolRegistry",
    "ToolBinding",
    "ToolDefinition",
    "ToolExecution",
    "ToolExecutionError",
    "ToolExecutor",
    "ToolFailure",
    "bind_agent",
    "build_platform_executor",
    "build_platform_registry",
    "require_resource_tenant",
]


def build_platform_executor(
    gateway: object,
    *,
    settings: object | None = None,
    reader: object | None = None,
    timeout_seconds: float = 5.0,
    audit: AuditLog | None = None,
) -> ToolExecutor:
    """Platform executor whose MCP tools are the existing allowlisted reads."""
    registry, adapter = build_platform_registry(
        gateway,
        settings=settings,
        reader=reader,
        timeout_seconds=timeout_seconds,
    )
    return ToolExecutor(registry, adapter=adapter, audit=audit)
