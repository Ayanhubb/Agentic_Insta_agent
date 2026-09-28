"""Payload cleaning. Values are data. They are never executed."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_REJECTED_KEYS = frozenset(
    {
        "code",
        "script",
        "command",
        "shell",
        "exec",
        "eval",
        "handler",
        "handler_path",
        "import_path",
        "callable",
    }
)

_DROPPED_KEYS = frozenset(
    {
        "tenant_id",
        "user_id",
        "account_id",
        "execution_id",
        "correlation_id",
        "access_token",
        "token",
        "api_key",
        "password",
        "secret",
        "authorization",
        "refresh_token",
        "credentials",
        "approval_policy_id",
        "published_version_number",
    }
)

_SECRET_FRAGMENTS = (
    "api_key",
    "apikey",
    "access_token",
    "password",
    "secret",
    "credential",
    "authorization",
)

_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+|access_token=\S+)",
    re.IGNORECASE,
)

MAX_PAYLOAD_BYTES = 256 * 1024


def redact_text(value: str) -> str:
    return _SECRET_VALUE.sub("[REDACTED]", value)


def _normalize_key(key: str) -> str:
    return key.lower().replace("-", "_")


def _drop_key(normalized: str) -> bool:
    if normalized in _DROPPED_KEYS:
        return True
    return any(fragment in normalized for fragment in _SECRET_FRAGMENTS)


def sanitize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON data with secret keys removed and code keys rejected."""
    try:
        encoded = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("Payload must be JSON data.") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Payload is too large.")
    return _walk(payload)


def _walk(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized = _normalize_key(str(key))
            if normalized in _REJECTED_KEYS:
                raise ValueError("Payload cannot contain executable code.")
            if _drop_key(normalized):
                continue
            cleaned[str(key)] = _walk(item)
        return cleaned
    if isinstance(value, list):
        return [_walk(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    raise ValueError("Payload must be JSON data.")


def isolate_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy JSON data so a step cannot change the engine state."""
    return json.loads(json.dumps(dict(value)))
