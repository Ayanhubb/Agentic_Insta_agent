"""Versioned documents and structured business snapshots, scoped by tenant.

Live sources (brand, product, festival, account, trend, content rules) stay in
their current tables and are read through MCP. This store does not copy them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.platform.knowledge.audit import KnowledgeAuditEvent, KnowledgeAuditLog
from backend.platform.knowledge.catalog import VERSIONED_SOURCES, KnowledgeSource
from backend.platform.knowledge.errors import fail
from backend.platform.redaction import PATH_KEYS, public_payload
from models.errors import AppError
from services.asset_paths import require_tenant_id

_MAX_BODY_CHARS = 64 * 1024


@dataclass
class KnowledgeRecord:
    id: str
    tenant_id: str
    owner_id: str
    source: KnowledgeSource
    title: str
    body: dict[str, Any]
    version: int
    lifecycle: str

    def public(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "source": self.source.value,
            "title": self.title,
            "version": self.version,
            "lifecycle": self.lifecycle,
            "body": self.body,
        }
        if PATH_KEYS.intersection(payload):
            raise fail("INVALID_DEFINITION")
        return payload


class KnowledgeStore:
    def __init__(self, audit: KnowledgeAuditLog | None = None) -> None:
        self._audit = audit or KnowledgeAuditLog()
        self._versions: dict[tuple[str, int], KnowledgeRecord] = {}
        self._latest: dict[str, int] = {}
        self._order: dict[str, int] = {}
        self._clock = 0

    @property
    def audit(self) -> KnowledgeAuditLog:
        return self._audit

    def put(
        self,
        tenant_id: str,
        *,
        source: KnowledgeSource | str,
        title: str,
        body: dict[str, Any],
        agent_id: str | None = None,
    ) -> KnowledgeRecord:
        tenant = _tenant(tenant_id)
        parsed = _versioned(source)
        record = KnowledgeRecord(
            id=f"know_{uuid4().hex}",
            tenant_id=tenant,
            owner_id=tenant,
            source=parsed,
            title=_title(title),
            body=_body(body),
            version=1,
            lifecycle="active",
        )
        self._save(record, agent_id, action="put")
        return record

    def revise(
        self,
        tenant_id: str,
        record_id: str,
        body: dict[str, Any],
        *,
        title: str | None = None,
        agent_id: str | None = None,
    ) -> KnowledgeRecord:
        current = self.get(tenant_id, record_id)
        if current is None:
            raise fail("NOT_FOUND")
        record = KnowledgeRecord(
            id=current.id,
            tenant_id=current.tenant_id,
            owner_id=current.owner_id,
            source=current.source,
            title=_title(title) if title is not None else current.title,
            body=_body(body),
            version=current.version + 1,
            lifecycle="active",
        )
        self._save(record, agent_id, action="revise")
        return record

    def get(
        self,
        tenant_id: str,
        record_id: str,
        *,
        version: int | None = None,
        agent_id: str | None = None,
    ) -> KnowledgeRecord | None:
        tenant = _tenant(tenant_id)
        token = (record_id or "").strip()
        latest_number = self._latest.get(token)
        if latest_number is None:
            self._miss(tenant, agent_id, token or None, None)
            return None
        sample = self._versions.get((token, latest_number))
        if sample is None or sample.tenant_id != tenant or sample.owner_id != tenant:
            self._miss(tenant, agent_id, token, None)
            return None
        number = latest_number if version is None else version
        record = self._versions.get((token, number))
        if record is None or record.lifecycle != "active" or record.tenant_id != tenant:
            self._miss(tenant, agent_id, token, number)
            return None
        return record

    def latest(self, tenant_id: str, source: KnowledgeSource) -> KnowledgeRecord | None:
        tenant = _tenant(tenant_id)
        chosen: KnowledgeRecord | None = None
        for record_id, number in self._latest.items():
            record = self._versions.get((record_id, number))
            if record is None or record.lifecycle != "active":
                continue
            if record.tenant_id != tenant or record.source is not source:
                continue
            if chosen is None or self._order.get(record.id, 0) >= self._order.get(chosen.id, 0):
                chosen = record
        return chosen

    def delete(self, tenant_id: str, record_id: str, *, agent_id: str | None = None) -> None:
        tenant = _tenant(tenant_id)
        current = self.get(tenant, record_id, agent_id=agent_id)
        if current is None:
            raise fail("NOT_FOUND")
        removed = 0
        for key, record in list(self._versions.items()):
            if key[0] != current.id or record.tenant_id != tenant:
                continue
            record.lifecycle = "deleted"
            record.body = {}
            removed = record.version
        self._audit.record(
            KnowledgeAuditEvent(
                action="delete",
                tenant_id=tenant,
                agent_id=agent_id,
                source=current.source.value,
                record_id=current.id,
                version=removed,
                status="deleted",
                error_code=None,
            )
        )

    def _save(self, record: KnowledgeRecord, agent_id: str | None, *, action: str) -> None:
        self._versions[(record.id, record.version)] = record
        self._latest[record.id] = record.version
        self._clock += 1
        self._order[record.id] = self._clock
        self._audit.record(
            KnowledgeAuditEvent(
                action=action,
                tenant_id=record.tenant_id,
                agent_id=agent_id,
                source=record.source.value,
                record_id=record.id,
                version=record.version,
                status="ok",
                error_code=None,
            )
        )

    def _miss(self, tenant: str, agent_id: str | None, record_id: str | None, version: int | None) -> None:
        self._audit.record(
            KnowledgeAuditEvent(
                action="get",
                tenant_id=tenant,
                agent_id=agent_id,
                source=None,
                record_id=record_id,
                version=version,
                status="not_found",
                error_code="NOT_FOUND",
            )
        )


def _tenant(tenant_id: str) -> str:
    try:
        return require_tenant_id(tenant_id)
    except AppError as exc:
        raise fail("TENANT_REQUIRED") from exc


def _versioned(source: KnowledgeSource | str) -> KnowledgeSource:
    try:
        parsed = source if isinstance(source, KnowledgeSource) else KnowledgeSource(str(source))
    except ValueError as exc:
        raise fail("INVALID_SOURCE") from exc
    if parsed not in VERSIONED_SOURCES:
        raise fail("NOT_VERSIONED")
    return parsed


def _title(value: str) -> str:
    if not isinstance(value, str):
        raise fail("INVALID_DEFINITION")
    text = value.strip()
    if not text or len(text) > 160:
        raise fail("INVALID_DEFINITION")
    if "\x00" in text or ".." in text or "/" in text or "\\" in text:
        raise fail("INVALID_DEFINITION")
    return text


def _body(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise fail("INVALID_DEFINITION")
    cleaned = public_payload(value)
    if not isinstance(cleaned, dict):
        raise fail("INVALID_DEFINITION")
    encoded = str(cleaned)
    if len(encoded) > _MAX_BODY_CHARS:
        raise fail("INVALID_DEFINITION")
    return cleaned
