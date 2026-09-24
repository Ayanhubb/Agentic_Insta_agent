"""Tenant-bound brand and product tools.

These tools are not part of the Instagram publishing allowlist. ``user_id`` is bound
when the tool object is created and cannot be overridden by tool arguments.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from config import Settings
from models.errors import AppError, ErrorCode
from services.asset_catalog import AssetCatalog

BRAND_MCP_TOOL_NAMES = (
    "get_company_logo",
    "get_brand_assets",
    "get_brand_guidelines",
    "get_product",
    "get_product_image",
    "get_active_products",
    "get_active_offers",
)

_FORBIDDEN_ARGUMENTS = frozenset(
    {
        "user_id",
        "user",
        "tenant",
        "path",
        "storage_key",
        "storage_path",
        "filesystem_path",
        "filename",
    }
)


class BrandMcp:
    def __init__(self, session: Session, user_id: str, settings: Settings) -> None:
        self.user_id = user_id
        self.catalog = AssetCatalog(session, settings)

    def get_company_logo(self, kind: str = "png") -> dict[str, Any]:
        chosen = (kind or "png").strip().lower()
        if chosen not in {"png", "svg"}:
            raise AppError(ErrorCode.INVALID_REQUEST, "Logo kind must be png or svg.", http_status=400)
        brand = self.catalog.get_brand(self.user_id)
        logo = None
        if brand is not None:
            logo = brand.get("logo_svg") if chosen == "svg" else brand.get("logo_png")
        if logo is None:
            role = "logo_svg" if chosen == "svg" else "logo_png"
            rows = self.catalog.list_assets(self.user_id, role=role)
            logo = rows[0] if rows else None
        if logo is None:
            return _missing("logo")
        return {"ok": True, "logo": logo}

    def get_brand_assets(self) -> dict[str, Any]:
        brand = self.catalog.get_brand(self.user_id)
        assets = self.catalog.list_assets(self.user_id, scope="brand")
        return {"ok": True, "brand": brand, "assets": assets}

    def get_brand_guidelines(self) -> dict[str, Any]:
        brand = self.catalog.get_brand(self.user_id)
        guidelines = list(brand.get("guidelines") or []) if brand else []
        files = self.catalog.list_assets(self.user_id, role="guideline")
        return {"ok": True, "guidelines": guidelines, "files": files}

    def get_product(self, product_id: str | None = None, sku: str | None = None) -> dict[str, Any]:
        if product_id:
            try:
                product = self.catalog.get_product(self.user_id, product_id)
            except AppError as exc:
                if exc.code == ErrorCode.NOT_FOUND:
                    return _missing("product")
                raise
            return {"ok": True, "product": product}
        if sku:
            product = self.catalog.get_product_by_sku(self.user_id, sku.strip())
            if product is None:
                return _missing("product")
            return {"ok": True, "product": product}
        raise AppError(ErrorCode.INVALID_REQUEST, "A product id or SKU is required.", http_status=400)

    def get_product_image(self, product_id: str) -> dict[str, Any]:
        try:
            product = self.catalog.get_product(self.user_id, product_id)
        except AppError as exc:
            if exc.code == ErrorCode.NOT_FOUND:
                return _missing("product")
            raise
        image = product.get("image")
        if not image:
            return _missing("product_image")
        return {"ok": True, "image": image}

    def get_active_products(self) -> dict[str, Any]:
        products = self.catalog.list_products(self.user_id, active_only=True)
        return {"ok": True, "products": products}

    def get_active_offers(self) -> dict[str, Any]:
        products = self.catalog.list_products(self.user_id, offers_only=True)
        offers = [
            {
                "product_id": item["id"],
                "name": item["name"],
                "sku": item["sku"],
                "offer": item["offer"],
                "price": item["price"],
            }
            for item in products
            if item.get("offer")
        ]
        return {"ok": True, "offers": offers}

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in BRAND_MCP_TOOL_NAMES:
            raise AppError(ErrorCode.UNKNOWN_TOOL, "Requested tool is not available.", http_status=400)
        payload = dict(arguments or {})
        if _FORBIDDEN_ARGUMENTS.intersection(payload):
            raise AppError(ErrorCode.INVALID_REQUEST, "That argument is not allowed.", http_status=400)
        method = getattr(self, name)
        return method(**payload)


def _missing(kind: str) -> dict[str, Any]:
    return {"ok": False, "error": "not_found", "resource": kind}
