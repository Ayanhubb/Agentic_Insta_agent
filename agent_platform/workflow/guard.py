"""Checks that keep workflow data free of code and secrets.

Comparisons and field paths are structured. Nothing in this module runs text
as code.
"""

from __future__ import annotations

import re
from typing import Any

from agent_platform.workflow.errors import InvalidDefinition, SecretRejected, WorkflowError

# Same keys MCP strips. Copied here so this package does not import the MCP
# package (that import loads the Instagram tool allowlist).
UNTRUSTED_TENANT_KEYS = frozenset(
    {
        "tenant_id",
        "tenantId",
        "tenant",
        "user_id",
        "userId",
        "user",
        "account_id",
        "accountId",
        "instagram_account_id",
        "instagramAccountId",
        "business_id",
        "businessId",
        "owner_id",
        "ownerId",
    }
)
_TRUSTED_SOURCES = frozenset({"authenticated", "scheduler", "system"})

_WORKFLOW_ID = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_STEP_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|credential|private[_-]?key)",
    re.IGNORECASE,
)
_CODE_KEYS = frozenset(
    {
        "eval",
        "exec",
        "command",
        "commands",
        "shell",
        "script",
        "code",
        "python",
        "subprocess",
        "import",
        "handler",
        "callable",
    }
)
_MAX_STRING = 16_384
_MAX_DEPTH = 8


def require_workflow_id(value: object) -> str:
    if not isinstance(value, str) or _WORKFLOW_ID.fullmatch(value) is None:
        raise InvalidDefinition("Workflow id is not valid.")
    return value


def require_step_id(value: object) -> str:
    if not isinstance(value, str) or _STEP_ID.fullmatch(value) is None:
        raise InvalidDefinition("Step id is not valid.")
    return value


def require_capability_name(value: object) -> str:
    if not isinstance(value, str) or _CAPABILITY_NAME.fullmatch(value) is None:
        raise InvalidDefinition("Capability name is not valid.")
    return value


def require_field_path(value: object) -> str:
    if not isinstance(value, str) or "__" in value or _FIELD.fullmatch(value) is None:
        raise InvalidDefinition("Condition field is not valid.")
    return value


def bind_tenant(tenant: object) -> tuple[str, str]:
    """Accept the server ``TenantContext`` without importing the MCP package."""

    if tenant.__class__.__name__ != "TenantContext":
        raise WorkflowError("A trusted tenant context is required.")
    if getattr(tenant.__class__, "__module__", "") != "backend.mcp.tenant_isolation":
        raise WorkflowError("A trusted tenant context is required.")
    tenant_id = str(getattr(tenant, "tenant_id", "") or "").strip()
    source = str(getattr(tenant, "source", "") or "")
    if not tenant_id or source not in _TRUSTED_SOURCES:
        raise WorkflowError("A trusted tenant context is required.")
    return tenant_id, source


def strip_untrusted_tenant_keys(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in UNTRUSTED_TENANT_KEYS}


def require_idempotency_key(value: str) -> str:
    if _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise InvalidDefinition("Idempotency key is not valid.")
    return value


def reject_secrets(value: Any, *, depth: int = 0) -> None:
    """Raise if a mapping carries a secret-like or code-like key, or a callable."""

    if depth > _MAX_DEPTH:
        raise InvalidDefinition("Workflow data is too deep.")
    if callable(value) or isinstance(value, (bytes, bytearray)):
        raise SecretRejected()
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            raise InvalidDefinition("Workflow data is too large.")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            reject_secrets(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or _bad_key(key):
                raise SecretRejected()
            reject_secrets(item, depth=depth + 1)
        return
    raise InvalidDefinition("Workflow data contains an unsupported value.")


def _bad_key(key: str) -> bool:
    lowered = key.strip().lower().replace("-", "_")
    if lowered in _CODE_KEYS:
        return True
    return _SECRET_KEY.search(lowered) is not None


def lookup_field(data: dict[str, Any], field: str) -> tuple[bool, Any]:
    current: Any = data
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current
