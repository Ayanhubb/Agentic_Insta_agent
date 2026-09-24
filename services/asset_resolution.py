"""Owner-scoped logo and product image resolution for image generation.

Callers pass a user id plus a product id or business id. Bytes are returned
only for ``AVAILABLE`` rasters owned by that user. A missing, invalid, deleted,
or foreign file does not produce a stand-in image.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from backend.ai.image.base import ImageInput
from db.asset_repositories import BrandProfileRepository, BusinessAssetRepository, ProductAssetRepository, ProductRepository
from db.models import BusinessAsset, BusinessProfile, Product
from db.repositories import BusinessProfileRepository
from models.errors import AppError
from services.asset_paths import AssetPaths
from services.image_reference import load_reference_image

_LOGO_ROLES = frozenset({"logo_png", "logo_svg"})
_PRODUCT_ROLE = "product_image"


class AssetState(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNAUTHORIZED = "UNAUTHORIZED"
    DELETED = "DELETED"


@dataclass(frozen=True)
class ResolvedAsset:
    """One resolution result. ``image`` is set only when ``state`` is AVAILABLE."""

    state: AssetState
    asset_id: str | None = None
    image: ImageInput | None = None
    role: str | None = None
    product_id: str | None = None
    business_id: str | None = None
    link_role: str | None = None

    @property
    def available(self) -> bool:
        return self.state is AssetState.AVAILABLE and self.image is not None


def owner_business_id(session: Session, user_id: str) -> str | None:
    """Business profile id for this user. Absent profiles are not an error."""
    if not _caller(user_id):
        return None
    profile = BusinessProfileRepository(session).get_for_user(user_id.strip())
    return None if profile is None else profile.id


def resolve_product_asset(session: Session, settings: Any, user_id: str, product_id: str) -> ResolvedAsset:
    """Primary product image for this owner, or the primary link's failure state."""
    assets = resolve_product_assets(session, settings, user_id, product_id)
    if not assets:
        return _status(AssetState.MISSING, product_id=product_id)
    primary = next((item for item in assets if item.link_role == "primary"), None)
    return primary or assets[0]


def resolve_product_assets(session: Session, settings: Any, user_id: str, product_id: str) -> list[ResolvedAsset]:
    """Product images in catalog order: primary first, then older alternates."""
    caller = _caller(user_id)
    token = (product_id or "").strip()
    if caller is None:
        return [_status(AssetState.UNAUTHORIZED, product_id=token or None)]
    if not token or not _safe_id(token):
        return [_status(AssetState.INVALID, product_id=token or None)]
    product = session.get(Product, token)
    if product is None:
        return [_status(AssetState.MISSING, product_id=token)]
    if product.user_id != caller:
        return [_status(AssetState.UNAUTHORIZED, product_id=token)]
    links = ProductAssetRepository(session).list_for_product(caller, product.id)
    if not links:
        return [_status(AssetState.MISSING, product_id=product.id)]
    resolved: list[ResolvedAsset] = []
    for link in links:
        item = resolve_owned_asset(
            session,
            settings,
            caller,
            link.asset_id,
            expected_role=_PRODUCT_ROLE,
        )
        resolved.append(
            ResolvedAsset(
                state=item.state,
                asset_id=item.asset_id,
                image=item.image,
                role=item.role,
                product_id=product.id,
                link_role=link.role,
            )
        )
    return resolved


def resolve_brand_logo(
    session: Session,
    settings: Any,
    user_id: str,
    business_id: str | None = None,
    asset_id: str | None = None,
) -> ResolvedAsset:
    """Raster company logo for this owner.

    PNG on the brand profile is preferred. SVG and other non-raster files stay
    ``INVALID`` and are not replaced with a drawn mark.
    """
    caller, business, failure = _business_scope(session, user_id, business_id)
    if failure is not None:
        return _status(failure, business_id=_clean_id(business_id))
    requested = (asset_id or "").strip()
    if requested:
        if not _safe_id(requested):
            return _status(AssetState.INVALID, business_id=business)
        return _as_logo(
            resolve_owned_asset(session, settings, caller, requested, expected_role=None, logo=True),
            business,
        )
    candidates = _logo_rows(session, caller)
    if not candidates:
        return _status(AssetState.MISSING, business_id=business)
    classified = [
        _as_logo(resolve_owned_asset(session, settings, caller, row.id, logo=True), business) for row in candidates
    ]
    for item in classified:
        if item.available and item.role == "logo_png":
            return item
    for item in classified:
        if item.available:
            return item
    for state in (AssetState.DELETED, AssetState.INVALID, AssetState.UNAUTHORIZED):
        match = next((item for item in classified if item.state is state), None)
        if match is not None:
            return match
    return _status(AssetState.MISSING, business_id=business)


def resolve_brand_assets(
    session: Session,
    settings: Any,
    user_id: str,
    business_id: str | None = None,
) -> list[ResolvedAsset]:
    """Brand-scoped files for this business. Generation uses only AVAILABLE rasters."""
    caller, business, failure = _business_scope(session, user_id, business_id)
    if failure is not None:
        return [_status(failure, business_id=_clean_id(business_id))]
    rows = BusinessAssetRepository(session).list_for_user(caller, scope="brand")
    if not rows:
        return [_status(AssetState.MISSING, business_id=business)]
    logos = [row for row in rows if row.role in _LOGO_ROLES]
    others = [row for row in rows if row.role not in _LOGO_ROLES]
    png = [row for row in logos if row.role == "logo_png"]
    svg = [row for row in logos if row.role == "logo_svg"]
    ordered = png + svg + others
    return [
        _as_logo(resolve_owned_asset(session, settings, caller, row.id), business) for row in ordered
    ]


def resolve_owned_asset(
    session: Session,
    settings: Any,
    user_id: str,
    asset_id: str,
    *,
    expected_role: str | None = None,
    logo: bool = False,
) -> ResolvedAsset:
    """Classify one stored file. Foreign rows are not opened."""
    caller = _caller(user_id)
    token = (asset_id or "").strip()
    if caller is None:
        return _status(AssetState.UNAUTHORIZED, asset_id=token or None)
    if not token or not _safe_id(token):
        return _status(AssetState.INVALID, asset_id=token or None)
    row = session.get(BusinessAsset, token)
    if row is None:
        owned_product = ProductRepository(session).get_owned(caller, token)
        if owned_product is not None:
            return _status(AssetState.MISSING, asset_id=token)
        return _status(AssetState.DELETED, asset_id=token)
    if row.user_id != caller:
        return _status(AssetState.UNAUTHORIZED)
    if logo and row.role not in _LOGO_ROLES:
        return _status(AssetState.INVALID, asset_id=row.id, role=row.role)
    if expected_role is not None and row.role != expected_role:
        return _status(AssetState.INVALID, asset_id=row.id, role=row.role)
    return _classify_file(session, settings, caller, row)


def available_images(items: list[ResolvedAsset]) -> list[ImageInput]:
    return [item.image for item in items if item.available and item.image is not None]


def _classify_file(session: Session, settings: Any, user_id: str, row: BusinessAsset) -> ResolvedAsset:
    if settings is None:
        return _status(AssetState.INVALID, asset_id=row.id, role=row.role)
    try:
        path = AssetPaths(settings.media_root).resolve_storage_key(user_id, row.storage_key)
    except AppError:
        return _status(AssetState.INVALID, asset_id=row.id, role=row.role)
    if not path.is_file():
        return _status(AssetState.DELETED, asset_id=row.id, role=row.role)
    image = load_reference_image(session, settings, user_id, row.id)
    if image is None:
        return _status(AssetState.INVALID, asset_id=row.id, role=row.role)
    return ResolvedAsset(state=AssetState.AVAILABLE, asset_id=row.id, image=image, role=row.role)


def _logo_rows(session: Session, user_id: str) -> list[BusinessAsset]:
    assets = BusinessAssetRepository(session)
    profile = BrandProfileRepository(session).get_for_user(user_id)
    chosen: list[BusinessAsset] = []
    seen: set[str] = set()
    if profile is not None:
        for asset_id, role in (
            (profile.logo_png_asset_id, "logo_png"),
            (profile.logo_svg_asset_id, "logo_svg"),
        ):
            if not asset_id or asset_id in seen:
                continue
            row = assets.get_owned(user_id, asset_id)
            if row is None or row.role != role:
                continue
            seen.add(row.id)
            chosen.append(row)
    if chosen:
        return chosen
    for role in ("logo_png", "logo_svg"):
        row = assets.latest_for_role(user_id, role)
        if row is not None and row.id not in seen:
            seen.add(row.id)
            chosen.append(row)
    return chosen


def _business_scope(
    session: Session,
    user_id: str,
    business_id: str | None,
) -> tuple[str, str | None, AssetState | None]:
    caller = _caller(user_id)
    if caller is None:
        return "", None, AssetState.UNAUTHORIZED
    token = _clean_id(business_id)
    if business_id is not None and not token:
        return caller, None, AssetState.INVALID
    if token:
        if not _safe_id(token):
            return caller, None, AssetState.INVALID
        profile = session.get(BusinessProfile, token)
        if profile is None:
            return caller, token, AssetState.MISSING
        if profile.user_id != caller:
            return caller, token, AssetState.UNAUTHORIZED
        return caller, profile.id, None
    return caller, owner_business_id(session, caller), None


def _as_logo(item: ResolvedAsset, business_id: str | None) -> ResolvedAsset:
    if item.business_id == business_id:
        return item
    return ResolvedAsset(
        state=item.state,
        asset_id=item.asset_id,
        image=item.image if item.state is AssetState.AVAILABLE else None,
        role=item.role,
        business_id=business_id,
    )


def _status(
    state: AssetState,
    *,
    asset_id: str | None = None,
    role: str | None = None,
    product_id: str | None = None,
    business_id: str | None = None,
) -> ResolvedAsset:
    return ResolvedAsset(
        state=state,
        asset_id=asset_id,
        role=role,
        product_id=product_id,
        business_id=business_id,
    )


def _caller(user_id: str | None) -> str | None:
    token = (user_id or "").strip()
    if not token or not _safe_id(token):
        return None
    return token


def _clean_id(value: str | None) -> str | None:
    if value is None:
        return None
    token = value.strip()
    return token or None


def _safe_id(value: str) -> bool:
    if not value or len(value) > 64:
        return False
    return not any(char in value for char in "/\\") and ".." not in value
