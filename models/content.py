"""Content Strategy Agent schemas.

These models are the contract between the Content Agent, the LLM provider,
persistence, and the scheduler. They do not call Instagram.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContentType(str, Enum):
    PRODUCT = "PRODUCT"
    PROMOTION = "PROMOTION"
    BRAND = "BRAND"
    LIFESTYLE = "LIFESTYLE"
    EDUCATIONAL = "EDUCATIONAL"
    SEASONAL = "SEASONAL"
    FESTIVAL = "FESTIVAL"
    NEW_ARRIVAL = "NEW_ARRIVAL"
    CUSTOMER_FOCUSED = "CUSTOMER_FOCUSED"


class ContentMode(str, Enum):
    USER_PROMPT = "USER_PROMPT"
    DAILY = "DAILY"
    FESTIVAL = "FESTIVAL"


class ContentSource(str, Enum):
    USER_PROMPT = "USER_PROMPT"
    DAILY_AUTOMATION = "DAILY_AUTOMATION"
    FESTIVAL_AUTOMATION = "FESTIVAL_AUTOMATION"


class ApprovalStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    AUTO_APPROVED = "AUTO_APPROVED"


class ContentTaskStatus(str, Enum):
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED_PENDING_PUBLISH = "APPROVED_PENDING_PUBLISH"
    FAILED = "FAILED"


class BusinessProfileSnapshot(BaseModel):
    """Duck-typed view of the `business_profiles` persistence model."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str | None = None
    user_id: str | None = None
    business_name: str
    business_type: str | None = None
    business_category: str | None = None
    description: str | None = None
    target_audience: str | None = None
    location: str | None = None
    brand_style: str | None = None
    preferred_language: str | None = None
    products: list[str] | str | None = None
    services: list[str] | str | None = None


class AutomationSettingsSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)

    daily_enabled: bool = False
    daily_posts_per_day: int = 1
    daily_post_time: str | None = None
    festival_enabled: bool = False
    festival_posts_per_festival: int = 2
    auto_daily_publish: bool = False
    auto_festival_publish: bool = False
    timezone: str = "Asia/Kolkata"

    @field_validator("daily_post_time", mode="before")
    @classmethod
    def _coerce_daily_post_time(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, time):
            return value.strftime("%H:%M")
        return str(value)


class FestivalContext(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)

    name: str
    date: date | datetime | None = None
    year: int | None = None
    campaign_id: str | None = None
    required_posts: int = 2
    generated_posts: int = 0
    published_posts: int = 0
    notes: str | None = None
    festival_name: str | None = None

    @property
    def display_name(self) -> str:
        return (self.festival_name or self.name or "").strip()


class RecentContent(BaseModel):
    """Normalized recent post or generated-image history for diversity."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str | None = None
    content_type: str | None = None
    theme: str | None = None
    prompt: str | None = None
    original_prompt: str | None = None
    enhanced_prompt: str | None = None
    product: str | None = None
    products: list[str] | str | None = None
    category: str | None = None
    festival_name: str | None = None
    source: str | None = None
    status: str | None = None
    created_at: datetime | None = None

    def prompt_text(self) -> str:
        for value in (self.enhanced_prompt, self.original_prompt, self.prompt, self.theme):
            if value and str(value).strip():
                return str(value)
        return ""


class ContentPlan(BaseModel):
    """Structured plan produced by the LLM and validated by the Content Agent."""

    model_config = ConfigDict(extra="ignore")

    content_type: ContentType
    theme: str
    image_prompt: str
    business_context: str
    reason: str
    caption_hint: str | None = None
    featured_product_or_service: str | None = None
    audience_angle: str | None = None
    mode: ContentMode | None = None
    source: ContentSource | None = None
    diversity_notes: str | None = None
    product_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    logo_required: bool = False
    logo_asset_id: str | None = None
    offer_text: str | None = None
    qa_requirements: dict[str, Any] | None = None
    canva_action: str | None = None


class GeneratedImageSnapshot(BaseModel):
    """Minimal generated-image record the Content Agent persists and returns."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str | None = None
    user_id: str | None = None
    original_prompt: str | None = None
    enhanced_prompt: str | None = None
    model: str | None = None
    provider: str | None = None
    filename: str | None = None
    storage_path: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    generation_status: str = "GENERATED"
    approval_status: str = ApprovalStatus.PENDING_APPROVAL.value
    source: str | None = None
    content_type: str | None = None
    theme: str | None = None


class DiversityVerdict(BaseModel):
    accepted: bool
    reason: str
    score: float = 0.0
    collided_with: str | None = None
    backend: str = "token_overlap_v1"
    blocking: bool = True


class InstagramHandoff(BaseModel):
    """Data package for the Instagram Agent. Not a publish call."""

    ready: bool = False
    user_id: str
    generated_image_id: str | None = None
    image_path: str | None = None
    caption: str | None = None
    source: str | None = None
    content_type: str | None = None
    instagram_account_id: str | None = None
    requires_instagram_agent: bool = True
    auto_approved: bool = False


class ContentTask(BaseModel):
    id: str
    user_id: str
    mode: ContentMode
    source: ContentSource
    status: ContentTaskStatus
    plan: ContentPlan
    generated_image_id: str | None = None
    approval_status: ApprovalStatus
    requires_approval: bool
    handoff_to_instagram: bool = False
    published: bool = False
    error: str | None = None


class ContentStrategyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str
    mode: ContentMode
    user_prompt: str | None = None
    business_profile: BusinessProfileSnapshot | dict[str, Any] | None = None
    recent_posts: list[RecentContent | dict[str, Any]] = Field(default_factory=list)
    recent_generated: list[RecentContent | dict[str, Any]] = Field(default_factory=list)
    festival: FestivalContext | dict[str, Any] | None = None
    automation: AutomationSettingsSnapshot | dict[str, Any] | None = None
    instagram_account_id: str | None = None
    now: datetime | None = None
    generate_image: bool = True
    task_id: str | None = None
    correlation_id: str | None = None
    request_id: str | None = None

    @field_validator("business_profile", mode="before")
    @classmethod
    def _coerce_business_profile(cls, value: Any) -> Any:
        if value is None or isinstance(value, (dict, BusinessProfileSnapshot)):
            return value
        return BusinessProfileSnapshot.model_validate(value)

    @field_validator("automation", mode="before")
    @classmethod
    def _coerce_automation(cls, value: Any) -> Any:
        if value is None or isinstance(value, (dict, AutomationSettingsSnapshot)):
            return value
        return AutomationSettingsSnapshot.model_validate(value)


class ContentStrategyResult(BaseModel):
    task: ContentTask
    plan: ContentPlan
    generated_image: GeneratedImageSnapshot | None = None
    approval_status: ApprovalStatus
    diversity: DiversityVerdict
    handoff: InstagramHandoff
    published: Literal[False] = False
    llm_attempts: int = 1
    mode: ContentMode
    source: ContentSource
