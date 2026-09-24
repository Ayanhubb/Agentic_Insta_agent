"""Read-only access to existing repositories and festival services.

MCP tools project these results. They do not keep a second copy of business data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
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
    """Owned generated image. `prompt_text` is for matching only and is not returned."""

    id: str
    content_type: str | None
    theme: str | None
    mime_type: str | None
    width: int | None
    height: int | None
    approval_status: str | None
    prompt_text: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content_type": self.content_type,
            "theme": self.theme,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "media_url": f"/api/v1/generation/{self.id}/media",
        }


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return [str(item).strip() for item in value if str(item).strip()]


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
            return None if row is None else serialize_guidelines(row)

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
