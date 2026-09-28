"""Checks that keep policy data free of code, secrets, and bypass flags.

Comparisons stay in the engine. Nothing in this module runs text as code.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from agent_platform.policy.errors import (
    InvalidPolicyRequest,
    PolicyBypass,
    PolicyRejected,
    SecretRejected,
)

# Same names the workflow engine refuses. Copied here so this package does not
# import the Instagram agent. ``instagram.publish`` is a permission token, not
# one of these tool names, and it is not in this set.
BLOCKED_CAPABILITY_NAMES = frozenset(
    {
        "validate_image",
        "prepare_image",
        "upload_image",
        "create_instagram_media",
        "publish_instagram_media",
        "verify_publication",
        "media_publish",
        "instagram_publish",
        "publish",
        "post_to_instagram",
        "create_media",
        "publish_media",
        "eval",
        "exec",
        "compile",
        "os.system",
        "subprocess.run",
        "subprocess.call",
        "subprocess.popen",
        "enqueue_instagram_publication",
        "instagram_graph_client",
    }
)

INSTAGRAM_AGENT_ID = "instagram_marketing"
PUBLISH_CAPABILITY = "instagram.publish"
APPROVED_PUBLICATION_WORKFLOWS = frozenset(
    {
        "studio.approve",
        "direct.publish",
        "scheduler.campaign",
        "instagram.publish",
    }
)

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
_POLICY_ID = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")
_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_WORKFLOW_ID = re.compile(r"^[a-z][a-z0-9_.]{0,80}$")
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_ACTOR_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
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
_BYPASS_EXACT = frozenset(
    {
        "publish",
        "published",
        "publishing",
        "publish_true",
        "decision",
        "status",
        "approved",
        "approval",
        "approval_status",
        "skip_policy",
        "bypass",
        "force",
        "mode",
        "policy_mode",
        "auto_publish",
        "auto_daily_publish",
        "auto_festival_publish",
        "grant",
        "capability_grant",
        "publishing_granted",
        "executes_effect",
    }
)
_RESERVED_ACTORS = frozenset(
    {
        "auto",
        "system",
        "scheduler",
        "agent",
        "publish",
        "instagram",
        "instagram_marketing",
        "policy",
    }
)
_MAX_STRING = 4_096
_MAX_DEPTH = 6
_MAX_KEYS = 64
_MAX_LIST = 32


def _normalize(key: str) -> str:
    return key.strip().lower().replace("-", "_")


_NORMALIZED_TENANT_KEYS = frozenset(_normalize(key) for key in UNTRUSTED_TENANT_KEYS)


def require_policy_id(value: object) -> str:
    if not isinstance(value, str) or _POLICY_ID.fullmatch(value) is None:
        raise PolicyRejected("Policy id is not valid.")
    return value


def require_agent_id(value: object) -> str:
    if not isinstance(value, str) or _AGENT_ID.fullmatch(value) is None:
        raise PolicyRejected("Agent id is not valid.")
    return value


def require_workflow_id(value: object) -> str:
    if not isinstance(value, str) or _WORKFLOW_ID.fullmatch(value) is None:
        raise PolicyRejected("Workflow id is not valid.")
    return value


def require_capability_name(value: object) -> str:
    if not isinstance(value, str) or _CAPABILITY_NAME.fullmatch(value) is None:
        raise PolicyRejected("Capability name is not valid.")
    if value in BLOCKED_CAPABILITY_NAMES or value.split(".", 1)[0] in {
        "os",
        "sys",
        "subprocess",
        "importlib",
        "builtins",
    }:
        raise PolicyRejected("Capability is not allowed.")
    return value


def require_field_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or "__" in value
        or value.count(".") > 3
        or _FIELD.fullmatch(value) is None
    ):
        raise PolicyRejected("Policy condition is not valid.")
    if _blocked_fact_key(value.split(".", 1)[0]):
        raise PolicyRejected("Policy condition is not valid.")
    return value


def optional_request_capability(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CAPABILITY_NAME.fullmatch(value) is None:
        raise InvalidPolicyRequest()
    return value


def require_request_id(value: object, *, kind: str) -> str:
    pattern = {"policy": _POLICY_ID, "agent": _AGENT_ID, "workflow": _WORKFLOW_ID}[kind]
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise InvalidPolicyRequest()
    return value


def bind_tenant(tenant: object) -> tuple[str, str]:
    """Accept the server ``TenantContext`` without importing the MCP package."""

    if tenant.__class__.__name__ != "TenantContext":
        raise InvalidPolicyRequest()
    if getattr(tenant.__class__, "__module__", "") != "backend.mcp.tenant_isolation":
        raise InvalidPolicyRequest()
    tenant_id = str(getattr(tenant, "tenant_id", "") or "").strip()
    source = str(getattr(tenant, "source", "") or "")
    if not tenant_id or source not in _TRUSTED_SOURCES or len(tenant_id) > 128:
        raise InvalidPolicyRequest()
    return tenant_id, source


def require_human_actor(actor_id: object, agent_id: str) -> str:
    if not isinstance(actor_id, str) or _ACTOR_ID.fullmatch(actor_id) is None:
        raise PolicyBypass()
    if actor_id.strip().lower() in _RESERVED_ACTORS or actor_id == agent_id:
        raise PolicyBypass()
    return actor_id


_DOCUMENT_KEYS = frozenset(
    {
        "id",
        "mode",
        "domain",
        "outcome",
        "conditions",
        "capability",
        "agent_id",
        "workflows",
        "field",
        "operator",
        "value",
    }
)


def sanitize_facts(facts: Mapping[str, Any] | None) -> dict[str, Any]:
    if facts is None:
        return {}
    if not isinstance(facts, Mapping) or isinstance(facts, (str, bytes, bytearray)):
        raise InvalidPolicyRequest()
    cleaned = _walk(facts, depth=0, facts=True)
    if not isinstance(cleaned, dict):
        raise InvalidPolicyRequest()
    return cleaned


def scan_document(value: Mapping[str, Any]) -> None:
    """Reject secret keys and unknown keys in a policy document."""

    _walk(value, depth=0, facts=False)


def _walk(value: Any, *, depth: int, facts: bool) -> Any:
    if depth > _MAX_DEPTH:
        raise InvalidPolicyRequest()
    if callable(value) or isinstance(value, (bytes, bytearray)):
        raise SecretRejected()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidPolicyRequest()
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            raise InvalidPolicyRequest()
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_LIST:
            raise InvalidPolicyRequest()
        return [_walk(item, depth=depth + 1, facts=facts) for item in value]
    if isinstance(value, Mapping):
        if len(value) > _MAX_KEYS:
            raise InvalidPolicyRequest()
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidPolicyRequest()
            _reject_key(key, facts=facts)
            cleaned[key] = _walk(item, depth=depth + 1, facts=facts)
        return cleaned
    raise InvalidPolicyRequest()


def _reject_key(key: str, *, facts: bool) -> None:
    if _SECRET_KEY.search(key) or _normalize(key) in _CODE_KEYS:
        raise SecretRejected()
    if facts:
        if _blocked_fact_key(key):
            raise PolicyBypass()
        return
    if _normalize(key) not in _DOCUMENT_KEYS:
        raise PolicyBypass()


def _blocked_fact_key(key: str) -> bool:
    if key in UNTRUSTED_TENANT_KEYS:
        return True
    normalized = _normalize(key)
    if normalized in _NORMALIZED_TENANT_KEYS or normalized in _BYPASS_EXACT:
        return True
    if normalized.startswith(("publish", "skip_")) or "auto_publish" in normalized:
        return True
    return "bypass" in normalized
