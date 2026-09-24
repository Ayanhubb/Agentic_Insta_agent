"""Product, product image, and active offer tools over the business profile and generated images."""

from __future__ import annotations

from typing import Any

from backend.mcp.errors import AssetAccessDenied, MalformedArguments
from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.servers.asset import require_owned_asset
from backend.mcp.sources import AssetView, RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext

_ACTIVE_APPROVALS = frozenset({"APPROVED", "AUTO_APPROVED"})


def _is_product_image(asset: AssetView, product: str | None) -> bool:
    if (asset.content_type or "").upper() != "PRODUCT":
        return False
    if not product:
        return True
    return product.casefold() in asset.prompt_text.casefold()


def product_tools(gateway: RepositoryGateway) -> list[MCPTool]:
    async def get_product(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        wanted = str(arguments["name"]).strip()
        if not wanted:
            raise MalformedArguments("Argument 'name' is too short.")
        match = next(
            (name for name in gateway.product_names(tenant.tenant_id) if name.casefold() == wanted.casefold()),
            None,
        )
        if match is None:
            return {"found": False, "product": None}
        return {"found": True, "product": {"name": match}}

    async def get_product_image(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        image_id = arguments.get("image_id")
        product = arguments.get("product")
        if product is not None:
            product = product.strip()
            if not product:
                raise MalformedArguments("Argument 'product' is too short.")
        if not image_id and not product:
            raise MalformedArguments("Missing required argument 'product' or 'image_id'.")
        if image_id:
            asset = require_owned_asset(gateway, tenant.tenant_id, image_id)
            if not _is_product_image(asset, product):
                raise AssetAccessDenied("Asset is not available for this tenant.")
            return {"found": True, "asset": asset.public()}
        matches = [
            asset for asset in gateway.list_assets(tenant.tenant_id) if _is_product_image(asset, product)
        ]
        if not matches:
            return {"found": False, "asset": None}
        return {"found": True, "asset": matches[0].public()}

    async def get_active_offers(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        offers = [
            asset.public()
            for asset in gateway.list_assets(tenant.tenant_id)
            if (asset.content_type or "").upper() == "PROMOTION"
            and (asset.approval_status or "").upper() in _ACTIVE_APPROVALS
        ]
        return {"offers": offers}

    string_arg = {"type": "string", "minLength": 1, "maxLength": 200}
    return [
        MCPTool(
            name="get_product",
            description="Look up one product by name on this tenant's business profile.",
            input_schema=object_schema({"name": string_arg}, required=["name"]),
            server="product",
            handler=get_product,
        ),
        MCPTool(
            name="get_product_image",
            description="Load a product image owned by this tenant. Other tenants' asset ids are denied.",
            input_schema=object_schema(
                {
                    "product": string_arg,
                    "image_id": {"type": "string", "minLength": 1, "maxLength": 64},
                }
            ),
            server="product",
            handler=get_product_image,
        ),
        MCPTool(
            name="get_active_offers",
            description="List this tenant's approved promotion images.",
            input_schema=object_schema(),
            server="product",
            handler=get_active_offers,
        ),
    ]
