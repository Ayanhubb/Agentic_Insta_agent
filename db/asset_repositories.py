"""Owner-scoped queries for brand files, products, and guidelines."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, delete, select, update
from sqlalchemy.orm import Session

from db.base import utcnow
from db.models import BrandGuideline, BrandProfile, BusinessAsset, Product, ProductAsset
from db.repositories import flush_or_raise


class BusinessAssetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> BusinessAsset:
        asset = BusinessAsset(user_id=user_id, **fields)
        self.session.add(asset)
        flush_or_raise(self.session)
        return asset

    def get_owned(self, user_id: str, asset_id: str) -> BusinessAsset | None:
        return self.session.scalar(
            select(BusinessAsset).where(BusinessAsset.id == asset_id, BusinessAsset.user_id == user_id)
        )

    def list_for_user(
        self,
        user_id: str,
        *,
        scope: str | None = None,
        role: str | None = None,
    ) -> list[BusinessAsset]:
        stmt = select(BusinessAsset).where(BusinessAsset.user_id == user_id)
        if scope:
            stmt = stmt.where(BusinessAsset.scope == scope)
        if role:
            stmt = stmt.where(BusinessAsset.role == role)
        stmt = stmt.order_by(BusinessAsset.created_at.desc())
        return list(self.session.scalars(stmt))

    def latest_for_role(self, user_id: str, role: str) -> BusinessAsset | None:
        return self.session.scalar(
            select(BusinessAsset)
            .where(BusinessAsset.user_id == user_id, BusinessAsset.role == role)
            .order_by(BusinessAsset.created_at.desc())
            .limit(1)
        )

    def delete_owned(self, user_id: str, asset_id: str) -> BusinessAsset | None:
        asset = self.get_owned(user_id, asset_id)
        if asset is None:
            return None
        profiles = list(self.session.scalars(select(BrandProfile).where(BrandProfile.user_id == user_id)))
        for profile in profiles:
            if profile.logo_png_asset_id == asset.id:
                profile.logo_png_asset_id = None
            if profile.logo_svg_asset_id == asset.id:
                profile.logo_svg_asset_id = None
        self.session.execute(
            delete(ProductAsset).where(ProductAsset.user_id == user_id, ProductAsset.asset_id == asset.id)
        )
        self.session.execute(
            update(BrandGuideline)
            .where(BrandGuideline.user_id == user_id, BrandGuideline.asset_id == asset.id)
            .values(asset_id=None)
        )
        self.session.delete(asset)
        flush_or_raise(self.session)
        return asset


class BrandProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: str) -> BrandProfile | None:
        return self.session.scalar(select(BrandProfile).where(BrandProfile.user_id == user_id))

    def upsert(self, user_id: str, **fields: Any) -> BrandProfile:
        profile = self.get_for_user(user_id)
        if profile is None:
            profile = BrandProfile(user_id=user_id, **fields)
            self.session.add(profile)
        else:
            for key, value in fields.items():
                setattr(profile, key, value)
            profile.updated_at = utcnow()
        flush_or_raise(self.session)
        return profile


class BrandGuidelineRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_user(self, user_id: str) -> list[BrandGuideline]:
        stmt = (
            select(BrandGuideline)
            .where(BrandGuideline.user_id == user_id)
            .order_by(BrandGuideline.created_at.asc())
        )
        return list(self.session.scalars(stmt))

    def replace_for_user(self, user_id: str, items: list[dict[str, str]]) -> list[BrandGuideline]:
        existing = self.list_for_user(user_id)
        for row in existing:
            self.session.delete(row)
        flush_or_raise(self.session)
        created: list[BrandGuideline] = []
        for item in items:
            row = BrandGuideline(user_id=user_id, title=item["title"], body=item["body"])
            self.session.add(row)
            created.append(row)
        flush_or_raise(self.session)
        return created


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> Product:
        product = Product(user_id=user_id, **fields)
        self.session.add(product)
        flush_or_raise(self.session)
        return product

    def get_owned(self, user_id: str, product_id: str) -> Product | None:
        return self.session.scalar(select(Product).where(Product.id == product_id, Product.user_id == user_id))

    def get_by_sku(self, user_id: str, sku: str) -> Product | None:
        return self.session.scalar(select(Product).where(Product.user_id == user_id, Product.sku == sku))

    def list_for_user(
        self,
        user_id: str,
        *,
        active_only: bool = False,
        offers_only: bool = False,
    ) -> list[Product]:
        stmt = select(Product).where(Product.user_id == user_id)
        if active_only:
            stmt = stmt.where(Product.is_active.is_(True))
        if offers_only:
            stmt = stmt.where(Product.is_active.is_(True), Product.offer.is_not(None), Product.offer != "")
        stmt = stmt.order_by(Product.created_at.desc())
        return list(self.session.scalars(stmt))

    def update(self, user_id: str, product_id: str, changes: dict[str, Any]) -> Product | None:
        product = self.get_owned(user_id, product_id)
        if product is None:
            return None
        for key, value in changes.items():
            setattr(product, key, value)
        product.updated_at = utcnow()
        flush_or_raise(self.session)
        return product


class ProductAssetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_product(self, user_id: str, product_id: str) -> list[ProductAsset]:
        primary_first = case((ProductAsset.role == "primary", 0), else_=1)
        stmt = (
            select(ProductAsset)
            .where(ProductAsset.user_id == user_id, ProductAsset.product_id == product_id)
            .order_by(primary_first, ProductAsset.created_at.asc())
        )
        return list(self.session.scalars(stmt))

    def list_for_asset(self, user_id: str, asset_id: str) -> list[ProductAsset]:
        stmt = select(ProductAsset).where(
            ProductAsset.user_id == user_id,
            ProductAsset.asset_id == asset_id,
        )
        return list(self.session.scalars(stmt))

    def primary_for_product(self, user_id: str, product_id: str) -> ProductAsset | None:
        return self.session.scalar(
            select(ProductAsset)
            .where(
                ProductAsset.user_id == user_id,
                ProductAsset.product_id == product_id,
                ProductAsset.role == "primary",
            )
            .order_by(ProductAsset.created_at.desc())
            .limit(1)
        )

    def replace_primary(self, user_id: str, product_id: str, asset_id: str | None) -> ProductAsset | None:
        self.session.execute(
            delete(ProductAsset).where(
                ProductAsset.user_id == user_id,
                ProductAsset.product_id == product_id,
                ProductAsset.role == "primary",
            )
        )
        flush_or_raise(self.session)
        if asset_id is None:
            return None
        link = ProductAsset(user_id=user_id, product_id=product_id, asset_id=asset_id, role="primary")
        self.session.add(link)
        flush_or_raise(self.session)
        return link
