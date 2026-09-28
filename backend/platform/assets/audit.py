"""Audit lines for asset intake, resolution, deletion, and provider preparation.

The line names the tenant, the agent, the role, and the outcome. It does not
include bytes, storage keys, or another tenant's file name.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("backend.platform.assets")

AUDIT_EVENT = "platform.asset"


@dataclass(frozen=True, slots=True)
class AssetAuditEvent:
    action: str
    tenant_id: str | None
    agent_id: str | None
    asset_id: str | None
    role: str | None
    status: str
    error_code: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": AUDIT_EVENT,
            "action": self.action,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "asset_id": self.asset_id,
            "role": self.role,
            "status": self.status,
            "error_code": self.error_code,
        }


class AssetAuditLog:
    def __init__(self) -> None:
        self.events: list[AssetAuditEvent] = []

    def record(self, event: AssetAuditEvent) -> None:
        self.events.append(event)
        logger.info("%s", json.dumps(event.as_dict(), sort_keys=True))
