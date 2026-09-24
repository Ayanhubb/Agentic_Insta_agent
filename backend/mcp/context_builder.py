"""Build a tenant-scoped context bundle by calling allowlisted MCP tools."""

from __future__ import annotations

import asyncio
from typing import Any

from backend.mcp.client import MCPClient, sanitize_payload
from backend.mcp.tenant_isolation import TenantContext, require_trusted_tenant


class ContextBuilder:
    def __init__(self, client: MCPClient) -> None:
        self._client = client

    async def build(
        self,
        tenant: TenantContext,
        *,
        festival_name: str | None = None,
        product_name: str | None = None,
    ) -> dict[str, Any]:
        bound = require_trusted_tenant(tenant)
        # Independent reads. DeepSeek is called only after this bundle exists.
        business, brand, offers, festivals, rules, logo = await asyncio.gather(
            self._client.invoke("get_business_profile", {}, bound),
            self._client.invoke("get_brand_guidelines", {}, bound),
            self._client.invoke("get_active_offers", {}, bound),
            self._client.invoke("get_upcoming_festivals", {}, bound),
            self._client.invoke("get_content_rules", {}, bound),
            self._client.invoke("get_company_logo", {}, bound),
        )
        context: dict[str, Any] = {
            "tenant_id": bound.tenant_id,
            "business": business.data,
            "brand": brand.data,
            "offers": offers.data,
            "festivals": festivals.data,
            "content_rules": rules.data,
            "logo": logo.data,
        }
        if festival_name and festival_name.strip():
            details = await self._client.invoke(
                "get_festival_details",
                {"festival_name": festival_name.strip()},
                bound,
            )
            context["festival"] = details.data
        if product_name and product_name.strip():
            product = await self._client.invoke(
                "get_product",
                {"name": product_name.strip()},
                bound,
            )
            context["product"] = product.data
        safe = sanitize_payload(context)
        return safe if isinstance(safe, dict) else context
