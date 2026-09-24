"""Read-only access to existing repositories and festival services.

MCP tools project these results. They do not keep a second copy of business data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from db.enums import (
    DEFAULT_DAILY_POSTS_PER_DAY,
    DEFAULT_FESTIVAL_POSTS_PER_FESTIVAL,
    DEFAULT_TIMEZONE,
)
from db.uow import Database
from festivals.festival_service import FestivalService
from services.clock import Clock


@dataclass(frozen=True, slots=True)
class AssetView:
    """Owned image metadata. `prompt_text` is for matching only and is not returned.

    Business assets use `media_kind="asset"`. Generated images keep the generation URL.
    Filesystem keys are never included.
    """

    id: str
    content_type: str | None
    theme: str | None
    mime_type: str | None
    width: int | None
    height: int | None
    approval_status: str | None
    prompt_text: str
    role: str | None = None
    scope: str | None = None
    media_kind: str = "generation"

    def public(self) -> dict[str, Any]:
        if self.media_kind == "asset":
            media_url = f"/api/v1/assets/{self.id}/media"
        else:
            media_url = f"/api/v1/generation/{self.id}/media"
        payload: dict[str, Any] = {
            "id": self.id,
            "content_type": self.content_type,
            "theme": self.theme,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "media_url": media_url,
        }
        if self.role:
            payload["role"] = self.role
        if self.scope:
            payload["scope"] = self.scope
        return payload


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe_asset_id(asset_id: str) -> bool:
    token = (asset_id or "").strip()
    if not token or len(token) > 64:
        return False
    return not any(char in token for char in "/\\") and ".." not in token


def _money(value: Any) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def _product_image_ids(db: Any, tenant_id: str, product_id: str) -> list[str]:
    ids: list[str] = []
    for link in db.product_assets.list_for_product(tenant_id, product_id):
        asset = db.business_assets.get_owned(tenant_id, link.asset_id)
        if asset is not None and asset.role == "product_image" and asset.id not in ids:
            ids.append(asset.id)
    return ids


def _business_asset_view(db: Any, row: Any, *, product_name: str | None = None) -> AssetView:
    names: list[str] = []
    if product_name:
        names.append(product_name)
    else:
        for link in db.product_assets.list_for_asset(row.user_id, row.id):
            product = db.products.get_owned(row.user_id, link.product_id)
            if product is not None and product.name not in names:
                names.append(product.name)
    role = str(row.role or "")
    if role in {"logo_png", "logo_svg"}:
        content_type, theme = "BRAND", "logo"
    elif role == "product_image":
        content_type, theme = "PRODUCT", names[0] if names else "product"
    else:
        content_type, theme = "BRAND", role or "brand"
    prompt = " ".join(names) or str(getattr(row, "original_filename", "") or "")
    return AssetView(
        id=row.id,
        content_type=content_type,
        theme=theme,
        mime_type=row.mime_type,
        width=row.width,
        height=row.height,
        approval_status=None,
        prompt_text=prompt,
        role=role or None,
        scope=row.scope,
        media_kind="asset",
    )


def _asset_view(row: Any) -> AssetView:
    fields = (
        getattr(row, "theme", None),
        getattr(row, "original_prompt", None),
        getattr(row, "enhanced_prompt", None),
    )
    prompt = " ".join(str(part).strip() for part in fields if part and str(part).strip())
    return AssetView(
        id=row.id,
        content_type=row.content_type,
        theme=row.theme,
        mime_type=row.mime_type,
        width=row.width,
        height=row.height,
        approval_status=row.approval_status,
        prompt_text=prompt,
    )


def serialize_profile(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "business_name": row.business_name,
        "business_type": row.business_type,
        "business_category": row.business_category,
        "description": row.description,
        "target_audience": row.target_audience,
        "location": row.location,
        "brand_style": row.brand_style,
        "preferred_language": row.preferred_language,
        "products": _text_list(row.products),
        "services": _text_list(row.services),
    }


def serialize_guidelines(row: Any) -> dict[str, Any]:
    return {
        "business_name": row.business_name,
        "brand_style": row.brand_style,
        "preferred_language": row.preferred_language,
        "target_audience": row.target_audience,
        "business_category": row.business_category,
        "location": row.location,
    }


def serialize_campaign(row: Any) -> dict[str, Any]:
    festival_date = row.festival_date
    return {
        "id": row.id,
        "festival_name": row.festival_name,
        "festival_date": festival_date.isoformat() if isinstance(festival_date, date) else str(festival_date),
        "year": row.year,
        "required_posts": row.required_posts,
        "generated_posts": row.generated_posts,
        "published_posts": row.published_posts,
        "remaining_posts": row.remaining_posts,
        "enabled": row.enabled,
    }


def default_automation() -> dict[str, Any]:
    return {
        "daily_enabled": False,
        "daily_posts_per_day": DEFAULT_DAILY_POSTS_PER_DAY,
        "daily_post_time": None,
        "festival_enabled": False,
        "festival_posts_per_festival": DEFAULT_FESTIVAL_POSTS_PER_FESTIVAL,
        "auto_daily_publish": False,
        "auto_festival_publish": False,
        "timezone": DEFAULT_TIMEZONE,
        "pre_festival_days": 1,
        "allow_same_day_festival_posts": False,
    }


def serialize_automation(row: Any) -> dict[str, Any]:
    payload = default_automation()
    payload.update(
        {
            "daily_enabled": bool(row.daily_enabled),
            "daily_posts_per_day": row.daily_posts_per_day,
            "daily_post_time": row.daily_post_time,
            "festival_enabled": bool(row.festival_enabled),
            "festival_posts_per_festival": row.festival_posts_per_festival,
            "auto_daily_publish": bool(row.auto_daily_publish),
            "auto_festival_publish": bool(row.auto_festival_publish),
            "timezone": row.timezone or DEFAULT_TIMEZONE,
            "pre_festival_days": row.pre_festival_days,
            "allow_same_day_festival_posts": bool(row.allow_same_day_festival_posts),
        }
    )
    return payload


class RepositoryGateway:
    """Short read-only sessions over the existing unit of work."""

    def __init__(self, session_factory: Callable[[], Session], clock: Clock | None = None) -> None:
        self._session_factory = session_factory
        self._clock = clock or Clock()

    @contextmanager
    def read(self) -> Iterator[Database]:
        session = self._session_factory()
        try:
            yield Database(session)
        finally:
            session.rollback()
            session.close()

    def today(self, timezone_name: str | None = None) -> date:
        return self._clock.now(timezone_name or DEFAULT_TIMEZONE).date()

    def business_profile(self, tenant_id: str) -> dict[str, Any] | None:
        with self.read() as db:
            row = db.business.get_for_user(tenant_id)
            return None if row is None else serialize_profile(row)

    def brand_guidelines(self, tenant_id: str) -> dict[str, Any] | None:
        with self.read() as db:
            row = db.business.get_for_user(tenant_id)
            payload = None if row is None else serialize_guidelines(row)
            sections = [
                {"id": item.id, "title": item.title, "body": item.body}
                for item in db.brand_guidelines.list_for_user(tenant_id)
            ]
        if payload is None and not sections:
            return None
        guidelines = dict(payload or {})
        if sections:
            guidelines["sections"] = sections
        return guidelines

    def brand_assets(self, tenant_id: str) -> list[AssetView]:
        with self.read() as db:
            rows = db.business_assets.list_for_user(tenant_id, scope="brand")
            return [_business_asset_view(db, row) for row in rows]

    def company_logo(self, tenant_id: str) -> AssetView | None:
        """PNG logo on the brand profile, then any owned logo file. No generated-image fallback."""
        with self.read() as db:
            profile = db.brand_profiles.get_for_user(tenant_id)
            preferred: list[tuple[str | None, str]] = []
            if profile is not None:
                preferred.append((profile.logo_png_asset_id, "logo_png"))
                preferred.append((profile.logo_svg_asset_id, "logo_svg"))
            for asset_id, role in preferred:
                if not asset_id:
                    continue
                asset = db.business_assets.get_owned(tenant_id, asset_id)
                if asset is not None and asset.role == role:
                    return _business_asset_view(db, asset)
            for role in ("logo_png", "logo_svg"):
                asset = db.business_assets.latest_for_role(tenant_id, role)
                if asset is not None:
                    return _business_asset_view(db, asset)
        return None

    def catalog_product(self, tenant_id: str, name: str) -> dict[str, Any] | None:
        wanted = name.casefold()
        with self.read() as db:
            match = next((row for row in db.products.list_for_user(tenant_id) if row.name.casefold() == wanted), None)
            if match is None:
                return None
            image_ids = _product_image_ids(db, tenant_id, match.id)
            return {
                "id": match.id,
                "name": match.name,
                "description": match.description,
                "category": match.category,
                "price": _money(match.price),
                "sku": match.sku,
                "offer": match.offer,
                "is_active": bool(match.is_active),
                "image_id": image_ids[0] if image_ids else None,
                "image_ids": image_ids,
            }

    def product_images(
        self,
        tenant_id: str,
        *,
        name: str | None = None,
        product_id: str | None = None,
    ) -> list[AssetView]:
        with self.read() as db:
            chosen = []
            for row in db.products.list_for_user(tenant_id):
                id_ok = product_id is None or row.id == product_id
                name_ok = name is None or row.name.casefold() == name.casefold()
                if product_id is not None and name is not None:
                    if row.id == product_id and row.name.casefold() == name.casefold():
                        chosen.append(row)
                elif id_ok and name_ok and (product_id is not None or name is not None):
                    chosen.append(row)
            views: list[AssetView] = []
            seen: set[str] = set()
            for row in chosen:
                for asset_id in _product_image_ids(db, tenant_id, row.id):
                    if asset_id in seen:
                        continue
                    asset = db.business_assets.get_owned(tenant_id, asset_id)
                    if asset is None or asset.role != "product_image":
                        continue
                    seen.add(asset_id)
                    views.append(_business_asset_view(db, asset, product_name=row.name))
            return views

    def owned_reference(self, tenant_id: str, asset_id: str) -> AssetView | None:
        if not _safe_asset_id(asset_id):
            return None
        with self.read() as db:
            asset = db.business_assets.get_owned(tenant_id, asset_id)
            if asset is not None:
                return _business_asset_view(db, asset)
        return self.owned_asset(tenant_id, asset_id)

    def product_names(self, tenant_id: str) -> list[str]:
        profile = self.business_profile(tenant_id)
        if profile is None:
            return []
        return list(profile["products"])

    def automation(self, tenant_id: str) -> dict[str, Any]:
        with self.read() as db:
            row = db.automation.get_for_user(tenant_id)
            if row is None:
                return default_automation()
            return serialize_automation(row)

    def timezone_name(self, tenant_id: str) -> str:
        return str(self.automation(tenant_id).get("timezone") or DEFAULT_TIMEZONE)

    def owned_asset(self, tenant_id: str, image_id: str) -> AssetView | None:
        with self.read() as db:
            row = db.generated_images.get_owned(tenant_id, image_id)
            return None if row is None else _asset_view(row)

    def list_assets(self, tenant_id: str, *, limit: int = 50) -> list[AssetView]:
        with self.read() as db:
            return [_asset_view(row) for row in db.generated_images.list_for_user(tenant_id, limit=limit)]

    def festival_catalog(self, year: int) -> list[dict[str, Any]]:
        with self.read() as db:
            return FestivalService(db.session).catalog(year)

    def campaigns(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.read() as db:
            return [serialize_campaign(row) for row in db.festivals.list_campaigns(tenant_id)]

    def owned_campaign(self, tenant_id: str, campaign_id: str) -> dict[str, Any] | None:
        with self.read() as db:
            row = db.festivals.get_owned(tenant_id, campaign_id)
            return None if row is None else serialize_campaign(row)

    def fresh_after(self, tenant_id: str, *, days: int = 14) -> datetime:
        moment = self._clock.now(self.timezone_name(tenant_id))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc) - timedelta(days=days)

    def publishing_history(self, tenant_id: str, *, limit: int) -> dict[str, Any] | None:
        """Stored publications for an account this tenant owns. Does not call Meta."""
        with self.read() as db:
            account = db.instagram_accounts.get_primary(tenant_id)
            if account is None or account.user_id != tenant_id:
                return None
            rows = db.posts.list_recent(tenant_id, limit=limit + 1)
            owned = [
                row
                for row in rows
                if row.user_id == tenant_id
                and (row.instagram_account_id is None or row.instagram_account_id == account.id)
            ]
            return {
                "account_status": account.status,
                "instagram_account_id": account.instagram_account_id,
                "posts": [_media_public(row) for row in owned[:limit]],
                "truncated": len(owned) > limit,
            }

    def account_summary(self, tenant_id: str) -> dict[str, Any]:
        with self.read() as db:
            profile = db.business.get_for_user(tenant_id)
            accounts = db.instagram_accounts.list_for_user(tenant_id)
            business = None
            if profile is not None:
                business = {
                    "id": profile.id,
                    "business_name": profile.business_name,
                    "business_type": profile.business_type,
                    "business_category": profile.business_category,
                    "location": profile.location,
                }
            return {
                "business": business,
                "accounts": [_account_public(row) for row in accounts],
            }

    def stored_intelligence(self, tenant_id: str) -> dict[str, Any] | None:
        """Latest normalized account-intelligence context. Raw Graph payloads are not stored."""
        from sqlalchemy import select

        from db.models import InstagramIntelligenceRecord

        with self.read() as db:
            row = db.session.scalar(
                select(InstagramIntelligenceRecord)
                .where(
                    InstagramIntelligenceRecord.user_id == tenant_id,
                    InstagramIntelligenceRecord.kind == "trend",
                )
                .order_by(InstagramIntelligenceRecord.captured_at.desc())
            )
            if row is None:
                return None
            return dict(row.payload or {})

    def recent_media(
        self,
        tenant_id: str,
        *,
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        with self.read() as db:
            rows = db.posts.list_recent(tenant_id, limit=limit + 1, start=start, end=end)
            truncated = len(rows) > limit
            return {"media": [_media_public(row) for row in rows[:limit]], "truncated": truncated}

    def top_content(
        self,
        tenant_id: str,
        *,
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        with self.read() as db:
            rows = db.posts.list_recent(
                tenant_id,
                limit=limit + 1,
                start=start,
                end=end,
                published_only=True,
            )
            truncated = len(rows) > limit
            return {"media": [_media_public(row) for row in rows[:limit]], "truncated": truncated}

    def content_performance(
        self,
        tenant_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        with self.read() as db:
            by_status = db.posts.count_grouped(tenant_id, "status", start=start, end=end)
            by_type = db.posts.count_grouped(
                tenant_id,
                "post_type",
                start=start,
                end=end,
                published_only=True,
            )
            return {
                "by_status": [{"status": label, "count": count} for label, count in by_status],
                "by_type": [{"post_type": label, "published": count} for label, count in by_type],
                "published": sum(count for _label, count in by_type),
            }

    def account_insights(
        self,
        tenant_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        cutoff = self.fresh_after(tenant_id)
        with self.read() as db:
            latest = db.posts.latest_published_at(tenant_id)
            by_type = db.posts.count_grouped(
                tenant_id,
                "post_type",
                start=start,
                end=end,
                published_only=True,
            )
        published = sum(count for _label, count in by_type)
        if published == 0:
            return {"found": False, "stale": False, "source": "instagram_posts", "insights": None}
        latest_utc = _as_utc(latest) if latest is not None else None
        return {
            "found": True,
            "stale": latest_utc is None or latest_utc < cutoff,
            "source": "instagram_posts",
            "insights": {
                "published": published,
                "latest_published_at": latest_utc.isoformat() if latest_utc else None,
                "by_type": [{"post_type": label, "published": count} for label, count in by_type],
            },
        }

    def trend_observations(
        self,
        tenant_id: str,
        *,
        industry: str | None,
        region: str | None,
        festival: str | None,
        content_type: str | None,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        fresh_only: bool,
    ) -> dict[str, Any]:
        as_of = self._utc_now(tenant_id)
        cutoff = self.fresh_after(tenant_id)
        with self.read() as db:
            rows, truncated = db.trend_observations.list_for_user(
                tenant_id,
                industry=industry,
                region=region,
                festival=festival,
                content_type=content_type,
                start=start,
                end=end,
                fresh_after=None,
                current_at=as_of if fresh_only else None,
                legacy_after=cutoff,
                limit=limit,
            )
            stale_excluded = (
                db.trend_observations.count_not_current(
                    tenant_id,
                    industry=industry,
                    region=region,
                    festival=festival,
                    content_type=content_type,
                    start=start,
                    end=end,
                    current_at=as_of,
                    legacy_after=cutoff,
                )
                if fresh_only
                else 0
            )
            catalog = list(db.trend_sources.list_for_user(tenant_id))
            sources = {item.id: item for item in catalog}
            by_url = {item.url: item for item in catalog if item.url}
            observations = [
                _observation_public(
                    row,
                    as_of,
                    cutoff,
                    sources.get(row.source_id),
                    db.trend_evidence.list_for_observation(tenant_id, row.id),
                    by_url,
                )
                for row in rows
            ]
        return {
            "observations": observations,
            "truncated": truncated,
            "stale_excluded": stale_excluded,
            "as_of": self.today(self.timezone_name(tenant_id)).isoformat(),
            "source": "trend_observations",
        }

    def trend_evidence(self, tenant_id: str, observation_id: str) -> dict[str, Any] | None:
        as_of = self._utc_now(tenant_id)
        cutoff = self.fresh_after(tenant_id)
        with self.read() as db:
            row = db.trend_observations.get_owned(tenant_id, observation_id)
            if row is None:
                return None
            catalog = list(db.trend_sources.list_for_user(tenant_id))
            sources = {item.id: item for item in catalog}
            by_url = {item.url: item for item in catalog if item.url}
            payload = _observation_public(
                row,
                as_of,
                cutoff,
                sources.get(row.source_id),
                db.trend_evidence.list_for_observation(tenant_id, row.id),
                by_url,
            )
            payload["evidence"] = _clip(row.evidence, 360)
            return payload

    def _utc_now(self, tenant_id: str) -> datetime:
        moment = self._clock.now(self.timezone_name(tenant_id))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def latest_trend_report(self, tenant_id: str) -> dict[str, Any] | None:
        cutoff = self.fresh_after(tenant_id)
        with self.read() as db:
            row = db.trend_reports.latest_for_user(tenant_id)
            if row is None:
                return None
            observed = _as_utc(row.observed_at)
            return {
                "id": row.id,
                "title": _clip(row.title, 120),
                "summary": _clip(row.summary, 240),
                "industry": row.industry,
                "region": row.region,
                "observation_count": row.observation_count,
                "observed_at": observed.isoformat(),
                "stale": observed < cutoff,
                "source": "trend_reports",
            }

    def active_products(self, tenant_id: str, *, limit: int) -> list[dict[str, Any]]:
        with self.read() as db:
            rows = db.products.list_for_user(tenant_id, active_only=True)
            offers = db.products.list_for_user(tenant_id, offers_only=True)
            offer_ids = {row.id for row in offers}
            items = []
            for row in rows[:limit]:
                items.append(
                    {
                        "id": row.id,
                        "name": _clip(row.name, 80),
                        "category": row.category,
                        "sku": row.sku,
                        "has_offer": row.id in offer_ids,
                    }
                )
            return items

    def brand_snapshot(self, tenant_id: str) -> dict[str, Any]:
        with self.read() as db:
            profile = db.business.get_for_user(tenant_id)
            brand = db.brand_profiles.get_for_user(tenant_id)
            guidelines = db.brand_guidelines.list_for_user(tenant_id)
            return {
                "business_name": None if profile is None else profile.business_name,
                "brand_style": None if profile is None else profile.brand_style,
                "company_name": None if brand is None else brand.company_name,
                "has_guidelines": bool(guidelines),
                "guideline_titles": [_clip(row.title, 80) for row in guidelines[:5]],
            }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clip(text: str | None, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _account_public(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "instagram_account_id": row.instagram_account_id,
        "status": row.status,
        "connected": row.status == "CONNECTED",
    }


def _instagram_permalink(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower()
    if lowered.startswith("https://www.instagram.com/") or lowered.startswith("https://instagram.com/"):
        return value[:180]
    return None


def _media_public(row: Any) -> dict[str, Any]:
    published = row.published_at
    return {
        "id": row.id,
        "status": row.status,
        "post_type": row.post_type,
        "published_at": _as_utc(published).isoformat() if isinstance(published, datetime) else None,
        "instagram_media_id": row.instagram_media_id,
        "permalink": _instagram_permalink(row.permalink),
    }


def _observation_public(
    row: Any,
    as_of: datetime,
    cutoff: datetime,
    source: Any = None,
    evidence_rows: list[Any] | None = None,
    sources_by_url: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from backend.trends.research import freshness_of, language_of, public_keywords, source_name_for

    observed = _as_utc(row.observed_at)
    published = getattr(row, "published_at", None)
    valid_until = getattr(row, "valid_until", None)
    status = getattr(row, "status", "ACTIVE")
    if status != "ACTIVE":
        current = False
    elif isinstance(valid_until, datetime):
        current = _as_utc(valid_until) > as_of
    else:
        current = observed >= cutoff
    keywords = list(getattr(row, "keywords", None) or [])
    confidence = getattr(row, "confidence", None)
    industry = row.industry if row.industry not in {"", "unspecified"} else None
    primary = None
    source_url = None
    source_name = None
    if source is not None and getattr(source, "url", None):
        source_url = source.url
        source_name = source.name
        primary = {
            "source_url": source.url,
            "source_name": source.name,
            "published_at": _as_utc(published).isoformat() if isinstance(published, datetime) else None,
            "observed_at": observed.isoformat(),
            "title": _clip(row.title, 120),
        }
    supporting: list[dict[str, Any]] = []
    for item in evidence_rows or []:
        if getattr(item, "source_type", "") != "supporting":
            continue
        record = item.source_record_id or ""
        named = (sources_by_url or {}).get(record)
        supporting.append(
            {
                "source_url": named.url if named is not None else record,
                "source_name": named.name if named is not None else source_name_for(record),
                "title": _clip(item.excerpt, 120),
                "observed_at": _as_utc(item.observed_at).isoformat() if isinstance(item.observed_at, datetime) else None,
            }
        )
    return {
        "id": row.id,
        "industry": industry,
        "region": row.region or None,
        "festival": row.festival,
        "content_type": row.content_type,
        "trend_type": getattr(row, "trend_type", None),
        "title": _clip(row.title, 120),
        "summary": _clip(row.summary, 240),
        "source": row.source,
        "source_url": source_url,
        "source_name": source_name,
        "published_at": _as_utc(published).isoformat() if isinstance(published, datetime) else None,
        "observed_at": observed.isoformat(),
        "valid_until": _as_utc(valid_until).isoformat() if isinstance(valid_until, datetime) else None,
        "keywords": public_keywords(keywords),
        "content_language": language_of(keywords),
        "evidence": _clip(row.evidence, 240),
        "confidence": None if confidence is None else float(confidence),
        "freshness": freshness_of(keywords),
        "primary_source": primary,
        "supporting_sources": supporting,
        "stale": not current,
        "is_current": current,
    }
