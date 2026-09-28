"""Tool definitions registered by server code. The model cannot supply one of these."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS, MCP_TOOL_ALLOWLIST
from backend.mcp.tenant_isolation import TenantContext, UNTRUSTED_TENANT_KEYS
from backend.platform.tools.errors import fail
from services.logging import is_secret_key

ToolHandler = Callable[[TenantContext, dict[str, Any]], Awaitable[dict[str, Any]]]

CLASSIFICATIONS = frozenset({"read", "write"})
PLANES = frozenset({"mcp_read", "service"})
READ_PERMISSIONS = frozenset({"read"})
WRITE_PERMISSIONS = frozenset({"write"})


@dataclass(frozen=True, slots=True)
class AuditRequirements:
    """Every execution is recorded. `required` cannot be turned off."""

    required: bool = True
    record_arguments: bool = True
    record_result_keys: bool = True


@dataclass(frozen=True, slots=True)
class Idempotency:
    """Whether a retry of this tool is safe. Read tools are idempotent."""

    idempotent: bool


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: frozenset[str]
    tenant_scope: str
    classification: str
    timeout_seconds: float
    idempotency: Idempotency
    audit: AuditRequirements
    handler: ToolHandler
    plane: str = "service"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise fail("INVALID_DEFINITION", "Tool name is required.")
        name = self.name.strip()
        object.__setattr__(self, "name", name)
        if name in INSTAGRAM_PUBLISHING_TOOLS:
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=name,
            )
        if not isinstance(self.version, str) or not self.version.strip():
            raise fail("INVALID_DEFINITION", "Tool version is required.", tool=name)
        object.__setattr__(self, "version", self.version.strip())
        if self.classification not in CLASSIFICATIONS:
            raise fail("INVALID_DEFINITION", "Tool classification must be read or write.", tool=name)
        if self.plane == "instagram_publish":
            raise fail(
                "TOOL_NOT_ALLOWED",
                "Instagram publishing tools are not available through the tool registry.",
                tool=name,
            )
        if self.plane not in PLANES:
            raise fail("INVALID_DEFINITION", "Tool plane is not registrable.", tool=name)
        on_mcp_allowlist = name in MCP_TOOL_ALLOWLIST
        if self.plane == "mcp_read" and not on_mcp_allowlist:
            raise fail("INVALID_DEFINITION", "MCP tools must be on the MCP allowlist.", tool=name)
        if on_mcp_allowlist and self.plane != "mcp_read":
            raise fail("INVALID_DEFINITION", "MCP allowlisted tools must use the MCP adapter.", tool=name)
        if self.plane == "mcp_read" and self.classification != "read":
            raise fail("INVALID_DEFINITION", "MCP tools must be read-only.", tool=name)
        if isinstance(self.permissions, str) or not isinstance(self.permissions, (set, frozenset, list, tuple)):
            raise fail("INVALID_DEFINITION", "Tool permissions do not match its classification.", tool=name)
        permissions = frozenset(self.permissions)
        expected = READ_PERMISSIONS if self.classification == "read" else WRITE_PERMISSIONS
        if permissions != expected:
            raise fail("INVALID_DEFINITION", "Tool permissions do not match its classification.", tool=name)
        object.__setattr__(self, "permissions", permissions)
        if self.tenant_scope != "tenant":
            raise fail("INVALID_DEFINITION", "Tool tenant scope must be tenant.", tool=name)
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool):
            raise fail("INVALID_DEFINITION", "Tool timeout must be positive.", tool=name)
        if float(self.timeout_seconds) <= 0:
            raise fail("INVALID_DEFINITION", "Tool timeout must be positive.", tool=name)
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))
        if not isinstance(self.idempotency, Idempotency) or not isinstance(self.idempotency.idempotent, bool):
            raise fail("INVALID_DEFINITION", "Tool idempotency must be declared.", tool=name)
        if self.classification == "read" and not self.idempotency.idempotent:
            raise fail("INVALID_DEFINITION", "Read tools must be idempotent.", tool=name)
        if not isinstance(self.audit, AuditRequirements) or self.audit.required is not True:
            raise fail("INVALID_DEFINITION", "Tool audit is required.", tool=name)
        object.__setattr__(self, "input_schema", _schema_copy(self.input_schema, name, output=False))
        object.__setattr__(self, "output_schema", _schema_copy(self.output_schema, name, output=True))
        if not callable(self.handler):
            raise fail("INVALID_DEFINITION", "Tool handler is required.", tool=name)

    def catalog_entry(self) -> dict[str, Any]:
        """Public description. The handler is not included."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permissions": sorted(self.permissions),
            "tenant_scope": self.tenant_scope,
            "classification": self.classification,
            "timeout_seconds": self.timeout_seconds,
            "idempotency": {"idempotent": self.idempotency.idempotent},
            "audit": {
                "required": self.audit.required,
                "record_arguments": self.audit.record_arguments,
                "record_result_keys": self.audit.record_result_keys,
            },
            "plane": self.plane,
        }


def _schema_copy(schema: dict[str, Any], name: str, *, output: bool) -> dict[str, Any]:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        kind = "output" if output else "input"
        raise fail("INVALID_DEFINITION", f"Tool {kind} schema must be an object.", tool=name)
    if not output and schema.get("additionalProperties") is not False:
        raise fail("INVALID_DEFINITION", "Tool input schema must reject additional properties.", tool=name)
    copied = copy.deepcopy(schema)
    properties = copied.get("properties") or {}
    if not isinstance(properties, dict):
        raise fail("INVALID_DEFINITION", "Tool schema properties are invalid.", tool=name)
    for key in properties:
        if key in UNTRUSTED_TENANT_KEYS:
            raise fail("INVALID_DEFINITION", "Tool schema must not include tenant identifiers.", tool=name)
        if is_secret_key(str(key)):
            raise fail("INVALID_DEFINITION", "Tool schema must not include secret fields.", tool=name)
    if output and copied.get("additionalProperties") is False:
        properties = dict(properties)
        properties.setdefault("tenant_id", {"type": "string"})
        copied["properties"] = properties
    return copied
