"""Run a granted tool. Tenant, schema, classification, secrets, and audit are enforced here."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from backend.mcp.client import sanitize_payload
from backend.mcp.errors import MCPError
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.mcp.registry import _check_property
from backend.mcp.registry import validate_arguments
from backend.mcp.tenant_isolation import TenantContext, require_trusted_tenant, strip_model_tenant_arguments
from backend.platform.tools.audit import AUDIT_EVENT, AuditEvent, AuditLog, arguments_for_audit
from backend.platform.tools.binding import ToolBinding
from backend.platform.tools.errors import ToolExecutionError, fail
from backend.platform.tools.mcp_adapter import MCPAdapter
from backend.platform.tools.registry import PlatformToolRegistry
from services.logging import is_secret_key, redact_text

logger = logging.getLogger("backend.platform.tools")

_MCP_MESSAGES = {
    "UNKNOWN_TOOL": "Unknown tool.",
    "TOOL_NOT_ALLOWED": "Instagram publishing tools are not available through the tool registry.",
    "MALFORMED_ARGUMENTS": "Tool arguments are not valid.",
    "TENANT_CONTEXT_REQUIRED": "A trusted tenant context is required.",
    "TIMEOUT": "Tool execution timed out.",
    "ASSET_ACCESS_DENIED": "The resource is not available for this tenant.",
    "MCP_ERROR": "Tool execution failed.",
    "INTERNAL_ERROR": "Tool execution failed.",
}


@dataclass(frozen=True, slots=True)
class ToolExecution:
    name: str
    version: str
    classification: str
    data: dict[str, Any]


class ToolExecutor:
    """Agent → binding → registry → adapter or service handler."""

    def __init__(
        self,
        registry: PlatformToolRegistry,
        *,
        adapter: MCPAdapter | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.registry = registry
        self.adapter = adapter
        self.audit = audit or AuditLog()

    async def execute(
        self,
        binding: ToolBinding,
        name: str,
        arguments: dict[str, Any] | None,
        tenant: TenantContext | None,
        *,
        version: str | None = None,
    ) -> ToolExecution:
        started = time.perf_counter()
        status = "error"
        error_code: str | None = None
        tenant_id: str | None = None
        result_keys: tuple[str, ...] = ()
        tool_version: str | None = None
        classification: str | None = None
        idempotent: bool | None = None
        ignored_untrusted_tenant = False
        audit_arguments: Any = arguments
        agent_id = binding.agent_id if isinstance(binding, ToolBinding) else None
        tool_name = name.strip() if isinstance(name, str) else ""
        try:
            if not isinstance(binding, ToolBinding):
                raise fail("TOOL_NOT_ALLOWED", "Tool is not granted to this agent.")
            bound = require_trusted_tenant(tenant)
            tenant_id = bound.tenant_id
            safe_arguments, discarded = strip_model_tenant_arguments(arguments)
            ignored_untrusted_tenant = discarded is not None
            audit_arguments = safe_arguments
            if not isinstance(name, str) or not name.strip():
                raise fail("UNKNOWN_TOOL", "Unknown tool.")
            tool_name = name.strip()
            if tool_name in INSTAGRAM_PUBLISHING_TOOLS:
                raise fail(
                    "TOOL_NOT_ALLOWED",
                    "Instagram publishing tools are not available through the tool registry.",
                    tool=tool_name,
                )
            definition = self.registry.get(tool_name)
            tool_version = definition.version
            classification = definition.classification
            idempotent = definition.idempotency.idempotent
            if version is not None and version != definition.version:
                raise fail("UNKNOWN_TOOL", "Unknown tool.", tool=tool_name)
            if definition.classification == "write" and "write" not in binding.permissions:
                raise fail(
                    "UNAUTHORIZED_MUTATION",
                    "Mutation tools require an explicit write grant.",
                    tool=tool_name,
                )
            if definition.name not in binding.tool_names:
                raise fail("TOOL_NOT_ALLOWED", "Tool is not granted to this agent.", tool=tool_name)
            if definition.classification == "read" and "read" not in binding.permissions:
                raise fail("TOOL_NOT_ALLOWED", "Tool is not granted to this agent.", tool=tool_name)
            validated = validate_arguments(definition.input_schema, safe_arguments)
            audit_arguments = validated
            try:
                raw = await asyncio.wait_for(
                    self._invoke(definition, bound, validated),
                    timeout=definition.timeout_seconds,
                )
            except TimeoutError:
                raise fail(
                    "TIMEOUT",
                    "Tool execution timed out.",
                    tool=tool_name,
                    retryable=idempotent is True,
                ) from None
            data = _accept_result(definition, bound, raw)
            result_keys = tuple(sorted(data))
            status = "ok"
            return ToolExecution(
                name=definition.name,
                version=definition.version,
                classification=definition.classification,
                data=data,
            )
        except ToolExecutionError as exc:
            error_code = exc.code
            raise
        except TimeoutError:
            error_code = "TIMEOUT"
            raise fail(
                "TIMEOUT",
                "Tool execution timed out.",
                tool=tool_name,
                retryable=idempotent is True,
            ) from None
        except MCPError as exc:
            error_code = exc.code if isinstance(exc.code, str) else "MCP_ERROR"
            retryable = error_code == "TIMEOUT" and idempotent is True
            raise fail(error_code, _mcp_message(error_code), tool=tool_name, retryable=retryable) from None
        except Exception as exc:
            error_code = "INTERNAL_ERROR"
            logger.warning("platform.tool.internal %s", type(exc).__name__)
            raise fail("INTERNAL_ERROR", "Tool execution failed.", tool=tool_name) from None
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            registered = self.registry.find(tool_name) if tool_name else None
            recorded_arguments = arguments_for_audit(audit_arguments)
            recorded_keys = result_keys
            if registered is not None and not registered.audit.record_arguments:
                recorded_arguments = {"omitted": True}
            if registered is not None and not registered.audit.record_result_keys:
                recorded_keys = ()
            self.audit.record(
                AuditEvent(
                    event=AUDIT_EVENT,
                    tool=redact_text(tool_name)[:80] if tool_name else "",
                    version=tool_version,
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    classification=classification,
                    status=status,
                    error_code=error_code,
                    duration_ms=duration_ms,
                    arguments=recorded_arguments,
                    result_keys=recorded_keys,
                    idempotent=idempotent,
                    ignored_untrusted_tenant=ignored_untrusted_tenant,
                )
            )

    async def _invoke(self, definition: Any, tenant: TenantContext, arguments: dict[str, Any]) -> Any:
        if definition.plane == "mcp_read":
            if self.adapter is None:
                raise fail(
                    "TOOL_NOT_ALLOWED",
                    "This tool cannot be executed without its adapter.",
                    tool=definition.name,
                )
            return await self.adapter.invoke(definition, tenant, arguments)
        return await definition.handler(tenant, arguments)


def require_resource_tenant(resource_tenant_id: object, tenant: TenantContext, *, tool: str | None = None) -> None:
    """Service helpers call this with the row's owner. The model cannot supply that owner."""
    if not isinstance(tenant, TenantContext):
        raise fail("TENANT_CONTEXT_REQUIRED", "A trusted tenant context is required.", tool=tool)
    if str(resource_tenant_id or "") != tenant.tenant_id:
        raise fail("CROSS_TENANT_DENIED", "The resource is not available for this tenant.", tool=tool)


def _accept_result(definition: Any, tenant: TenantContext, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise fail("OUTPUT_INVALID", "Tool result did not match the output schema.", tool=definition.name)
    for key in ("tenant_id", "user_id"):
        value = raw.get(key)
        if value is not None and str(value) != tenant.tenant_id:
            raise fail("CROSS_TENANT_DENIED", "The resource is not available for this tenant.", tool=definition.name)
    data = sanitize_payload(raw)
    if not isinstance(data, dict) or _contains_secret_key(data):
        raise fail("SECRET_REJECTED", "Tool result was rejected.", tool=definition.name)
    data["tenant_id"] = tenant.tenant_id
    try:
        _validate_output(definition.output_schema, data)
    except MCPError:
        raise fail("OUTPUT_INVALID", "Tool result did not match the output schema.", tool=definition.name) from None
    return data


def _validate_output(schema: dict[str, Any], payload: dict[str, Any]) -> None:
    if schema.get("type") != "object":
        raise fail("OUTPUT_INVALID", "Tool result did not match the output schema.")
    properties: dict[str, dict[str, Any]] = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    for key in required:
        if key not in payload or payload[key] is None:
            raise fail("OUTPUT_INVALID", "Tool result did not match the output schema.")
    if schema.get("additionalProperties") is False:
        unexpected = [key for key in payload if key not in properties]
        if unexpected:
            raise fail("OUTPUT_INVALID", "Tool result did not match the output schema.")
    for key, spec in properties.items():
        if key not in payload or payload[key] is None:
            continue
        _check_property(key, payload[key], spec)


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(is_secret_key(str(key)) or _contains_secret_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False


def _mcp_message(code: str) -> str:
    return _MCP_MESSAGES.get(code, "Tool execution failed.")
