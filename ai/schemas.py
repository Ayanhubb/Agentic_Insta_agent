"""Pydantic schemas for LLM content plans and generated images.

These are backend-only contracts. They never include API keys, OAuth tokens,
database credentials, or Instagram publish instructions.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "product_promotion",
        "product",
        "promotion",
        "brand",
        "lifestyle",
        "educational",
        "seasonal",
        "festival",
        "new_arrival",
        "customer_focused",
    }
)

ALLOWED_PLAN_REASONS = frozenset(
    {
        "user_prompt",
        "daily_automation",
        "festival_campaign",
        "festival_automation",
    }
)

MIN_IMAGE_PROMPT_LENGTH = 20
MAX_IMAGE_PROMPT_LENGTH = 4000
MAX_THEME_LENGTH = 120
MAX_BUSINESS_CONTEXT_LENGTH = 2000

_SAFE_USER_ID = re.compile(r"[^A-Za-z0-9_-]")


class ContentSource(str, Enum):
    USER_PROMPT = "USER_PROMPT"
    DAILY_AUTOMATION = "DAILY_AUTOMATION"
    FESTIVAL_AUTOMATION = "FESTIVAL_AUTOMATION"


class GenerationStatus(str, Enum):
    GENERATED = "GENERATED"
    GENERATION_FAILED = "GENERATION_FAILED"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BusinessProfileContext(BaseModel):
    """Subset of a business profile the LLM is allowed to see."""

    model_config = ConfigDict(extra="ignore")

    business_name: str | None = None
    business_type: str | None = None
    business_category: str | None = None
    description: str | None = None
    target_audience: str | None = None
    location: str | None = None
    brand_style: str | None = None
    preferred_language: str | None = None
    products: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)


class FestivalContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    festival_name: str | None = None
    festival_date: str | None = None
    year: int | None = None
    required_posts: int | None = None


class ContentHistoryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    theme: str | None = None
    content_type: str | None = None
    image_prompt: str | None = None
    created_at: str | None = None


class ContentPlanRequest(BaseModel):
    """Inputs the backend assembles for the LLM. The model cannot fetch these itself."""

    model_config = ConfigDict(extra="ignore")

    user_prompt: str | None = None
    business_profile: BusinessProfileContext | None = None
    festival: FestivalContext | None = None
    recent_content: list[ContentHistoryItem] = Field(default_factory=list)
    current_date: str | None = None
    reason_hint: str | None = None

    def recent_themes(self) -> list[str]:
        themes: list[str] = []
        seen: set[str] = set()
        for item in self.recent_content:
            theme = (item.theme or "").strip()
            key = theme.lower()
            if theme and key not in seen:
                seen.add(key)
                themes.append(theme)
        return themes


class ContentPlan(BaseModel):
    """Schema-validated LLM output. Extra fields are ignored, never executed."""

    model_config = ConfigDict(extra="ignore")

    content_type: str
    theme: str
    image_prompt: str
    business_context: str
    reason: str

    @field_validator("content_type")
    @classmethod
    def _content_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        if normalized not in ALLOWED_CONTENT_TYPES:
            raise ValueError("content_type is not a supported value")
        return normalized

    @field_validator("theme")
    @classmethod
    def _theme(cls, value: str) -> str:
        theme = value.strip()
        if not theme:
            raise ValueError("theme is required")
        if len(theme) > MAX_THEME_LENGTH:
            raise ValueError("theme is too long")
        return theme

    @field_validator("image_prompt")
    @classmethod
    def _image_prompt(cls, value: str) -> str:
        return validate_image_prompt(value)

    @field_validator("business_context")
    @classmethod
    def _business_context(cls, value: str) -> str:
        context = value.strip()
        if not context:
            raise ValueError("business_context is required")
        return context[:MAX_BUSINESS_CONTEXT_LENGTH]

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_").replace("-", "_")
        if normalized not in ALLOWED_PLAN_REASONS:
            raise ValueError("reason is not a supported value")
        return normalized


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt: str
    user_id: str
    original_prompt: str | None = None
    source: ContentSource = ContentSource.USER_PROMPT
    mime_type: str = "image/png"

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        return validate_image_prompt(value)

    @field_validator("user_id")
    @classmethod
    def _user_id(cls, value: str) -> str:
        user_id = value.strip()
        if not user_id:
            raise ValueError("user_id is required")
        return user_id

    @model_validator(mode="after")
    def _default_original(self) -> "ImageGenerationRequest":
        if not self.original_prompt:
            self.original_prompt = self.prompt
        return self


class StoredGeneratedImage(BaseModel):
    filename: str
    storage_path: str
    mime_type: str
    size_bytes: int = 0
    public_url: str | None = None


class GeneratedImage(BaseModel):
    """Internal generated-image record. Never includes API keys or Instagram media ids."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    original_prompt: str
    enhanced_prompt: str
    model: str
    provider: str
    filename: str
    storage_path: str
    mime_type: str
    width: int | None = None
    height: int | None = None
    generation_status: GenerationStatus = GenerationStatus.GENERATED
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    source: ContentSource = ContentSource.USER_PROMPT
    public_url: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: datetime | None = None

    def public_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("openai_api_key", None)
        payload.pop("api_key", None)
        return payload


def validate_image_prompt(value: str) -> str:
    prompt = (value or "").strip()
    if len(prompt) < MIN_IMAGE_PROMPT_LENGTH:
        raise ValueError("The image prompt is missing or too short.")
    if len(prompt) > MAX_IMAGE_PROMPT_LENGTH:
        raise ValueError("The image prompt exceeds the maximum allowed length.")
    return prompt


def sanitize_user_id(user_id: str) -> str:
    cleaned = _SAFE_USER_ID.sub("", (user_id or "").strip())
    return cleaned[:64] or "unknown"


def content_plan_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "content_type",
            "theme",
            "image_prompt",
            "business_context",
            "reason",
        ],
        "properties": {
            "content_type": {
                "type": "string",
                "enum": sorted(ALLOWED_CONTENT_TYPES),
            },
            "theme": {"type": "string"},
            "image_prompt": {"type": "string"},
            "business_context": {"type": "string"},
            "reason": {"type": "string", "enum": sorted(ALLOWED_PLAN_REASONS)},
        },
    }
