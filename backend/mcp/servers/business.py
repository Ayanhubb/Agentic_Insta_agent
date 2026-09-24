"""Business profile tool. Reads BusinessProfileRepository."""

from __future__ import annotations

from typing import Any

from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext


def business_tools(gateway: RepositoryGateway) -> list[MCPTool]:
    async def get_business_profile(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        profile = gateway.business_profile(tenant.tenant_id)
        return {"found": profile is not None, "profile": profile}

    return [
        MCPTool(
            name="get_business_profile",
            description="Load the signed-in tenant's business profile.",
            input_schema=object_schema(),
            server="business",
            handler=get_business_profile,
        )
    ]
