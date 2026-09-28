"""Durable agents, immutable published versions, and executions.

The tenant id is the caller's user id, or ``system`` for the shared reference
definition. A snapshot stores provider and model names. It does not store keys.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import DurableAgent, DurableAgentExecution, DurableAgentVersion

_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|credential|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)", re.IGNORECASE)
_SNAPSHOT_KEYS = frozenset(
    {
        "workflow_key",
        "models",
        "tool_names",
        "knowledge_sources",
        "asset_roles",
        "approval_policy_id",
        "trigger_key",
        "timeout_seconds",
    }
)


class PlatformStoreError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Reject secrets and unknown keys. Return a copy safe to store."""

    if not isinstance(snapshot, dict):
        raise PlatformStoreError("INVALID_SNAPSHOT", "Agent version snapshot must be an object.")
    unknown = set(snapshot) - _SNAPSHOT_KEYS
    if unknown:
        raise PlatformStoreError("INVALID_SNAPSHOT", "Agent version snapshot has an unknown field.")
    if snapshot.get("workflow_key") != "instagram.reference":
        raise PlatformStoreError("INVALID_SNAPSHOT", "Workflow is not registered.")
    _reject_secrets(snapshot)
    models = snapshot.get("models") or []
    if not isinstance(models, list):
        raise PlatformStoreError("INVALID_SNAPSHOT", "Model configuration must be a list.")
    cleaned_models: list[dict[str, str]] = []
    for item in models:
        if not isinstance(item, dict):
            raise PlatformStoreError("INVALID_SNAPSHOT", "Model configuration must be a list.")
        _reject_secrets(item)
        if "api_key" in item or "secret" in item:
            raise PlatformStoreError("INVALID_SNAPSHOT", "Model configuration cannot include a secret.")
        role = str(item.get("role") or "").strip()
        implementation = str(item.get("implementation") or "").strip()
        if not role or not implementation:
            raise PlatformStoreError("INVALID_SNAPSHOT", "Model configuration needs a role and implementation.")
        cleaned_models.append(
            {"role": role, "implementation": implementation, "model": str(item.get("model") or "")}
        )
    return {
        "workflow_key": "instagram.reference",
        "models": cleaned_models,
        "tool_names": sorted(str(item) for item in snapshot.get("tool_names") or []),
        "knowledge_sources": sorted(str(item) for item in snapshot.get("knowledge_sources") or []),
        "asset_roles": sorted(str(item) for item in snapshot.get("asset_roles") or []),
        "approval_policy_id": str(snapshot.get("approval_policy_id") or ""),
        "trigger_key": str(snapshot.get("trigger_key") or ""),
        "timeout_seconds": _timeout(snapshot.get("timeout_seconds")),
    }


def upsert_agent(
    session: Session,
    *,
    tenant_id: str,
    agent_key: str,
    display_name: str,
    description: str,
    owner: str,
    status: str,
    published_version_number: int | None,
) -> DurableAgent:
    row = _agent(session, tenant_id, agent_key)
    if row is None:
        row = DurableAgent(
            tenant_id=tenant_id,
            agent_key=agent_key,
            display_name=display_name,
            description=description,
            owner=owner,
            status=status,
            published_version_number=published_version_number,
        )
        session.add(row)
        session.flush()
        return row
    if row.tenant_id != tenant_id:
        raise PlatformStoreError("NOT_FOUND", "Agent was not found.")
    row.display_name = display_name
    row.description = description
    row.status = status
    row.published_version_number = published_version_number
    session.flush()
    return row


def save_version(
    session: Session,
    *,
    tenant_id: str,
    agent: DurableAgent,
    version_number: int,
    status: str,
    snapshot: dict[str, Any],
) -> DurableAgentVersion:
    if agent.tenant_id != tenant_id:
        raise PlatformStoreError("NOT_FOUND", "Agent was not found.")
    cleaned = validate_snapshot(snapshot)
    row = session.scalar(
        select(DurableAgentVersion).where(
            DurableAgentVersion.agent_id == agent.id,
            DurableAgentVersion.version_number == version_number,
        )
    )
    if row is None:
        row = DurableAgentVersion(
            tenant_id=tenant_id,
            agent_id=agent.id,
            version_number=version_number,
            status=status,
            snapshot=cleaned,
        )
        session.add(row)
        session.flush()
        return row
    if row.tenant_id != tenant_id:
        raise PlatformStoreError("NOT_FOUND", "Agent version was not found.")
    if row.status == "published":
        if row.snapshot != cleaned or status != "published":
            raise PlatformStoreError("IMMUTABLE_VERSION", "A published agent version cannot be changed.")
        return row
    row.snapshot = cleaned
    row.status = status
    session.flush()
    return row


def get_agent(session: Session, tenant_id: str, agent_key: str) -> DurableAgent | None:
    return _agent(session, tenant_id, agent_key)


def get_version(session: Session, tenant_id: str, agent_id: str, version_number: int) -> DurableAgentVersion | None:
    row = session.scalar(
        select(DurableAgentVersion).where(
            DurableAgentVersion.agent_id == agent_id,
            DurableAgentVersion.version_number == version_number,
            DurableAgentVersion.tenant_id == tenant_id,
        )
    )
    return row


def published_version(session: Session, agent: DurableAgent) -> DurableAgentVersion | None:
    if agent.published_version_number is None:
        return None
    return get_version(session, agent.tenant_id, agent.id, agent.published_version_number)


def record_execution(
    session: Session,
    *,
    execution_id: str,
    tenant_id: str,
    agent: DurableAgent,
    version: DurableAgentVersion,
    workflow_key: str,
    status: str,
    mode: str,
    started_at: datetime,
    completed_at: datetime | None,
    error_code: str | None,
    error_message: str | None,
    output: dict[str, Any] | None,
) -> DurableAgentExecution:
    if version.tenant_id != agent.tenant_id:
        raise PlatformStoreError("NOT_FOUND", "Agent version was not found.")
    row = DurableAgentExecution(
        id=execution_id,
        tenant_id=tenant_id,
        agent_id=agent.id,
        agent_version_id=version.id,
        workflow_key=workflow_key,
        status=status,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        error_code=error_code,
        error_message=None if error_message is None else error_message[:500],
        output=output,
    )
    session.add(row)
    session.flush()
    return row


def get_execution(session: Session, tenant_id: str, execution_id: str) -> DurableAgentExecution | None:
    return session.scalar(
        select(DurableAgentExecution).where(
            DurableAgentExecution.id == execution_id,
            DurableAgentExecution.tenant_id == tenant_id,
        )
    )


def _agent(session: Session, tenant_id: str, agent_key: str) -> DurableAgent | None:
    return session.scalar(
        select(DurableAgent).where(
            DurableAgent.tenant_id == tenant_id,
            DurableAgent.agent_key == agent_key,
        )
    )


def _timeout(value: object) -> int:
    if value in (None, ""):
        return 120
    try:
        timeout = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PlatformStoreError("INVALID_SNAPSHOT", "Timeout is not valid.") from exc
    if timeout < 1 or timeout > 3600:
        raise PlatformStoreError("INVALID_SNAPSHOT", "Timeout is not valid.")
    return timeout


def _reject_secrets(value: object, *, depth: int = 0) -> None:
    if depth > 6:
        raise PlatformStoreError("INVALID_SNAPSHOT", "Agent version snapshot is too deep.")
    if isinstance(value, dict):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                raise PlatformStoreError("INVALID_SNAPSHOT", "Agent version snapshot cannot include a secret.")
            _reject_secrets(item, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _reject_secrets(item, depth=depth + 1)
        return
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        raise PlatformStoreError("INVALID_SNAPSHOT", "Agent version snapshot cannot include a secret.")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reference_snapshot() -> dict[str, Any]:
    """Published configuration for the shared Instagram reference agent."""

    from backend.mcp.permissions import MCP_TOOL_ALLOWLIST
    from backend.platform.assets.catalog import INSTAGRAM_ASSET_ROLES
    from backend.platform.knowledge.catalog import INSTAGRAM_KNOWLEDGE_SOURCES

    return validate_snapshot(
        {
            "workflow_key": "instagram.reference",
            "models": [
                {"role": "content_planning", "implementation": "deepseek", "model": ""},
                {"role": "image_generation", "implementation": "openai", "model": ""},
                {"role": "vision_qa", "implementation": "deepseek", "model": ""},
            ],
            "tool_names": sorted(MCP_TOOL_ALLOWLIST),
            "knowledge_sources": sorted(item.value for item in INSTAGRAM_KNOWLEDGE_SOURCES),
            "asset_roles": sorted(item.value for item in INSTAGRAM_ASSET_ROLES),
            "approval_policy_id": "instagram.still_image",
            "trigger_key": "reference.run",
            "timeout_seconds": 120,
        }
    )


def ensure_reference_agent(session: Session) -> DurableAgent:
    """Create the shared reference agent once. Do not reactivate a paused row."""

    row = get_agent(session, "system", "instagram_marketing")
    if row is not None:
        if row.published_version_number is not None and published_version(session, row) is None:
            save_version(
                session,
                tenant_id="system",
                agent=row,
                version_number=row.published_version_number,
                status="published",
                snapshot=reference_snapshot(),
            )
        return row
    row = upsert_agent(
        session,
        tenant_id="system",
        agent_key="instagram_marketing",
        display_name="Instagram Marketing Agent",
        description="The Instagram studio is this agent's interface.",
        owner="system",
        status="active",
        published_version_number=1,
    )
    save_version(
        session,
        tenant_id="system",
        agent=row,
        version_number=1,
        status="published",
        snapshot=reference_snapshot(),
    )
    return row


def sync_agent_public(session: Session, *, tenant_id: str, agent: dict[str, Any]) -> DurableAgent:
    """Write the builder's public agent, including its version snapshots."""

    row = upsert_agent(
        session,
        tenant_id=tenant_id,
        agent_key=str(agent["key"]),
        display_name=str(agent["display_name"]),
        description=str(agent.get("description") or ""),
        owner=str(agent["owner"]),
        status=str(agent["status"]),
        published_version_number=agent.get("published_version_number"),
    )
    for version in agent.get("versions") or []:
        if not isinstance(version, dict):
            continue
        save_version(
            session,
            tenant_id=tenant_id,
            agent=row,
            version_number=int(version["version_number"]),
            status=str(version["status"]),
            snapshot={
                "workflow_key": version.get("workflow_key"),
                "models": version.get("models") or [],
                "tool_names": version.get("tool_names") or [],
                "knowledge_sources": version.get("knowledge_sources") or [],
                "asset_roles": version.get("asset_roles") or [],
                "approval_policy_id": version.get("approval_policy_id"),
                "trigger_key": version.get("trigger_key"),
                "timeout_seconds": version.get("timeout_seconds") or 120,
            },
        )
    return row
