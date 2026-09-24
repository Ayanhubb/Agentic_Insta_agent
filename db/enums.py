"""Domain enumerations stored as VARCHAR on SQLite and PostgreSQL (native_enum=False)."""

from __future__ import annotations

from enum import Enum


class AccountStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class ImageSource(str, Enum):
    USER_PROMPT = "USER_PROMPT"
    DAILY_AUTOMATION = "DAILY_AUTOMATION"
    FESTIVAL_AUTOMATION = "FESTIVAL_AUTOMATION"


class GenerationStatus(str, Enum):
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PostStatus(str, Enum):
    GENERATED = "GENERATED"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    FAILED = "FAILED"
    AMBIGUOUS_PUBLICATION = "AMBIGUOUS_PUBLICATION"
    PUBLISHED = "PUBLISHED"


class PostType(str, Enum):
    USER_PROMPT = "USER_PROMPT"
    DAILY_RETAIL_POST = "DAILY_RETAIL_POST"
    FESTIVAL = "FESTIVAL"


class TaskType(str, Enum):
    INSTAGRAM_PUBLISH = "INSTAGRAM_PUBLISH"
    GENERATE_IMAGE = "GENERATE_IMAGE"
    DAILY_RETAIL_POST = "DAILY_RETAIL_POST"
    FESTIVAL_POST = "FESTIVAL_POST"
    CONTENT_JOB = "CONTENT_JOB"


class TaskTrigger(str, Enum):
    USER = "USER"
    SCHEDULER = "SCHEDULER"
    FESTIVAL = "FESTIVAL"
    SYSTEM = "SYSTEM"


class JobType(str, Enum):
    DAILY_RETAIL_POST = "DAILY_RETAIL_POST"
    FESTIVAL_CAMPAIGN = "FESTIVAL_CAMPAIGN"


class JobStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class TrendObservationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class ContentOpportunityStatus(str, Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    USED = "USED"


class FestivalPostStatus(str, Enum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    APPROVED = "APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    AMBIGUOUS_PUBLICATION = "AMBIGUOUS_PUBLICATION"


# Never counted as published. Only VERIFIED PostStatus.PUBLISHED counts.
NON_PUBLISHED_POST_STATUSES: frozenset[PostStatus] = frozenset(
    {
        PostStatus.GENERATED,
        PostStatus.APPROVED,
        PostStatus.PUBLISHING,
        PostStatus.FAILED,
        PostStatus.AMBIGUOUS_PUBLICATION,
    }
)

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_FESTIVAL_REQUIRED_POSTS = 2
DEFAULT_DAILY_POSTS_PER_DAY = 1
DEFAULT_FESTIVAL_POSTS_PER_FESTIVAL = 2
