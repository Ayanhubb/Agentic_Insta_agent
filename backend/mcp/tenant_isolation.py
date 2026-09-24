"""Trusted tenant context.

Tenant identity comes from the authenticated caller or the scheduler.
Identifiers inside model arguments are discarded and never authorize a call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from backend.mcp.errors import MalformedArguments, TenantContextRequired

TRUSTED_SOURCES = frozenset({"authenticated", "scheduler", "system"})

# Keys a model might use to name a tenant. None of these are authoritative.
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


@dataclass(frozen=True, slots=True)
class TenantContext:
    """A tenant binding created by trusted server code."""

    tenant_id: str
    source: str = "authenticated"

    def __post_init__(self) -> None:
        tenant_id = str(self.tenant_id or "").strip()
        if not tenant_id:
            raise TenantContextRequired("Tenant context is required.")
        if self.source not in TRUSTED_SOURCES:
            raise TenantContextRequired("Tenant context must come from a trusted caller.")
        object.__setattr__(self, "tenant_id", tenant_id)


def trusted_tenant(tenant_id: str, *, source: str = "authenticated") -> TenantContext:
    """Bind a tenant from authentication or the scheduler, never from model output."""
    return TenantContext(tenant_id=tenant_id, source=source)


def require_trusted_tenant(tenant: TenantContext | None) -> TenantContext:
    if not isinstance(tenant, TenantContext):
        raise TenantContextRequired(
            "A trusted tenant context is required. Tenant ids supplied by the model are ignored."
        )
    return tenant


def strip_model_tenant_arguments(arguments: Mapping[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """Drop tenant identifiers the model included.

    The discarded value is returned only so callers can see that it was ignored.
    It must not be used as the tenant id.
    """
    if arguments is None:
        return {}, None
    if not isinstance(arguments, dict):
        raise MalformedArguments("Tool arguments must be an object.")
    safe: dict[str, Any] = {}
    discarded: str | None = None
    for key, value in arguments.items():
        if key in UNTRUSTED_TENANT_KEYS:
            if discarded is None and value is not None:
                discarded = str(value)
            continue
        safe[key] = value
    return safe, discarded
