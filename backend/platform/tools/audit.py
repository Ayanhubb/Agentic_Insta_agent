"""Audit record for every tool execution, including failures."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from backend.mcp.client import sanitize_payload
from backend.mcp.tenant_isolation import UNTRUSTED_TENANT_KEYS

logger = logging.getLogger("backend.platform.tools")

AUDIT_EVENT = "platform.tool.execute"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event: str
    tool: str
    version: str | None
    agent_id: str | None
    tenant_id: str | None
    classification: str | None
    status: str
    error_code: str | None
    duration_ms: int
    arguments: dict[str, Any]
    result_keys: tuple[str, ...]
    idempotent: bool | None
    ignored_untrusted_tenant: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "tool": self.tool,
            "version": self.version,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "classification": self.classification,
            "status": self.status,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "arguments": self.arguments,
            "result_keys": list(self.result_keys),
            "idempotent": self.idempotent,
            "ignored_untrusted_tenant": self.ignored_untrusted_tenant,
        }


class AuditLog:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)
        logger.info("%s", json.dumps(event.as_dict(), default=str, sort_keys=True))


def arguments_for_audit(value: Any) -> dict[str, Any]:
    """Arguments that were eligible to run. Model tenant ids and secrets are omitted."""
    if not isinstance(value, dict):
        return {"omitted": True}
    cleaned = sanitize_payload(value)
    if not isinstance(cleaned, dict):
        return {"omitted": True}
    for key in list(cleaned):
        if key in UNTRUSTED_TENANT_KEYS:
            cleaned.pop(key, None)
    return cleaned
