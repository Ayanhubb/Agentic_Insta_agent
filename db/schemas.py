"""Pydantic create/read schemas. ORM instances can be converted with model_validate."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from db.enums import (
    DEFAULT_TIMEZONE,
    AccountStatus,
    ApprovalStatus,
    FestivalPostStatus,
    GenerationStatus,
    ImageSource,
    JobStatus,
    JobType,
    PostStatus,
    PostType,
    TaskTrigger,
    TaskType,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")


class UserCreate(BaseModel):
    email: EmailStr
    password_hash: str
    is_active: bool = True
    is_admin: bool = False
    must_change_password: bool = False


class UserRead(ORMModel):
    id: str
    email: str
    is_active: bool
    is_admin: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime


class UserRecord(UserRead):
    """Internal record including the password hash. Never send this to React."""

    password_hash: str


class InstagramAccountCreate(BaseModel):
    user_id: str
    instagram_account_id: str
    access_token: str
    token_expires_at: datetime | None = None
    status: AccountStatus = AccountStatus.CONNECTED


class InstagramAccountRead(ORMModel):
    id: str
    user_id: str
    instagram_account_id: str
    token_expires_at: datetime | None
    status: AccountStatus
    connected_at: datetime
    updated_at: datetime
    has_encrypted_token: bool = True


class BusinessProfileWrite(BaseModel):
    business_name: str
    business_type: str | None = None
    business_category: str | None = None
    description: str | None = None
    target_audience: str | None = None
    location: str | None = None
    brand_style: str | None = None
    preferred_language: str | None = None
    products: list[str] | None = None
    services: list[str] | None = None


class BusinessProfileRead(BusinessProfileWrite, ORMModel):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class GeneratedImageCreate(BaseModel):
    user_id: str
    original_prompt: str
    enhanced_prompt: str | None = None
    model: str | None = None
    provider: str | None = None
    filename: str | None = None
    storage_path: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    generation_status: GenerationStatus = GenerationStatus.PENDING
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    source: ImageSource | str = ImageSource.USER_PROMPT


class GeneratedImageRead(GeneratedImageCreate, ORMModel):
    id: str
    created_at: datetime
    approved_at: datetime | None = None


class InstagramPostCreate(BaseModel):
    id: str | None = None
    user_id: str | None = None
    instagram_account_id: str | None = None
    generated_image_id: str | None = None
    instagram_media_id: str | None = None
    permalink: str | None = None
    status: PostStatus | str = PostStatus.GENERATED
    post_type: PostType | str = PostType.USER_PROMPT
    published_at: datetime | None = None
    error: str | None = None
    scheduled_date: date | None = None
    agent_task_id: str | None = None
    verified: bool = False


class InstagramPostRead(ORMModel):
    id: str
    user_id: str | None
    instagram_account_id: str | None
    generated_image_id: str | None
    instagram_media_id: str | None
    permalink: str | None
    status: PostStatus
    post_type: PostType
    published_at: datetime | None
    error: str | None
    created_at: datetime
    scheduled_date: date | None = None
    agent_task_id: str | None = None


class AgentTaskCreate(BaseModel):
    id: str
    user_id: str | None = None
    task_type: TaskType | str = TaskType.INSTAGRAM_PUBLISH
    trigger: TaskTrigger | str = TaskTrigger.USER
    status: str = "pending"
    current_step: str | None = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    state_json: dict[str, Any] | None = None


class AgentTaskRead(ORMModel):
    id: str
    user_id: str | None
    task_type: TaskType
    trigger: TaskTrigger
    status: str
    current_step: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    created_at: datetime


class AgentEventCreate(BaseModel):
    task_id: str
    from_state: str | None = None
    to_state: str | None = None
    tool: str | None = None
    result: str | None = None
    observation: dict[str, Any] | None = None
    timestamp: datetime | None = None


class AgentEventRead(ORMModel):
    id: str
    task_id: str
    from_state: str | None
    to_state: str | None
    tool: str | None
    result: str | None
    observation: dict[str, Any] | None
    timestamp: datetime


class ScheduledJobCreate(BaseModel):
    user_id: str
    instagram_account_id: str | None = None
    job_type: JobType
    schedule: str | None = None
    enabled: bool = True
    next_run_at: datetime | None = None
    status: JobStatus = JobStatus.IDLE


class ScheduledJobRead(ORMModel):
    id: str
    user_id: str
    instagram_account_id: str | None
    job_type: JobType
    schedule: str | None
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    status: JobStatus
    created_at: datetime
    updated_at: datetime


class FestivalCampaignCreate(BaseModel):
    user_id: str
    festival_name: str
    festival_date: date
    year: int | None = None
    required_posts: int = 2
    enabled: bool = True


class FestivalCampaignRead(ORMModel):
    id: str
    user_id: str
    festival_name: str
    festival_date: date
    year: int
    required_posts: int
    generated_posts: int
    published_posts: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class FestivalPostCreate(BaseModel):
    campaign_id: str
    sequence_number: int
    generated_image_id: str | None = None
    post_id: str | None = None
    status: FestivalPostStatus = FestivalPostStatus.PENDING
    scheduled_for: datetime | None = None


class FestivalPostRead(ORMModel):
    id: str
    campaign_id: str
    generated_image_id: str | None
    post_id: str | None
    sequence_number: int
    status: FestivalPostStatus
    scheduled_for: datetime | None
    published_at: datetime | None


class AutomationSettingsWrite(BaseModel):
    daily_enabled: bool = False
    daily_posts_per_day: int = Field(default=1, ge=1)
    daily_post_time: str | None = None
    festival_enabled: bool = False
    festival_posts_per_festival: int = Field(default=2, ge=1)
    auto_daily_publish: bool = False
    auto_festival_publish: bool = False
    timezone: str = DEFAULT_TIMEZONE
    pre_festival_days: int = Field(default=1, ge=0)
    allow_same_day_festival_posts: bool = False


class AutomationSettingsRead(AutomationSettingsWrite, ORMModel):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class BrandColorWrite(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    hex: str

    @field_validator("hex")
    @classmethod
    def _hex(cls, value: str) -> str:
        text = value.strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
            raise ValueError("Brand colors must be #RRGGBB.")
        return text.lower()


class FontMetadataWrite(BaseModel):
    family: str = Field(min_length=1, max_length=80)
    weight: str | None = Field(default=None, max_length=32)
    style: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=200)


class GuidelineWrite(BaseModel):
    title: str = Field(default="Brand guidelines", max_length=160)
    body: str = Field(min_length=1, max_length=20000)


class BrandWrite(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    instagram_handle: str | None = Field(default=None, max_length=31)
    brand_colors: list[BrandColorWrite] = Field(default_factory=list, max_length=24)
    fonts: list[FontMetadataWrite] = Field(default_factory=list, max_length=24)
    logo_png_asset_id: str | None = None
    logo_svg_asset_id: str | None = None
    guidelines: str | list[GuidelineWrite] | None = None

    @field_validator("company_name")
    @classmethod
    def _company_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Company name is required.")
        return cleaned

    @field_validator("website")
    @classmethod
    def _website(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Website must be an http(s) URL.")
        if parsed.username or parsed.password:
            raise ValueError("Website must not include credentials.")
        return cleaned

    @field_validator("instagram_handle")
    @classmethod
    def _handle(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lstrip("@")
        if not cleaned:
            return None
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", cleaned):
            raise ValueError("Instagram handle is invalid.")
        return cleaned


class ProductWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=128)
    price: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999.99"))
    sku: str | None = Field(default=None, max_length=64)
    is_active: bool = True
    offer: str | None = Field(default=None, max_length=500)
    image_asset_id: str | None = None

    @field_validator("name", "description", "category", "offer", "sku", mode="before")
    @classmethod
    def _strip_optional(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value:
            raise ValueError("Product name is required.")
        return value

    @field_validator("sku")
    @classmethod
    def _sku(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
            raise ValueError("SKU is invalid.")
        return value

    @field_validator("description", "category", "offer")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @field_validator("price")
    @classmethod
    def _price(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(Decimal("0.01"))


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=128)
    price: Decimal | None = Field(default=None, ge=0, le=Decimal("9999999999.99"))
    sku: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None
    offer: str | None = Field(default=None, max_length=500)
    image_asset_id: str | None = None

    @field_validator("name", "description", "category", "offer", "sku", mode="before")
    @classmethod
    def _strip_optional(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("sku")
    @classmethod
    def _sku(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
            raise ValueError("SKU is invalid.")
        return value

    @field_validator("description", "category", "offer")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @field_validator("price")
    @classmethod
    def _price(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(Decimal("0.01"))
