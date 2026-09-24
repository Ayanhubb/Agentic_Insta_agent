"""Inputs and outputs for the content opportunity engine.

Facts are copied from Trend Intelligence, the business catalog, and the festival
date table. This module does not publish and does not fill in missing facts.
"""

from __future__ import annotations

from datetime import date as calendar_date
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class BusinessVertical(str, Enum):
    RETAIL = "retail"
    FOOD_AND_BEVERAGE = "food_and_beverage"


class OpportunityKind(str, Enum):
    PRODUCT_LAUNCH = "product_launch"
    PRODUCT_SHOWCASE = "product_showcase"
    SEASONAL_CAMPAIGN = "seasonal_campaign"
    FESTIVAL_CAMPAIGN = "festival_campaign"
    OFFER_CAMPAIGN = "offer_campaign"
    BRAND_STORYTELLING = "brand_storytelling"
    EDUCATIONAL_CONTENT = "educational_content"
    DISH_SHOWCASE = "dish_showcase"
    MENU_PROMOTION = "menu_promotion"
    FESTIVAL_FOOD = "festival_food"
    SEASONAL_MENU = "seasonal_menu"
    OFFER = "offer"
    RESTAURANT_EXPERIENCE = "restaurant_experience"
    BEHIND_THE_SCENES = "behind_the_scenes"
    LOCAL_RELEVANCE = "local_relevance"


class RecommendedFormat(str, Enum):
    SINGLE_IMAGE = "single_image"
    CAROUSEL = "carousel"
    REEL_CONCEPT = "reel_concept"
    STORY_CONCEPT = "story_concept"


# The Instagram Agent publishes one still image. Other formats stay concepts.
INSTAGRAM_AGENT_FORMATS = frozenset({RecommendedFormat.SINGLE_IMAGE})


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    statement: str
    source: str | None = None

    @field_validator("statement")
    @classmethod
    def _statement(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("evidence statement is empty")
        return text

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: Any) -> str | None:
        return _blank(value)


class TrendBrief(BaseModel):
    """Evidence package produced by Trend Intelligence. Dates and metrics are not inferred here."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    expires_at: datetime
    region: str | None = None
    themes: list[str] = Field(default_factory=list)
    festival_name: str | None = None
    suggested_product_names: list[str] = Field(default_factory=list)
    suggested_offer_ids: list[str] = Field(default_factory=list)

    @field_validator("expires_at")
    @classmethod
    def _expires(cls, value: datetime) -> datetime:
        return _aware(value)

    @field_validator("region", "festival_name", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _blank(value)

    @field_validator("themes", "suggested_product_names", "suggested_offer_ids")
    @classmethod
    def _texts(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]


class BusinessProfileFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    business_name: str
    business_type: str | None = None
    business_category: str | None = None
    description: str | None = None
    location: str | None = None
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)

    @field_validator("business_type", "business_category", "description", "location", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _blank(value)


class ProductFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str | None = None
    category: str | None = None
    price: str | None = None
    offer: str | None = None
    is_active: bool = True

    @field_validator("description", "category", "price", "offer", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _blank(value)


class OfferFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    product_id: str | None = None
    price: str | None = None

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("offer text is empty")
        return text

    @field_validator("product_id", "price", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _blank(value)


class BrandGuidelinesFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    style: str | None = None
    audience: str | None = None
    language: str | None = None
    claims: list[str] = Field(default_factory=list)

    @field_validator("style", "audience", "language", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _blank(value)

    @field_validator("claims")
    @classmethod
    def _claims(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]


class FestivalContextFact(BaseModel):
    """Festival name and date already resolved by festival intelligence."""

    model_config = ConfigDict(extra="ignore")

    name: str
    date: calendar_date | None = None
    year: int | None = None
    region: str | None = None
    campaign_id: str | None = None

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> calendar_date | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, calendar_date):
            return value
        text = str(value).strip()
        if len(text) >= 10:
            return calendar_date.fromisoformat(text[:10])
        return None


class PerformanceFact(BaseModel):
    """A metric only when Trend Intelligence or history already measured it."""

    model_config = ConfigDict(extra="ignore")

    theme: str | None = None
    content_type: str | None = None
    metric_name: str | None = None
    metric_value: float | None = None

    @field_validator("theme", "content_type", "metric_name", mode="before")
    @classmethod
    def _optional(cls, value: Any) -> str | None:
        return _blank(value)


class OpportunityRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trend: TrendBrief
    business: BusinessProfileFacts
    products: list[ProductFact] = Field(default_factory=list)
    offers: list[OfferFact] = Field(default_factory=list)
    brand: BrandGuidelinesFact = Field(default_factory=BrandGuidelinesFact)
    festival: FestivalContextFact | None = None
    performance: list[PerformanceFact] = Field(default_factory=list)
    now: datetime
    vertical: BusinessVertical | None = None

    @field_validator("now")
    @classmethod
    def _now(cls, value: datetime) -> datetime:
        return _aware(value)


class ContentOpportunity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    why_now: str
    evidence: list[str]
    recommended_format: RecommendedFormat
    recommended_product: str | None = None
    recommended_offer: str | None = None
    festival_context: dict[str, Any] | None = None
    creative_direction: str
    audience_context: str | None = None
    expiration: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    kind: OpportunityKind
    vertical: BusinessVertical
    product_id: str | None = None
    offer_id: str | None = None
    instagram_can_publish: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "why_now": self.why_now,
            "evidence": list(self.evidence),
            "recommended_format": self.recommended_format.value,
            "recommended_product": self.recommended_product,
            "recommended_offer": self.recommended_offer,
            "festival_context": self.festival_context,
            "creative_direction": self.creative_direction,
            "audience_context": self.audience_context,
            "expiration": self.expiration.isoformat(),
            "confidence": self.confidence,
        }
