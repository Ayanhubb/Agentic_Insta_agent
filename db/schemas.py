"""Pydantic create/read schemas. ORM instances can be converted with model_validate."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
