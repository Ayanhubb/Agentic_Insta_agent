"""Brand guidelines projected from the tenant's business profile."""

from __future__ import annotations

from typing import Any

from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext


def brand_tools(gateway: RepositoryGateway) -> list[MCPTool]:
    async def get_brand_guidelines(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        guidelines = gateway.brand_guidelines(tenant.tenant_id)
        return {"found": guidelines is not None, "guidelines": guidelines}

    return [
        MCPTool(
            name="get_brand_guidelines",
            description="Load brand style, language, audience, and location for this tenant.",
            input_schema=object_schema(),
            server="brand",
            handler=get_brand_guidelines,
        )
    ]
