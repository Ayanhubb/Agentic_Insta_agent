"""Brand and product catalog. Responses never include filesystem paths."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from config import Settings
from db.asset_repositories import (
    BrandGuidelineRepository,
    BrandProfileRepository,
    BusinessAssetRepository,
    ProductAssetRepository,
    ProductRepository,
)
from db.exceptions import DuplicateRecordError
from db.models import BusinessAsset, Product
from db.schemas import BrandWrite, GuidelineWrite, ProductUpdate, ProductWrite
from models.errors import AppError, ErrorCode
from services.asset_paths import AssetPaths, assert_upload_filename
from services.asset_validation import expected_scope, validate_asset_bytes
from tools.image_validator import ImageLimits

_LEAKED_KEYS = frozenset({"storage_key", "storage_path", "path", "filesystem_path", "absolute_path"})


class AssetCatalog:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.paths = AssetPaths(Path(settings.media_root))
        self.assets = BusinessAssetRepository(session)
        self.brand = BrandProfileRepository(session)
        self.guidelines = BrandGuidelineRepository(session)
        self.products = ProductRepository(session)
        self.product_assets = ProductAssetRepository(session)
        max_side = int(settings.max_image_side or 8192)
        self.limits = ImageLimits(
            max_bytes=int(settings.max_image_bytes or 8 * 1024 * 1024),
            min_width=16,
            min_height=16,
            max_width=max_side,
            max_height=max_side,
        )

    def upload(
        self,
        user_id: str,
        *,
        filename: str | None,
        data: bytes,
        claimed_mime: str | None,
        role: str,
        scope: str | None = None,
        product_id: str | None = None,
    ) -> dict[str, Any]:
        label = assert_upload_filename(filename)
        chosen_scope = expected_scope(role, scope)
        mime, width, height = validate_asset_bytes(
            data,
            role=role,
            claimed_mime=claimed_mime,
            limits=self.limits,
        )
        product = None
        if product_id:
            product = self.products.get_owned(user_id, product_id)
            if product is None:
                raise AppError(ErrorCode.NOT_FOUND, "Product was not found.", http_status=404)
            if role != "product_image":
                raise AppError(ErrorCode.INVALID_REQUEST, "Only a product image can be attached to a product.", http_status=400)
        storage_key, path = self.paths.allocate(user_id, chosen_scope, mime)
        self.paths.write_bytes(path, data)
        try:
            asset = self.assets.create(
                user_id,
                scope=chosen_scope,
                role=role,
                original_filename=label,
                mime_type=mime,
                size_bytes=len(data),
                width=width,
                height=height,
                storage_key=storage_key,
            )
            if product is not None:
                self.product_assets.replace_primary(user_id, product.id, asset.id)
        except Exception:
            self.paths.remove(path)
            raise
        return self.public_asset(asset)

    def list_assets(self, user_id: str, *, scope: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
        if scope is not None and scope not in {"brand", "product", "campaign"}:
            raise AppError(ErrorCode.INVALID_REQUEST, "Invalid asset scope.", http_status=400)
        return [self.public_asset(item) for item in self.assets.list_for_user(user_id, scope=scope, role=role)]

    def get_asset(self, user_id: str, asset_id: str) -> dict[str, Any]:
        asset = self._owned_asset(user_id, asset_id)
        return self.public_asset(asset)

    def delete_asset(self, user_id: str, asset_id: str) -> None:
        asset = self._owned_asset(user_id, asset_id)
        path = self.paths.resolve_storage_key(user_id, asset.storage_key)
        removed = self.assets.delete_owned(user_id, asset_id)
        if removed is None:
            raise AppError(ErrorCode.NOT_FOUND, "Asset was not found.", http_status=404)
        self.paths.remove(path)

    def open_media(self, user_id: str, asset_id: str) -> tuple[Path, str, str]:
        asset = self._owned_asset(user_id, asset_id)
        path = self.paths.resolve_storage_key(user_id, asset.storage_key)
        if not path.is_file():
            raise AppError(ErrorCode.NOT_FOUND, "Asset was not found.", http_status=404)
        return path, asset.mime_type, asset.original_filename

    def upsert_brand(self, user_id: str, body: BrandWrite) -> dict[str, Any]:
        logo_png = self._logo(user_id, body.logo_png_asset_id, "logo_png")
        logo_svg = self._logo(user_id, body.logo_svg_asset_id, "logo_svg")
        if logo_png and logo_svg and logo_png == logo_svg:
            raise AppError(ErrorCode.INVALID_REQUEST, "PNG and SVG logos must be different files.", http_status=400)
        profile = self.brand.upsert(
            user_id,
            company_name=body.company_name,
            website=body.website,
            instagram_handle=body.instagram_handle,
            brand_colors=[item.model_dump() for item in body.brand_colors],
            fonts=[item.model_dump() for item in body.fonts],
            logo_png_asset_id=logo_png,
            logo_svg_asset_id=logo_svg,
        )
        guidelines = None
        if body.guidelines is not None:
            guidelines = self.guidelines.replace_for_user(user_id, _guideline_items(body.guidelines))
        return self.public_brand(profile, guidelines)

    def get_brand(self, user_id: str) -> dict[str, Any] | None:
        profile = self.brand.get_for_user(user_id)
        if profile is None:
            return None
        return self.public_brand(profile, self.guidelines.list_for_user(user_id))

    def create_product(self, user_id: str, body: ProductWrite) -> dict[str, Any]:
        try:
            product = self.products.create(user_id, **_product_fields(body))
        except DuplicateRecordError as exc:
            raise AppError(ErrorCode.CONFLICT, "A product with this SKU already exists.", http_status=409) from exc
        self._attach_image(user_id, product.id, body.image_asset_id)
        return self.public_product(user_id, product)

    def update_product(self, user_id: str, product_id: str, body: ProductUpdate) -> dict[str, Any]:
        changes = _product_changes(body)
        image_set = "image_asset_id" in body.model_fields_set
        image_id = body.image_asset_id if image_set else None
        try:
            product = self.products.update(user_id, product_id, changes)
        except DuplicateRecordError as exc:
            raise AppError(ErrorCode.CONFLICT, "A product with this SKU already exists.", http_status=409) from exc
        if product is None:
            raise AppError(ErrorCode.NOT_FOUND, "Product was not found.", http_status=404)
        if image_set:
            self._attach_image(user_id, product.id, image_id)
        return self.public_product(user_id, product)

    def list_products(
        self,
        user_id: str,
        *,
        active_only: bool = False,
        offers_only: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self.products.list_for_user(user_id, active_only=active_only, offers_only=offers_only)
        return [self.public_product(user_id, item) for item in rows]

    def get_product(self, user_id: str, product_id: str) -> dict[str, Any]:
        product = self.products.get_owned(user_id, product_id)
        if product is None:
            raise AppError(ErrorCode.NOT_FOUND, "Product was not found.", http_status=404)
        return self.public_product(user_id, product)

    def get_product_by_sku(self, user_id: str, sku: str) -> dict[str, Any] | None:
        product = self.products.get_by_sku(user_id, sku)
        if product is None:
            return None
        return self.public_product(user_id, product)

    def public_asset(self, asset: BusinessAsset) -> dict[str, Any]:
        payload = {
            "id": asset.id,
            "scope": asset.scope,
            "role": asset.role,
            "filename": asset.original_filename,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "width": asset.width,
            "height": asset.height,
            "media_url": f"/api/v1/assets/{asset.id}/media",
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
        }
        _assert_public(payload)
        return payload

    def public_product(self, user_id: str, product: Product) -> dict[str, Any]:
        link = self.product_assets.primary_for_product(user_id, product.id)
        image = None
        if link is not None:
            asset = self.assets.get_owned(user_id, link.asset_id)
            if asset is not None:
                image = self.public_asset(asset)
        payload = {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "price": _money(product.price),
            "sku": product.sku,
            "is_active": product.is_active,
            "offer": product.offer,
            "image": image,
            "created_at": product.created_at.isoformat() if product.created_at else None,
            "updated_at": product.updated_at.isoformat() if product.updated_at else None,
        }
        _assert_public(payload)
        return payload

    def public_brand(self, profile, guidelines) -> dict[str, Any]:
        if guidelines is None:
            guidelines = self.guidelines.list_for_user(profile.user_id)
        logo_png = self._public_logo(profile.user_id, profile.logo_png_asset_id)
        logo_svg = self._public_logo(profile.user_id, profile.logo_svg_asset_id)
        payload = {
            "id": profile.id,
            "company_name": profile.company_name,
            "website": profile.website,
            "instagram_handle": profile.instagram_handle,
            "brand_colors": profile.brand_colors or [],
            "fonts": profile.fonts or [],
            "logo_png": logo_png,
            "logo_svg": logo_svg,
            "guidelines": [
                {"id": item.id, "title": item.title, "body": item.body}
                for item in guidelines
            ],
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }
        _assert_public(payload)
        return payload

    def _owned_asset(self, user_id: str, asset_id: str) -> BusinessAsset:
        if not asset_id or "/" in asset_id or "\\" in asset_id or ".." in asset_id:
            raise AppError(ErrorCode.NOT_FOUND, "Asset was not found.", http_status=404)
        asset = self.assets.get_owned(user_id, asset_id)
        if asset is None:
            raise AppError(ErrorCode.NOT_FOUND, "Asset was not found.", http_status=404)
        return asset

    def _logo(self, user_id: str, asset_id: str | None, role: str) -> str | None:
        if asset_id is None:
            return None
        asset = self.assets.get_owned(user_id, asset_id)
        if asset is None:
            raise AppError(ErrorCode.NOT_FOUND, "Asset was not found.", http_status=404)
        if asset.role != role:
            raise AppError(ErrorCode.INVALID_REQUEST, "The selected file is not the right logo type.", http_status=400)
        return asset.id

    def _public_logo(self, user_id: str, asset_id: str | None) -> dict[str, Any] | None:
        if not asset_id:
            return None
        asset = self.assets.get_owned(user_id, asset_id)
        if asset is None:
            return None
        return self.public_asset(asset)

    def _attach_image(self, user_id: str, product_id: str, asset_id: str | None) -> None:
        if asset_id is None:
            self.product_assets.replace_primary(user_id, product_id, None)
            return
        asset = self.assets.get_owned(user_id, asset_id)
        if asset is None:
            raise AppError(ErrorCode.NOT_FOUND, "Asset was not found.", http_status=404)
        if asset.role != "product_image":
            raise AppError(ErrorCode.INVALID_REQUEST, "The selected file is not a product image.", http_status=400)
        self.product_assets.replace_primary(user_id, product_id, asset.id)


def _guideline_items(guidelines: str | list[GuidelineWrite]) -> list[dict[str, str]]:
    if isinstance(guidelines, str):
        text = guidelines.strip()
        if not text:
            raise AppError(ErrorCode.INVALID_REQUEST, "Brand guidelines cannot be empty.", http_status=400)
        if len(text) > 20000:
            raise AppError(ErrorCode.INVALID_REQUEST, "Brand guidelines are too long.", http_status=400)
        return [{"title": "Brand guidelines", "body": text}]
    if len(guidelines) > 20:
        raise AppError(ErrorCode.INVALID_REQUEST, "Too many guideline sections.", http_status=400)
    return [{"title": item.title.strip() or "Brand guidelines", "body": item.body.strip()} for item in guidelines]


def _product_fields(body: ProductWrite) -> dict[str, Any]:
    return {
        "name": body.name,
        "description": body.description,
        "category": body.category,
        "price": body.price,
        "sku": body.sku,
        "is_active": body.is_active,
        "offer": body.offer,
    }


def _product_changes(body: ProductUpdate) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    data.pop("image_asset_id", None)
    if "name" in data and not data["name"]:
        raise AppError(ErrorCode.INVALID_REQUEST, "Product name is required.", http_status=400)
    if data.get("is_active") is None and "is_active" in data:
        data.pop("is_active")
    return data


def _money(value: Decimal | float | int | str | None) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def _assert_public(payload: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            leaked = _LEAKED_KEYS.intersection(value)
            if leaked:
                raise AppError(ErrorCode.INTERNAL_ERROR, "Asset payload is not public.", http_status=500)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
