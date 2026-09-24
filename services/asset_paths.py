"""Tenant-scoped paths for brand, product, and campaign files.

User-supplied filesystem paths are never used. Stored names are generated here.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from models.errors import AppError, ErrorCode
from services.media_paths import atomic_write, is_within, resolve_contained

ASSET_SCOPES = frozenset({"brand", "product", "campaign"})
SCOPE_DIRECTORIES = {
    "brand": "brand",
    "product": "products",
    "campaign": "campaigns",
}
EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/svg+xml": "svg",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "txt",
}
INTERNAL_NAME_RE = re.compile(r"^asset_[0-9a-f]{32}\.(png|jpg|svg|pdf|txt)$")
TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
UPLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,179}$")


def _reject(message: str = "Invalid storage path.") -> None:
    raise AppError(ErrorCode.INVALID_REQUEST, message, http_status=400)


def require_tenant_id(user_id: str) -> str:
    token = (user_id or "").strip()
    if not token or not TENANT_RE.fullmatch(token) or ".." in token or "/" in token or "\\" in token:
        _reject("Invalid user id.")
    return token


def assert_upload_filename(filename: str | None) -> str:
    raw = filename or ""
    if not raw or len(raw) > 180:
        _reject("The file name is not allowed.")
    if any(ord(char) < 32 for char in raw) or "\x00" in raw:
        _reject("The file name is not allowed.")
    if ".." in raw or "/" in raw or "\\" in raw or ":" in raw:
        _reject("The file name is not allowed.")
    if not UPLOAD_NAME_RE.fullmatch(raw):
        _reject("The file name is not allowed.")
    return raw


class AssetPaths:
    def __init__(self, media_root: Path) -> None:
        self.root = Path(media_root).resolve()
        self.assets_root = resolve_contained(self.root, "assets")
        self.assets_root.mkdir(parents=True, exist_ok=True)

    def allocate(self, user_id: str, scope: str, mime_type: str) -> tuple[str, Path]:
        tenant = require_tenant_id(user_id)
        if scope not in ASSET_SCOPES:
            _reject("Invalid asset scope.")
        extension = EXTENSIONS.get(mime_type)
        if extension is None:
            raise AppError(ErrorCode.UNSUPPORTED_FORMAT, "This file type is not supported.", http_status=400)
        folder = SCOPE_DIRECTORIES[scope]
        filename = f"asset_{uuid4().hex}.{extension}"
        storage_key = f"assets/{tenant}/{folder}/{filename}"
        path = self.resolve_storage_key(tenant, storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return storage_key, path

    def resolve_storage_key(self, user_id: str, storage_key: str) -> Path:
        tenant = require_tenant_id(user_id)
        if not storage_key or "\\" in storage_key or storage_key.startswith(("/", "\\")):
            _reject()
        parts = storage_key.split("/")
        if len(parts) != 4 or parts[0] != "assets" or parts[1] != tenant:
            _reject()
        if parts[2] not in set(SCOPE_DIRECTORIES.values()):
            _reject()
        if not INTERNAL_NAME_RE.fullmatch(parts[3]) or ".." in parts[3]:
            _reject()
        path = resolve_contained(self.root, *parts)
        if not is_within(path, self.assets_root):
            _reject()
        return path

    def write_bytes(self, path: Path, data: bytes) -> Path:
        resolved = Path(path).resolve()
        if not is_within(resolved, self.assets_root):
            _reject()
        return atomic_write(resolved, data)

    def remove(self, path: Path) -> None:
        resolved = Path(path).resolve()
        if not is_within(resolved, self.assets_root):
            _reject()
        resolved.unlink(missing_ok=True)
