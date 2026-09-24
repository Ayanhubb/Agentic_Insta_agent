"""MCP client: discovery and controlled invocation."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from backend.mcp.errors import MCPError, MCPTimeout
from backend.mcp.registry import MCPRegistry, validate_arguments
from backend.mcp.tenant_isolation import (
    TenantContext,
    require_trusted_tenant,
    strip_model_tenant_arguments,
)
from services.logging import is_secret_key, redact_text

logger = logging.getLogger("backend.mcp")

_PATH_KEYS = frozenset({"storage_path", "image_path", "local_path", "password_hash"})
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    data: dict[str, Any]


def sanitize_payload(value: Any) -> Any:
    """Remove secret-bearing keys and filesystem paths, then redact secret-shaped text."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _PATH_KEYS or is_secret_key(normalized):
                continue
            cleaned[str(key)] = sanitize_payload(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _audit(
    *,
    tool: str,
    tenant_id: str | None,
    status: str,
    duration_ms: int,
    error_code: str | None,
    arguments: Any,
    result_keys: list[str] | None,
) -> None:
    payload = sanitize_payload(
        {
            "event": "mcp.tool.execute",
            "tool": tool,
            "tenant_id": tenant_id,
            "status": status,
            "duration_ms": duration_ms,
            "error_code": error_code,
            "arguments": arguments if isinstance(arguments, dict) else {"omitted": True},
            "result_keys": result_keys or [],
        }
    )
    logger.info("%s", json.dumps(payload, default=str, sort_keys=True))


class MCPClient:
    """Discovers allowlisted tools and invokes them under a trusted tenant."""

    def __init__(self, registry: MCPRegistry, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.registry = registry
        self.timeout_seconds = timeout_seconds

    def discover(self) -> list[dict[str, Any]]:
        return self.registry.discover()

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        tenant: TenantContext | None,
        *,
        timeout: float | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        status = "error"
        error_code: str | None = None
        tenant_id: str | None = None
        result_keys: list[str] | None = None
        try:
            bound = require_trusted_tenant(tenant)
            tenant_id = bound.tenant_id
            safe_arguments, _discarded = strip_model_tenant_arguments(arguments)
            tool = self.registry.get(name)
            validated = validate_arguments(tool.input_schema, safe_arguments)
            try:
                raw = await asyncio.wait_for(
                    tool.handler(bound, validated),
                    timeout=self.timeout_seconds if timeout is None else timeout,
                )
            except TimeoutError as exc:
                raise MCPTimeout("Tool execution timed out.") from exc
            if not isinstance(raw, dict):
                raise MCPError("Tool returned an invalid result.")
            data = sanitize_payload(raw)
            if not isinstance(data, dict):
                raise MCPError("Tool returned an invalid result.")
            data["tenant_id"] = bound.tenant_id
            result_keys = sorted(data.keys())
            status = "ok"
            return ToolResult(name=tool.name, data=data)
        except MCPError as exc:
            error_code = exc.code
            raise
        except Exception:
            error_code = "INTERNAL_ERROR"
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            _audit(
                tool=name if isinstance(name, str) else "",
                tenant_id=tenant_id,
                status=status,
                duration_ms=duration_ms,
                error_code=error_code,
                arguments=arguments,
                result_keys=result_keys,
            )
