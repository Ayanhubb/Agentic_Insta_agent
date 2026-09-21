"""Publication records, Instagram accounts, and verified-count statistics.

The Instagram Agent is the only writer of PUBLISHED. Counts are derived from
verified PUBLISHED rows — never from GENERATED, APPROVED, PUBLISHING, FAILED,
or AMBIGUOUS_PUBLICATION.

This in-memory store matches the Agent 2 entity contract so a SQLAlchemy
repository can replace it without changing the gateway.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from models.errors import AppError, ErrorCode
from models.state import utcnow


COUNTABLE_STATUS = "PUBLISHED"
NON_COUNTABLE_STATUSES = frozenset(
    {
        "FAILED",
        "AMBIGUOUS_PUBLICATION",
        "GENERATED",
        "APPROVED",
        "PUBLISHING",
    }
)

BLOCKING_DUPLICATE_STATUSES = frozenset({"PUBLISHED", "PUBLISHING", "AMBIGUOUS_PUBLICATION"})


class AccountStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class PublicationStatus(str, Enum):
    GENERATED = "GENERATED"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    AMBIGUOUS_PUBLICATION = "AMBIGUOUS_PUBLICATION"


class PostType(str, Enum):
    MANUAL = "MANUAL"
    DAILY = "DAILY_RETAIL_POST"
    FESTIVAL = "FESTIVAL"


class PublicationSource(str, Enum):
    UPLOAD = "UPLOAD"
    USER_PROMPT = "USER_PROMPT"
    DAILY_AUTOMATION = "DAILY_AUTOMATION"
    FESTIVAL_AUTOMATION = "FESTIVAL_AUTOMATION"


class InstagramAccountRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    instagram_account_id: str
    access_token_encrypted: str = ""
    token_expires_at: datetime | None = None
    status: str = AccountStatus.CONNECTED.value
    connected_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def public_dict(self) -> dict[str, Any]:
        return {
            "connected": self.status == AccountStatus.CONNECTED.value and bool(self.access_token_encrypted),
            "instagram_account_id": self.instagram_account_id,
            "status": self.status,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "token_expires_at": self.token_expires_at.isoformat() if self.token_expires_at else None,
        }


class InstagramPostRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    instagram_account_id: str
    generated_image_id: str | None = None
    instagram_media_id: str | None = None
    permalink: str | None = None
    status: str = PublicationStatus.PUBLISHING.value
    post_type: str = PostType.MANUAL.value
    source: str = PublicationSource.UPLOAD.value
    published_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    scheduled_date: date | None = None
    festival_campaign_id: str | None = None
    agent_task_id: str | None = None
    verified: bool = False


class AgentTaskRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str | None = None
    task_type: str = "INSTAGRAM_PUBLISH"
    trigger: str = PublicationSource.UPLOAD.value
    status: str = "pending"
    current_step: str | None = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentEventRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    from_state: str | None = None
    to_state: str | None = None
    tool: str | None = None
    result: str | None = None
    observation: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)


class FestivalCampaignRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    festival_name: str
    festival_date: date | None = None
    year: int | None = None
    required_posts: int = 2
    generated_posts: int = 0
    published_posts: int = 0
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def remaining_posts(self) -> int:
        return max(0, self.required_posts - self.published_posts)


class PublicationStats(BaseModel):
    total_posts: int = 0
    daily_posts: int = 0
    festival_published_count: int = 0


class PublicationStore(Protocol):
    async def upsert_account(self, account: InstagramAccountRecord) -> InstagramAccountRecord: ...

    async def get_account(self, user_id: str) -> InstagramAccountRecord | None: ...

    async def disconnect_account(self, user_id: str) -> InstagramAccountRecord | None: ...

    async def create_post(self, post: InstagramPostRecord) -> InstagramPostRecord: ...

    async def get_post(self, post_id: str, *, user_id: str | None = None) -> InstagramPostRecord | None: ...

    async def update_post(self, post: InstagramPostRecord) -> InstagramPostRecord: ...

    async def list_posts(self, user_id: str) -> list[InstagramPostRecord]: ...

    async def find_blocking_daily(
        self, user_id: str, instagram_account_id: str, scheduled_date: date
    ) -> InstagramPostRecord | None: ...

    async def find_by_generated_image(
        self, user_id: str, generated_image_id: str
    ) -> InstagramPostRecord | None: ...

    async def create_task(self, task: AgentTaskRecord) -> AgentTaskRecord: ...

    async def update_task(self, task: AgentTaskRecord) -> AgentTaskRecord: ...

    async def add_event(self, event: AgentEventRecord) -> AgentEventRecord: ...

    async def upsert_campaign(self, campaign: FestivalCampaignRecord) -> FestivalCampaignRecord: ...

    async def get_campaign(self, campaign_id: str, *, user_id: str | None = None) -> FestivalCampaignRecord | None: ...

    async def stats_for(
        self,
        user_id: str,
        *,
        on_date: date | None = None,
        instagram_account_id: str | None = None,
        campaign_id: str | None = None,
    ) -> PublicationStats: ...


def source_to_post_type(source: str | None) -> str:
    value = (source or PublicationSource.UPLOAD.value).upper()
    if value == PublicationSource.DAILY_AUTOMATION.value:
        return PostType.DAILY.value
    if value == PublicationSource.FESTIVAL_AUTOMATION.value:
        return PostType.FESTIVAL.value
    return PostType.MANUAL.value


class InMemoryPublicationStore:
    """Process-local store used until SQLAlchemy repositories are bound."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.accounts: dict[str, InstagramAccountRecord] = {}
        self.posts: dict[str, InstagramPostRecord] = {}
        self.tasks: dict[str, AgentTaskRecord] = {}
        self.events: list[AgentEventRecord] = []
        self.campaigns: dict[str, FestivalCampaignRecord] = {}

    async def upsert_account(self, account: InstagramAccountRecord) -> InstagramAccountRecord:
        async with self._lock:
            account.updated_at = utcnow()
            self.accounts[account.user_id] = account
            return account.model_copy()

    async def get_account(self, user_id: str) -> InstagramAccountRecord | None:
        async with self._lock:
            account = self.accounts.get(user_id)
            return account.model_copy() if account else None

    async def disconnect_account(self, user_id: str) -> InstagramAccountRecord | None:
        async with self._lock:
            account = self.accounts.get(user_id)
            if account is None:
                return None
            account.access_token_encrypted = ""
            account.status = AccountStatus.DISCONNECTED.value
            account.updated_at = utcnow()
            return account.model_copy()

    async def create_post(self, post: InstagramPostRecord) -> InstagramPostRecord:
        async with self._lock:
            self.posts[post.id] = post
            return post.model_copy()

    async def get_post(self, post_id: str, *, user_id: str | None = None) -> InstagramPostRecord | None:
        async with self._lock:
            post = self.posts.get(post_id)
            if post is None:
                return None
            if user_id is not None and post.user_id != user_id:
                return None
            return post.model_copy()

    async def update_post(self, post: InstagramPostRecord) -> InstagramPostRecord:
        async with self._lock:
            existing = self.posts.get(post.id)
            if existing is None:
                raise AppError(ErrorCode.NOT_FOUND, "Publication record was not found.", http_status=404)
            self.posts[post.id] = post
            self._sync_campaign_locked(post.festival_campaign_id)
            return post.model_copy()

    async def list_posts(self, user_id: str) -> list[InstagramPostRecord]:
        async with self._lock:
            return [item.model_copy() for item in self.posts.values() if item.user_id == user_id]

    async def find_blocking_daily(
        self, user_id: str, instagram_account_id: str, scheduled_date: date
    ) -> InstagramPostRecord | None:
        async with self._lock:
            for post in self.posts.values():
                if (
                    post.user_id == user_id
                    and post.instagram_account_id == instagram_account_id
                    and post.post_type == PostType.DAILY.value
                    and post.scheduled_date == scheduled_date
                    and post.status in BLOCKING_DUPLICATE_STATUSES
                ):
                    return post.model_copy()
            return None

    async def find_by_generated_image(
        self, user_id: str, generated_image_id: str
    ) -> InstagramPostRecord | None:
        async with self._lock:
            for post in self.posts.values():
                if (
                    post.user_id == user_id
                    and post.generated_image_id == generated_image_id
                    and post.status in BLOCKING_DUPLICATE_STATUSES
                ):
                    return post.model_copy()
            return None

    async def create_task(self, task: AgentTaskRecord) -> AgentTaskRecord:
        async with self._lock:
            self.tasks[task.id] = task
            return task.model_copy()

    async def update_task(self, task: AgentTaskRecord) -> AgentTaskRecord:
        async with self._lock:
            self.tasks[task.id] = task
            return task.model_copy()

    async def add_event(self, event: AgentEventRecord) -> AgentEventRecord:
        async with self._lock:
            self.events.append(event)
            return event.model_copy()

    async def upsert_campaign(self, campaign: FestivalCampaignRecord) -> FestivalCampaignRecord:
        async with self._lock:
            campaign.updated_at = utcnow()
            self.campaigns[campaign.id] = campaign
            self._sync_campaign_locked(campaign.id)
            return campaign.model_copy()

    async def get_campaign(
        self, campaign_id: str, *, user_id: str | None = None
    ) -> FestivalCampaignRecord | None:
        async with self._lock:
            campaign = self.campaigns.get(campaign_id)
            if campaign is None:
                return None
            if user_id is not None and campaign.user_id != user_id:
                return None
            return campaign.model_copy()

    async def stats_for(
        self,
        user_id: str,
        *,
        on_date: date | None = None,
        instagram_account_id: str | None = None,
        campaign_id: str | None = None,
    ) -> PublicationStats:
        async with self._lock:
            day = on_date or datetime.now(timezone.utc).date()
            total = 0
            daily = 0
            festival = 0
            for post in self.posts.values():
                if post.user_id != user_id:
                    continue
                if post.status != COUNTABLE_STATUS or not post.verified:
                    continue
                if instagram_account_id and post.instagram_account_id != instagram_account_id:
                    continue
                total += 1
                published_day = post.published_at.date() if post.published_at else post.scheduled_date
                if post.post_type == PostType.DAILY.value and published_day == day:
                    daily += 1
                if post.post_type == PostType.FESTIVAL.value:
                    if campaign_id is None or post.festival_campaign_id == campaign_id:
                        festival += 1
            if campaign_id:
                campaign = self.campaigns.get(campaign_id)
                if campaign and campaign.user_id == user_id:
                    festival = campaign.published_posts
            return PublicationStats(
                total_posts=total,
                daily_posts=daily,
                festival_published_count=festival,
            )

    def _sync_campaign_locked(self, campaign_id: str | None) -> None:
        if not campaign_id:
            return
        campaign = self.campaigns.get(campaign_id)
        if campaign is None:
            return
        campaign.published_posts = sum(
            1
            for post in self.posts.values()
            if post.festival_campaign_id == campaign_id
            and post.status == COUNTABLE_STATUS
            and post.verified
        )
        campaign.updated_at = utcnow()
