"""SQLAlchemy ORM models for the Instagram Agentic AI platform.

Table names match the V2 product schema. Extra columns (scheduled_date, state_json)
exist only where persistence or duplicate-protection requires them.
"""

from __future__ import annotations

from datetime import date, datetime, time as dt_time
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, new_id, timestamp_column, utcnow
from db.enums import (
    DEFAULT_DAILY_POSTS_PER_DAY,
    DEFAULT_FESTIVAL_POSTS_PER_FESTIVAL,
    DEFAULT_FESTIVAL_REQUIRED_POSTS,
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


class TimeAsString(TypeDecorator):
    """Accept datetime.time or 'HH:MM' strings; persist VARCHAR for SQLite/PostgreSQL."""

    impl = String(16)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, dt_time):
            return value.strftime("%H:%M")
        return str(value)

    def process_result_value(self, value: Any, dialect) -> str | None:
        return value


def enum_col(enum_cls, *, nullable: bool = False, default=None, name: str | None = None):
    kwargs: dict[str, Any] = {"nullable": nullable}
    if default is not None:
        kwargs["default"] = default
    return mapped_column(
        SAEnum(
            enum_cls,
            name=name or enum_cls.__name__.lower(),
            native_enum=False,
            length=64,
            values_callable=lambda items: [item.value for item in items],
        ),
        **kwargs,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column(on_update=True)

    instagram_accounts: Mapped[list["InstagramAccount"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    business_profile: Mapped["BusinessProfile | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    automation_settings: Mapped["AutomationSettings | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"
    __table_args__ = (UniqueConstraint("user_id", "instagram_account_id", name="uq_instagram_accounts_user_ig"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    instagram_account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default=AccountStatus.CONNECTED.value, nullable=False)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = timestamp_column(on_update=True)

    user: Mapped[User] = relationship(back_populates="instagram_accounts")


class BusinessProfile(Base):
    __tablename__ = "business_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_business_profiles_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    products: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    services: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column(on_update=True)

    user: Mapped[User] = relationship(back_populates="business_profile")


class GeneratedImage(Base):
    __tablename__ = "generated_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    original_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    enhanced_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_status: Mapped[str] = mapped_column(String(64), default=GenerationStatus.PENDING.value, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(64), default=ApprovalStatus.PENDING.value, nullable=False)
    publication_status: Mapped[str] = mapped_column(String(64), default=PostStatus.GENERATED.value, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default=ImageSource.USER_PROMPT.value, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    theme: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstagramPost(Base):
    __tablename__ = "instagram_posts"
    __table_args__ = (
        Index(
            "uq_ig_posts_daily_published",
            "user_id",
            "instagram_account_id",
            "scheduled_date",
            unique=True,
            sqlite_where=text("status = 'PUBLISHED' AND post_type = 'DAILY_RETAIL_POST'"),
            postgresql_where=text("status = 'PUBLISHED' AND post_type = 'DAILY_RETAIL_POST'"),
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR instagram_media_id IS NOT NULL",
            name="ck_instagram_posts_published_requires_media_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    instagram_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("instagram_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    generated_image_id: Mapped[str | None] = mapped_column(
        ForeignKey("generated_images.id", ondelete="SET NULL"), nullable=True
    )
    instagram_media_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    permalink: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default=PostStatus.GENERATED.value, nullable=False, index=True)
    post_type: Mapped[str] = mapped_column(String(64), default=PostType.USER_PROMPT.value, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
    # Calendar date in the account timezone. Required for DAILY_RETAIL_POST duplicate protection.
    scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    agent_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    # String PK so V1 AgentState.task_id values (uuid or test ids) persist unchanged.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(64), default=TaskType.INSTAGRAM_PUBLISH.value, nullable=False)
    trigger: Mapped[str] = mapped_column(String(64), default=TaskTrigger.USER.value, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending", index=True)
    current_step: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
    state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="AgentEvent.timestamp"
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    from_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    task: Mapped[AgentTask] = relationship(back_populates="events")


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "instagram_account_id", "job_type", name="uq_scheduled_jobs_user_account_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    instagram_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("instagram_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_type: Mapped[str] = mapped_column(String(64), default=JobType.DAILY_RETAIL_POST.value, nullable=False)
    schedule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default=JobStatus.IDLE.value, nullable=False)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column(on_update=True)


class FestivalCampaign(Base):
    __tablename__ = "festival_campaigns"
    __table_args__ = (
        UniqueConstraint("user_id", "festival_name", "year", name="uq_festival_campaigns_user_name_year"),
        CheckConstraint("required_posts >= 1", name="ck_festival_campaigns_required_posts"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    festival_name: Mapped[str] = mapped_column(String(128), nullable=False)
    festival_date: Mapped[date] = mapped_column(Date, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    required_posts: Mapped[int] = mapped_column(Integer, default=DEFAULT_FESTIVAL_REQUIRED_POSTS, nullable=False)
    generated_posts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_posts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column(on_update=True)

    posts: Mapped[list["FestivalPost"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", order_by="FestivalPost.sequence_number"
    )

    @property
    def remaining_posts(self) -> int:
        return max(0, int(self.required_posts or 0) - int(self.published_posts or 0))


class FestivalPost(Base):
    __tablename__ = "festival_posts"
    __table_args__ = (UniqueConstraint("campaign_id", "sequence_number", name="uq_festival_posts_campaign_seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("festival_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generated_image_id: Mapped[str | None] = mapped_column(
        ForeignKey("generated_images.id", ondelete="SET NULL"), nullable=True
    )
    post_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default=FestivalPostStatus.PENDING.value, nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign: Mapped[FestivalCampaign] = relationship(back_populates="posts")


class AutomationSettings(Base):
    __tablename__ = "automation_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_automation_settings_user_id"),
        CheckConstraint("daily_posts_per_day >= 1", name="ck_automation_daily_posts_per_day"),
        CheckConstraint("festival_posts_per_festival >= 1", name="ck_automation_festival_posts"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    daily_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    daily_posts_per_day: Mapped[int] = mapped_column(Integer, default=DEFAULT_DAILY_POSTS_PER_DAY, nullable=False)
    daily_post_time: Mapped[str | None] = mapped_column(TimeAsString, nullable=True)
    festival_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    festival_posts_per_festival: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_FESTIVAL_POSTS_PER_FESTIVAL, nullable=False
    )
    auto_daily_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_festival_publish: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE, nullable=False)
    pre_festival_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    allow_same_day_festival_posts: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = timestamp_column()
    updated_at: Mapped[datetime] = timestamp_column(on_update=True)

    user: Mapped[User] = relationship(back_populates="automation_settings")


class AuthSession(Base):
    """Login sessions for JWT `jti`. Not an Instagram publishing concern."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = timestamp_column()


class DailyPostSlot(Base):
    """Idempotency slot so a scheduler tick cannot double-claim a local date."""

    __tablename__ = "daily_post_slots"
    __table_args__ = (
        UniqueConstraint("user_id", "instagram_account_id", "local_date", name="uq_daily_post_slots_user_account_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    instagram_account_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CLAIMED", nullable=False)
    post_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
