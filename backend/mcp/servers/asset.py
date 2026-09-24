"""Owned generated-image assets. Filesystem paths are never returned."""

from __future__ import annotations

from typing import Any

from backend.mcp.errors import AssetAccessDenied
from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.sources import AssetView, RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext

_LOGO_THEMES = frozenset({"logo", "company logo"})


def is_logo(asset: AssetView) -> bool:
    if (asset.content_type or "").upper() != "BRAND":
        return False
    theme = (asset.theme or "").casefold().replace("_", " ").strip()
    return theme in _LOGO_THEMES


def require_owned_asset(gateway: RepositoryGateway, tenant_id: str, image_id: str) -> AssetView:
    asset = gateway.owned_reference(tenant_id, image_id)
    if asset is None:
        raise AssetAccessDenied("Asset is not available for this tenant.")
    return asset


def asset_tools(gateway: RepositoryGateway) -> list[MCPTool]:
    async def get_company_logo(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        asset_id = arguments.get("asset_id")
        if asset_id:
            asset = require_owned_asset(gateway, tenant.tenant_id, asset_id)
            if not is_logo(asset):
                raise AssetAccessDenied("Asset is not available for this tenant.")
            return {"found": True, "asset": asset.public()}
        catalog = gateway.company_logo(tenant.tenant_id)
        if catalog is not None:
            return {"found": True, "asset": catalog.public()}
        for asset in gateway.list_assets(tenant.tenant_id):
            if is_logo(asset):
                return {"found": True, "asset": asset.public()}
        return {"found": False, "asset": None}

    return [
        MCPTool(
            name="get_company_logo",
            description="Load this tenant's logo image. Cross-tenant asset ids are denied.",
            input_schema=object_schema(
                {"asset_id": {"type": "string", "minLength": 1, "maxLength": 64}},
            ),
            server="asset",
            handler=get_company_logo,
        )
    ]
