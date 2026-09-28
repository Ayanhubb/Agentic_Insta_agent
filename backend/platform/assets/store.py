"""Tenant-owned asset records.

File bytes that belong to the studio catalog stay in ``AssetCatalog``. This
store classifies them and can also hold agent-specific files under the same
path rules. It does not open another tenant's row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.platform.assets.audit import AssetAuditEvent, AssetAuditLog
from backend.platform.assets.catalog import (
    ROLE_CAPABILITY,
    STORED_ROLE_MAP,
    AssetCapability,
    AssetRole,
    is_reference_eligible,
)
from backend.platform.assets.errors import fail
from backend.platform.assets.validation import (
    as_asset_error,
    catalog_limits,
    validate_upload,
)
from backend.platform.redaction import PATH_KEYS, public_payload
from models.errors import AppError
from services.asset_paths import AssetPaths, require_tenant_id
from tools.image_validator import ImageLimits

_DEFAULT_LIMITS = catalog_limits(8 * 1024 * 1024, 8192)


@dataclass
class AssetRecord:
    id: str
    tenant_id: str
    owner_id: str
    capability: AssetCapability
    role: AssetRole
    stored_role: str
    filename: str
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    lifecycle: str
    revision: int
    sequence: int
    reference_eligible: bool
    metadata: dict[str, Any]
    media_url: str | None
    origin: str
    created_at: datetime
    _storage_key: str | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "capability": self.capability.value,
            "role": self.role.value,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "lifecycle": self.lifecycle,
            "revision": self.revision,
            "reference_eligible": self.reference_eligible,
            "metadata": dict(self.metadata),
            "media_url": self.media_url,
        }
        if PATH_KEYS.intersection(payload):
            raise fail("INVALID_PATH")
        return payload


class AssetStore:
    def __init__(
        self,
        *,
        limits: ImageLimits | None = None,
        media_root: Path | None = None,
        audit: AssetAuditLog | None = None,
    ) -> None:
        self._limits = limits or _DEFAULT_LIMITS
        self._media_root = None if media_root is None else Path(media_root)
        self._audit = audit or AssetAuditLog()
        self._records: dict[str, AssetRecord] = {}
        self._bytes: dict[str, bytes] = {}
        self._order = 0

    @property
    def audit(self) -> AssetAuditLog:
        return self._audit

    def put(
        self,
        tenant_id: str,
        *,
        filename: str | None,
        data: bytes,
        role: AssetRole | str,
        claimed_mime: str | None = None,
        metadata: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> AssetRecord:
        tenant = _tenant(tenant_id)
        parsed = _role(role)
        checked = validate_upload(
            data,
            role=parsed,
            filename=filename,
            claimed_mime=claimed_mime,
            limits=self._limits,
        )
        meta = _metadata(metadata)
        storage_key: str | None = None
        if self._media_root is not None:
            paths = AssetPaths(self._media_root)
            try:
                storage_key, path = paths.allocate(tenant, checked.scope, checked.mime_type)
                paths.write_bytes(path, data)
            except AppError as exc:
                raise fail("INVALID_PATH") from exc
        record = self._new_record(
            tenant=tenant,
            asset_id=f"asset_{uuid4().hex}",
            role=parsed,
            stored_role=checked.stored_role,
            filename=checked.filename,
            mime_type=checked.mime_type,
            size_bytes=len(data),
            width=checked.width,
            height=checked.height,
            metadata=meta,
            media_url=None,
            origin="platform",
            storage_key=storage_key,
            reference_eligible=checked.reference_eligible,
        )
        self._records[record.id] = record
        self._bytes[record.id] = data
        self._wrote("put", tenant, agent_id, record)
        return record

    def upload_through_catalog(self, catalog: Any, tenant_id: str, **kwargs: Any) -> AssetRecord:
        """Store a file with the existing catalog API, then classify it here."""
        tenant = _tenant(tenant_id)
        try:
            payload = catalog.upload(tenant, **kwargs)
        except AppError as exc:
            raise as_asset_error(exc) from exc
        asset_id = str(payload.get("id") or "")
        return self.index_catalog_asset(catalog, tenant, asset_id)

    def index_catalog_asset(self, catalog: Any, tenant_id: str, asset_id: str) -> AssetRecord:
        """Project an asset the catalog already returned for this tenant."""
        tenant = _tenant(tenant_id)
        try:
            public = catalog.get_asset(tenant, asset_id)
        except AppError as exc:
            self._miss("index", tenant, None, asset_id, None)
            raise fail("NOT_FOUND") from exc
        stored = str(public.get("role") or "")
        role = STORED_ROLE_MAP.get(stored)
        if role is None:
            raise fail("INVALID_DEFINITION")
        record = self._new_record(
            tenant=tenant,
            asset_id=str(public["id"]),
            role=role,
            stored_role=stored,
            filename=str(public.get("filename") or "file"),
            mime_type=str(public.get("mime_type") or ""),
            size_bytes=int(public.get("size_bytes") or 0),
            width=public.get("width"),
            height=public.get("height"),
            metadata={},
            media_url=public.get("media_url"),
            origin="catalog",
            storage_key=None,
            reference_eligible=is_reference_eligible(role, str(public.get("mime_type") or ""), stored),
        )
        self._records[record.id] = record
        self._wrote("index", tenant, None, record)
        return record

    def get(self, tenant_id: str, asset_id: str) -> AssetRecord | None:
        tenant = _tenant(tenant_id)
        return self._owned(tenant, asset_id, action="get", agent_id=None)

    def require(self, tenant_id: str, asset_id: str, *, agent_id: str | None = None) -> AssetRecord:
        tenant = _tenant(tenant_id)
        record = self._owned(tenant, asset_id, action="get", agent_id=agent_id)
        if record is None:
            raise fail("NOT_FOUND")
        return record

    def latest(self, tenant_id: str, role: AssetRole) -> AssetRecord | None:
        tenant = _tenant(tenant_id)
        chosen: AssetRecord | None = None
        for record in self._records.values():
            if record.lifecycle != "active" or record.tenant_id != tenant or record.role is not role:
                continue
            if chosen is None or record.sequence >= chosen.sequence:
                chosen = record
        return chosen

    def list_for_tenant(
        self,
        tenant_id: str,
        *,
        role: AssetRole | None = None,
        capability: AssetCapability | None = None,
    ) -> list[AssetRecord]:
        tenant = _tenant(tenant_id)
        rows = [
            record
            for record in self._records.values()
            if record.lifecycle == "active" and record.tenant_id == tenant and record.owner_id == tenant
        ]
        if role is not None:
            rows = [record for record in rows if record.role is role]
        if capability is not None:
            rows = [record for record in rows if record.capability is capability]
        rows.sort(key=lambda record: record.sequence)
        return rows

    def update_metadata(
        self,
        tenant_id: str,
        asset_id: str,
        metadata: dict[str, Any],
        *,
        agent_id: str | None = None,
    ) -> AssetRecord:
        record = self.require(tenant_id, asset_id, agent_id=agent_id)
        record.metadata = _metadata(metadata)
        record.revision += 1
        self._wrote("revise_metadata", record.tenant_id, agent_id, record)
        return record

    def delete(
        self,
        tenant_id: str,
        asset_id: str,
        *,
        catalog: Any | None = None,
        agent_id: str | None = None,
    ) -> None:
        record = self.require(tenant_id, asset_id, agent_id=agent_id)
        if record.origin == "catalog":
            if catalog is None:
                raise fail("INVALID_DEFINITION")
            try:
                catalog.delete_asset(record.tenant_id, record.id)
            except AppError as exc:
                raise fail("NOT_FOUND") from exc
        elif record._storage_key and self._media_root is not None:
            try:
                path = self.resolve_owned_key(record.tenant_id, record._storage_key)
                AssetPaths(self._media_root).remove(path)
            except AppError as exc:
                raise fail("INVALID_PATH") from exc
        record.lifecycle = "deleted"
        record._storage_key = None
        self._bytes.pop(record.id, None)
        self._audit.record(
            AssetAuditEvent(
                action="delete",
                tenant_id=record.tenant_id,
                agent_id=agent_id,
                asset_id=record.id,
                role=record.role.value,
                status="deleted",
                error_code=None,
            )
        )

    def read_bytes(self, tenant_id: str, asset_id: str, *, catalog: Any | None = None) -> bytes | None:
        record = self._owned(_tenant(tenant_id), asset_id, action="read", agent_id=None)
        if record is None:
            return None
        cached = self._bytes.get(record.id)
        if cached is not None:
            return cached
        if record.origin == "catalog" and catalog is not None:
            try:
                path, _mime, _name = catalog.open_media(record.tenant_id, record.id)
                return path.read_bytes()
            except (AppError, OSError):
                return None
        if record._storage_key and self._media_root is not None:
            try:
                path = self.resolve_owned_key(record.tenant_id, record._storage_key)
            except AppError:
                return None
            if not path.is_file():
                return None
            return path.read_bytes()
        return None

    def resolve_owned_key(self, tenant_id: str, storage_key: str) -> Path:
        """Resolve a key this store created. Caller-supplied traversal is rejected."""
        if self._media_root is None:
            raise fail("INVALID_PATH")
        tenant = _tenant(tenant_id)
        try:
            return AssetPaths(self._media_root).resolve_storage_key(tenant, storage_key)
        except AppError as exc:
            raise fail("INVALID_PATH") from exc

    def _new_record(
        self,
        *,
        tenant: str,
        asset_id: str,
        role: AssetRole,
        stored_role: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        width: int | None,
        height: int | None,
        metadata: dict[str, Any],
        media_url: str | None,
        origin: str,
        storage_key: str | None,
        reference_eligible: bool,
    ) -> AssetRecord:
        self._order += 1
        created = datetime.now(timezone.utc)
        cleaned = public_payload(metadata)
        if not isinstance(cleaned, dict):
            cleaned = {}
        return AssetRecord(
            id=asset_id,
            tenant_id=tenant,
            owner_id=tenant,
            capability=ROLE_CAPABILITY[role],
            role=role,
            stored_role=stored_role,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            width=width,
            height=height,
            lifecycle="active",
            revision=1,
            sequence=self._order,
            reference_eligible=reference_eligible,
            metadata=cleaned,
            media_url=media_url if isinstance(media_url, str) else None,
            origin=origin,
            created_at=created,
            _storage_key=storage_key,
        )

    def _owned(
        self,
        tenant: str,
        asset_id: str,
        *,
        action: str,
        agent_id: str | None,
    ) -> AssetRecord | None:
        token = (asset_id or "").strip()
        record = self._records.get(token)
        if record is None or record.lifecycle != "active":
            self._miss(action, tenant, agent_id, token or None, None)
            return None
        if record.tenant_id != tenant or record.owner_id != tenant:
            self._miss(action, tenant, agent_id, token, None)
            return None
        return record

    def _wrote(self, action: str, tenant: str, agent_id: str | None, record: AssetRecord) -> None:
        self._audit.record(
            AssetAuditEvent(
                action=action,
                tenant_id=tenant,
                agent_id=agent_id,
                asset_id=record.id,
                role=record.role.value,
                status="ok",
                error_code=None,
            )
        )

    def _miss(
        self,
        action: str,
        tenant: str,
        agent_id: str | None,
        asset_id: str | None,
        role: str | None,
    ) -> None:
        self._audit.record(
            AssetAuditEvent(
                action=action,
                tenant_id=tenant,
                agent_id=agent_id,
                asset_id=asset_id,
                role=role,
                status="not_found",
                error_code="NOT_FOUND",
            )
        )


def _tenant(tenant_id: str) -> str:
    try:
        return require_tenant_id(tenant_id)
    except AppError as exc:
        raise fail("TENANT_REQUIRED") from exc


def _role(role: AssetRole | str) -> AssetRole:
    if isinstance(role, AssetRole):
        return role
    try:
        return AssetRole(str(role))
    except ValueError as exc:
        raise fail("INVALID_DEFINITION") from exc


def _metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise fail("INVALID_DEFINITION")
    cleaned = public_payload(value)
    if not isinstance(cleaned, dict) or len(cleaned) > 20:
        raise fail("INVALID_DEFINITION")
    kept: dict[str, Any] = {}
    for key, item in cleaned.items():
        if not isinstance(item, (str, int, float, bool)) and item is not None:
            raise fail("INVALID_DEFINITION")
        if isinstance(item, str) and len(item) > 200:
            raise fail("INVALID_DEFINITION")
        kept[str(key)[:64]] = item
    return kept
