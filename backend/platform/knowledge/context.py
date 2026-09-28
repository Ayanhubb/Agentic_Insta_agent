"""Build the MCP context an execution may see.

Only granted knowledge sources and granted asset roles are read. A company
logo tool is not called unless that role is bound. Tenant ids in the argument
object are discarded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from backend.mcp.tenant_isolation import (
    TenantContext,
    require_trusted_tenant,
    strip_model_tenant_arguments,
)
from backend.platform.assets.binding import AssetBinding
from backend.platform.assets.catalog import AssetRole
from backend.platform.assets.store import AssetStore
from backend.platform.knowledge.binding import KnowledgeBinding
from backend.platform.knowledge.catalog import (
    ASSET_TOOLS,
    SOURCE_TOOLS,
    VERSIONED_SOURCES,
    KnowledgeSource,
)
from backend.platform.knowledge.store import KnowledgeStore
from backend.platform.redaction import public_payload

Invoker = Callable[[str, dict[str, Any], TenantContext], Awaitable[Mapping[str, Any]]]

class McpContextAssembler:
    def __init__(
        self,
        invoker: Invoker | None = None,
        *,
        knowledge: KnowledgeStore | None = None,
        assets: AssetStore | None = None,
    ) -> None:
        self._invoker = invoker
        self._knowledge = knowledge
        self._assets = assets

    async def build(
        self,
        tenant: TenantContext,
        knowledge_binding: KnowledgeBinding,
        asset_binding: AssetBinding,
        *,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        bound = require_trusted_tenant(tenant)
        safe_arguments, _discarded = strip_model_tenant_arguments(dict(arguments or {}))
        packet: dict[str, Any] = {
            "tenant_id": bound.tenant_id,
            "agent_id": knowledge_binding.agent_id,
            "knowledge": {},
            "assets": {},
        }
        for source in sorted(knowledge_binding.sources, key=lambda item: item.value):
            packet["knowledge"][source.value] = await self._knowledge_section(bound, source, safe_arguments)
        for role in sorted(asset_binding.roles, key=lambda item: item.value):
            packet["assets"][role.value] = await self._asset_section(bound, role, safe_arguments)
        cleaned = public_payload(packet)
        if not isinstance(cleaned, dict):
            return {"tenant_id": bound.tenant_id}
        cleaned["tenant_id"] = bound.tenant_id
        return cleaned

    async def _knowledge_section(
        self,
        tenant: TenantContext,
        source: KnowledgeSource,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        live = None
        tool = SOURCE_TOOLS.get(source)
        if tool is not None and self._invoker is not None:
            tool_name, tool_arguments = _knowledge_call(source, tool, arguments)
            live = await self._call(tool_name, tool_arguments, tenant)
        snapshot = None
        if source in VERSIONED_SOURCES and self._knowledge is not None:
            record = self._knowledge.latest(tenant.tenant_id, source)
            snapshot = {"found": True, "record": record.public()} if record is not None else {"found": False, "record": None}
        found = _found(live) or _found(snapshot)
        section: dict[str, Any] = {"found": found, "source": source.value}
        if live is not None:
            section["live"] = live
        if snapshot is not None:
            section["snapshot"] = snapshot
        return section

    async def _asset_section(
        self,
        tenant: TenantContext,
        role: AssetRole,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        tool = ASSET_TOOLS.get(role.value)
        if tool is not None and self._invoker is not None and _should_call_asset(role, arguments):
            payload = await self._call(tool, _asset_arguments(role, arguments), tenant)
            return {"found": _found(payload), "role": role.value, "live": payload}
        if self._assets is None:
            return {"found": False, "role": role.value, "asset": None}
        record = self._assets.latest(tenant.tenant_id, role)
        if record is None:
            return {"found": False, "role": role.value, "asset": None}
        return {"found": True, "role": role.value, "asset": record.public()}

    async def _call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tenant: TenantContext,
    ) -> dict[str, Any]:
        if self._invoker is None:
            return {"found": False}
        try:
            result = await self._invoker(tool_name, arguments, tenant)
        except Exception:  # noqa: BLE001 - handler text must not enter the packet
            return {"found": False}
        cleaned = public_payload(dict(result) if isinstance(result, Mapping) else {"found": False})
        return cleaned if isinstance(cleaned, dict) else {"found": False}


def _knowledge_call(
    source: KnowledgeSource,
    tool: str,
    arguments: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    if source is KnowledgeSource.PRODUCT and arguments.get("name"):
        return "get_product", {"name": arguments["name"]}
    if source is KnowledgeSource.FESTIVAL and arguments.get("festival_name"):
        return "get_festival_details", {"festival_name": arguments["festival_name"]}
    return tool, {}


def _should_call_asset(role: AssetRole, arguments: Mapping[str, Any]) -> bool:
    if role is not AssetRole.PRODUCT_IMAGE:
        return True
    return any(arguments.get(key) for key in ("product", "product_id", "image_id"))


def _asset_arguments(role: AssetRole, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if role is not AssetRole.PRODUCT_IMAGE:
        return {}
    return {key: arguments[key] for key in ("product", "product_id", "image_id") if arguments.get(key)}


def _found(payload: Mapping[str, Any] | None) -> bool:
    return bool(payload and payload.get("found"))
