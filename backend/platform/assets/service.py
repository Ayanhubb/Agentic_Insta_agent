"""Resolve a granted role for one tenant. Ungranted roles are not opened."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.platform.assets.audit import AssetAuditEvent
from backend.platform.assets.binding import AssetBinding
from backend.platform.assets.catalog import AssetRole, ProviderTarget
from backend.platform.assets.errors import fail
from backend.platform.assets.prepare import PreparedAsset, prepare_record
from backend.platform.assets.store import AssetRecord, AssetStore


class PlatformAssetService:
    """Asset port for an agent version. The tenant comes from the caller, not the payload."""

    def __init__(self, store: AssetStore, binding: AssetBinding, *, catalog: Any | None = None) -> None:
        self._store = store
        self._binding = binding
        self._catalog = catalog

    @property
    def binding(self) -> AssetBinding:
        return self._binding

    def resolve_for_tenant(
        self,
        tenant_id: str,
        role: AssetRole | str,
        asset_id: str | None = None,
        *,
        granted: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        parsed = self._granted(role, granted)
        record = self._select(tenant_id, parsed, asset_id)
        if record is None:
            return {"found": False, "role": parsed.value, "asset": None}
        if record.role is not parsed:
            return {"found": False, "role": parsed.value, "asset": None}
        return {"found": True, "role": parsed.value, "asset": record.public()}

    def prepare_for_tenant(
        self,
        tenant_id: str,
        role: AssetRole | str,
        target: ProviderTarget | str,
        asset_id: str | None = None,
        *,
        granted: frozenset[str] | None = None,
    ) -> PreparedAsset:
        parsed = self._granted(role, granted)
        provider = _target(target)
        record = self._select(tenant_id, parsed, asset_id)
        if record is None or record.role is not parsed:
            missed = PreparedAsset(
                False,
                {"found": False, "eligible": False, "provider": provider.value, "role": parsed.value, "asset": None},
                None,
            )
            self._audit("prepare", tenant_id, None, parsed.value, "not_found")
            return missed
        data = None
        if provider is ProviderTarget.IMAGE_EDIT and record.reference_eligible:
            data = self._store.read_bytes(tenant_id, record.id, catalog=self._catalog)
        prepared = prepare_record(record, provider, data)
        self._audit(
            "prepare",
            tenant_id,
            record.id,
            parsed.value,
            "ok" if prepared.eligible or provider is ProviderTarget.TEXT else "ineligible",
        )
        return prepared

    async def resolve(
        self,
        ctx: Any,
        role: str,
        asset_id: str | None,
    ) -> Mapping[str, Any]:
        """Runtime asset port. Payload tenant ids are ignored."""
        granted = frozenset(getattr(ctx.snapshot, "asset_roles", ()))
        return self.resolve_for_tenant(ctx.tenant_id, role, asset_id, granted=granted)

    def _select(self, tenant_id: str, role: AssetRole, asset_id: str | None) -> AssetRecord | None:
        if asset_id:
            return self._store.get(tenant_id, asset_id)
        return self._store.latest(tenant_id, role)

    def _granted(self, role: AssetRole | str, granted: frozenset[str] | None) -> AssetRole:
        try:
            parsed = role if isinstance(role, AssetRole) else AssetRole(str(role))
        except ValueError as exc:
            raise fail("NOT_GRANTED") from exc
        if not self._binding.grants(parsed):
            raise fail("NOT_GRANTED")
        if granted is not None and parsed.value not in granted:
            raise fail("NOT_GRANTED")
        return parsed

    def _audit(self, action: str, tenant_id: str, asset_id: str | None, role: str, status: str) -> None:
        self._store.audit.record(
            AssetAuditEvent(
                action=action,
                tenant_id=tenant_id,
                agent_id=self._binding.agent_id,
                asset_id=asset_id,
                role=role,
                status=status,
                error_code=None if status != "not_found" else "NOT_FOUND",
            )
        )


def _target(target: ProviderTarget | str) -> ProviderTarget:
    if isinstance(target, ProviderTarget):
        return target
    try:
        return ProviderTarget(str(target))
    except ValueError as exc:
        raise fail("INVALID_DEFINITION") from exc
