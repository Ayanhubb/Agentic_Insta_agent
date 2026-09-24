"""Pydantic contracts for the Content Agent creative workflow.

These models are the only structure sent to the planner and the only structure
accepted back. They do not publish, and they do not carry secrets.
"""

from __future__ import annotations

import re
from datetime import date as calendar_date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.mcp.client import sanitize_payload


class CampaignType(str, Enum):
    USER_PROMPT = "USER_PROMPT"
    DAILY = "DAILY"
    FESTIVAL = "FESTIVAL"


class CanvaAction(str, Enum):
    NONE = "none"
    APPLY_TEMPLATE = "apply_template"
    USE_REFERENCE = "use_reference"


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class SourcedProduct(BaseModel):
    """A product the business actually supplied. Price and offer stay empty unless sourced."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str | None = None
    price: str | None = None
    offer: str | None = None
    asset_id: str | None = None

    @field_validator("price", "offer", "description", "asset_id", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> str | None:
        return _blank_to_none(value)


class SourcedOffer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str
    product_id: str | None = None
    price: str | None = None

    @field_validator("price", "product_id", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> str | None:
        return _blank_to_none(value)


class SourcedFestival(BaseModel):
    """Festival name and date copied from Festival MCP. The date is never computed here."""

    model_config = ConfigDict(extra="ignore")

    name: str
    date: calendar_date | None = None
    year: int | None = None
    campaign_id: str | None = None

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> calendar_date | None:
        if value is None or value == "":
            return None
        if isinstance(value, calendar_date):
            return value
        text = str(value).strip()
        if len(text) >= 10:
            try:
                return calendar_date.fromisoformat(text[:10])
            except ValueError:
                return None
        return None


class BusinessFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    business_type: str | None = None
    category: str | None = None
    description: str | None = None
    location: str | None = None
    language: str | None = None

    @field_validator("business_type", "category", "description", "location", "language", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> str | None:
        return _blank_to_none(value)


class BrandFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    audience: str | None = None
    style: str | None = None
    language: str | None = None
    asset_ids: list[str] = Field(default_factory=list)

    @field_validator("audience", "style", "language", mode="before")
    @classmethod
    def _empty(cls, value: Any) -> str | None:
        return _blank_to_none(value)


class CanvaFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    queried: bool = False
    action: CanvaAction = CanvaAction.NONE
    asset_ids: list[str] = Field(default_factory=list)


class CreativeContext(BaseModel):
    """Normalized facts gathered from MCP. This is what the planner is allowed to see."""

    model_config = ConfigDict(extra="ignore")

    user_id: str
    campaign_type: CampaignType
    business: BusinessFact
    brand: BrandFact
    products: list[SourcedProduct] = Field(default_factory=list)
    offers: list[SourcedOffer] = Field(default_factory=list)
    festival: SourcedFestival | None = None
    canva: CanvaFact = Field(default_factory=CanvaFact)
    user_prompt: str | None = None
    current_date: calendar_date

    def for_model(self) -> dict[str, Any]:
        """Prompt payload with the tenant id and any secret-shaped text removed."""
        payload = self.model_dump(mode="json", exclude={"user_id"})
        safe = sanitize_payload(payload)
        return safe if isinstance(safe, dict) else {}


class CreativePlan(BaseModel):
    """Structured plan returned by DeepSeek and checked against CreativeContext."""

    model_config = ConfigDict(extra="ignore")

    campaign_type: CampaignType
    festival: str | None = None
    business_type: str | None = None
    audience: str | None = None
    caption: str
    hashtags: list[str] = Field(min_length=1)
    creative_direction: str
    image_prompt: str
    product_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    canva_action: CanvaAction = CanvaAction.NONE
    qa_requirements: list[str] = Field(min_length=1)

    @field_validator("festival", "business_type", "audience", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return _blank_to_none(value)

    @field_validator("caption", "creative_direction")
    @classmethod
    def _required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("required text is missing")
        return text

    @field_validator("image_prompt")
    @classmethod
    def _image_prompt(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 20:
            raise ValueError("image_prompt is too short")
        if len(text) > 4000:
            raise ValueError("image_prompt is too long")
        return text

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, value: list[str]) -> list[str]:
        tags: list[str] = []
        for item in value:
            token = re.sub(r"[^A-Za-z0-9]", "", str(item).lstrip("#"))
            if token:
                tags.append(f"#{token[:40]}")
        if not tags:
            raise ValueError("hashtags are required")
        return tags[:10]

    @field_validator("qa_requirements")
    @classmethod
    def _requirements(cls, value: list[str]) -> list[str]:
        items = [str(item).strip() for item in value if str(item).strip()]
        if not items:
            raise ValueError("qa_requirements are required")
        return items[:8]

    @field_validator("product_ids", "asset_ids")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]


class ContentOrchestrationRequest(BaseModel):
    """Caller input. The tenant id is taken from authentication, not from model output."""

    model_config = ConfigDict(extra="ignore")

    user_id: str
    authenticated: bool = False
    mode: str = "USER_PROMPT"
    user_prompt: str | None = None
    festival: str | None = None
    product_id: str | None = None
    offer_id: str | None = None
    use_canva: bool | None = None
    instagram_account_id: str | None = None
    task_id: str | None = None


class ImageQAVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    passed: bool
    issues: list[str] = Field(default_factory=list)

    @field_validator("issues")
    @classmethod
    def _issues(cls, value: list[str]) -> list[str]:
        return [str(item).strip()[:200] for item in value if str(item).strip()][:8]
