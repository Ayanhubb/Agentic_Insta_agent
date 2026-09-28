"""Load one granted knowledge source for an execution.

The tenant id is taken from the execution context. A tenant id in the payload
is not consulted.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from backend.mcp.tenant_isolation import TenantContext, trusted_tenant
from backend.platform.knowledge.binding import KnowledgeBinding
from backend.platform.knowledge.catalog import (
    SOURCE_TOOLS,
    VERSIONED_SOURCES,
    KnowledgeSource,
)
from backend.platform.knowledge.errors import fail
from backend.platform.knowledge.store import KnowledgeStore
from backend.platform.redaction import public_payload

Invoker = Callable[[str, dict[str, Any], TenantContext], Awaitable[Mapping[str, Any]]]


class PlatformKnowledgeService:
    def __init__(
        self,
        binding: KnowledgeBinding,
        store: KnowledgeStore | None = None,
        invoker: Invoker | None = None,
    ) -> None:
        self._binding = binding
        self._store = store or KnowledgeStore()
        self._invoker = invoker

    @property
    def binding(self) -> KnowledgeBinding:
        return self._binding

    async def load(self, ctx: Any, source_name: str) -> Mapping[str, Any]:
        granted = frozenset(getattr(ctx.snapshot, "knowledge_sources", ()))
        return await self.load_for_tenant(ctx.tenant_id, source_name, granted=granted)

    async def load_for_tenant(
        self,
        tenant_id: str,
        source_name: str,
        *,
        granted: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        source = self._granted(source_name, granted)
        live: dict[str, Any] | None = None
        if source in SOURCE_TOOLS and self._invoker is not None:
            tool = SOURCE_TOOLS[source]
            try:
                result = await self._invoker(tool, {}, trusted_tenant(tenant_id))
                live = dict(result) if isinstance(result, Mapping) else {"found": False}
            except Exception:  # noqa: BLE001 - handler text must not enter the packet
                live = {"found": False}
        snapshot: dict[str, Any] | None = None
        if source in VERSIONED_SOURCES:
            record = self._store.latest(tenant_id, source)
            if record is not None:
                snapshot = record.public()
        if live is None and snapshot is None:
            return {"found": False, "source": source.value}
        found = bool(live and live.get("found")) or snapshot is not None
        payload: dict[str, Any] = {"found": found, "source": source.value}
        if live is not None:
            payload["live"] = live
        if snapshot is not None:
            payload["record"] = snapshot
        cleaned = public_payload(payload)
        return cleaned if isinstance(cleaned, dict) else {"found": False, "source": source.value}

    def _granted(self, source_name: str, granted: frozenset[str] | None) -> KnowledgeSource:
        try:
            source = KnowledgeSource(str(source_name))
        except ValueError as exc:
            raise fail("NOT_GRANTED") from exc
        if not self._binding.grants(source):
            raise fail("NOT_GRANTED")
        if granted is not None and source.value not in granted:
            raise fail("NOT_GRANTED")
        return source
