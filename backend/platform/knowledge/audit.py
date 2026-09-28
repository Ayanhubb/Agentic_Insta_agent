"""Audit for knowledge loads. Bodies and other tenants' titles are omitted."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("backend.platform.knowledge")

AUDIT_EVENT = "platform.knowledge"


@dataclass(frozen=True, slots=True)
class KnowledgeAuditEvent:
    action: str
    tenant_id: str | None
    agent_id: str | None
    source: str | None
    record_id: str | None
    version: int | None
    status: str
    error_code: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": AUDIT_EVENT,
            "action": self.action,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "source": self.source,
            "record_id": self.record_id,
            "version": self.version,
            "status": self.status,
            "error_code": self.error_code,
        }


class KnowledgeAuditLog:
    def __init__(self) -> None:
        self.events: list[KnowledgeAuditEvent] = []

    def record(self, event: KnowledgeAuditEvent) -> None:
        self.events.append(event)
        logger.info("%s", json.dumps(event.as_dict(), default=str, sort_keys=True))
