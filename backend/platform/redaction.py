"""Public payloads for knowledge and assets.

Filesystem locations and secret-shaped keys are removed before a model,
an audit line, or an execution packet can see them.
"""

from __future__ import annotations

from typing import Any

from backend.mcp.client import sanitize_payload
from backend.mcp.tenant_isolation import UNTRUSTED_TENANT_KEYS

PATH_KEYS = frozenset(
    {
        "storage_key",
        "storage_path",
        "path",
        "filesystem_path",
        "absolute_path",
        "local_path",
        "image_path",
        "password_hash",
    }
)
_TENANT_KEYS = frozenset(key.lower() for key in UNTRUSTED_TENANT_KEYS)


def public_payload(value: Any) -> Any:
    """Sanitize, then drop path keys ``sanitize_payload`` does not name."""
    cleaned = sanitize_payload(value)
    return _drop_paths(cleaned)


def _drop_paths(value: Any) -> Any:
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in PATH_KEYS or str(key).lower() in _TENANT_KEYS:
                continue
            kept[str(key)] = _drop_paths(item)
        return kept
    if isinstance(value, list):
        return [_drop_paths(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return None
    return value
