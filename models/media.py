"""Generated-image lifecycle models. Internal storage paths are never public."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from models.errors import ErrorCode
from models.state import utcnow


class MediaLifecycleStatus(str, Enum):
    GENERATED = "GENERATED"
    VALIDATED = "VALIDATED"
    PREPARED = "PREPARED"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    GENERATION_FAILED = "GENERATION_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    AMBIGUOUS_PUBLICATION = "AMBIGUOUS_PUBLICATION"


class MediaStage(str, Enum):
    GENERATED = "generated"
    PREPARED = "prepared"
    PUBLISHED = "published"


class ContentSource(str, Enum):
    USER_PROMPT = "USER_PROMPT"
    DAILY_AUTOMATION = "DAILY_AUTOMATION"
    FESTIVAL_AUTOMATION = "FESTIVAL_AUTOMATION"


class MediaTransition(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    source: MediaLifecycleStatus = Field(alias="from")
    target: MediaLifecycleStatus = Field(alias="to")
    model_config = ConfigDict(populate_by_name=True)


FAILURE_STATUSES = frozenset(
    {
        MediaLifecycleStatus.GENERATION_FAILED,
        MediaLifecycleStatus.VALIDATION_FAILED,
        MediaLifecycleStatus.PREPARATION_FAILED,
        MediaLifecycleStatus.PUBLISH_FAILED,
        MediaLifecycleStatus.AMBIGUOUS_PUBLICATION,
    }
)

LEGAL_TRANSITIONS: dict[MediaLifecycleStatus, frozenset[MediaLifecycleStatus]] = {
    MediaLifecycleStatus.GENERATED: frozenset(
        {
            MediaLifecycleStatus.VALIDATED,
            MediaLifecycleStatus.VALIDATION_FAILED,
            MediaLifecycleStatus.GENERATION_FAILED,
        }
    ),
    MediaLifecycleStatus.VALIDATED: frozenset(
        {
            MediaLifecycleStatus.PREPARED,
            MediaLifecycleStatus.PREPARATION_FAILED,
        }
    ),
    MediaLifecycleStatus.PREPARED: frozenset({MediaLifecycleStatus.READY_FOR_APPROVAL}),
    MediaLifecycleStatus.READY_FOR_APPROVAL: frozenset({MediaLifecycleStatus.APPROVED}),
    MediaLifecycleStatus.APPROVED: frozenset({MediaLifecycleStatus.PUBLISHING}),
    MediaLifecycleStatus.PUBLISHING: frozenset(
        {
            MediaLifecycleStatus.PUBLISHED,
            MediaLifecycleStatus.PUBLISH_FAILED,
            MediaLifecycleStatus.AMBIGUOUS_PUBLICATION,
        }
    ),
}


def can_transition(current: MediaLifecycleStatus, target: MediaLifecycleStatus) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, frozenset())


class GeneratedImageRecord(BaseModel):
    """Owner-scoped generated image. Filesystem paths stay internal."""

    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    filename: str | None = None
    prepared_filename: str | None = None
    published_filename: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    status: MediaLifecycleStatus = MediaLifecycleStatus.GENERATED
    source: ContentSource = ContentSource.USER_PROMPT
    original_prompt: str | None = None
    enhanced_prompt: str | None = None
    model: str | None = None
    provider: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    generated_relpath: str | None = None
    prepared_relpath: str | None = None
    published_relpath: str | None = None
    transitions: list[MediaTransition] = Field(default_factory=list)

    def record_transition(self, target: MediaLifecycleStatus) -> MediaTransition:
        from models.errors import AppError

        if not can_transition(self.status, target):
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                f"Cannot transition media from {self.status.value} to {target.value}.",
                http_status=409,
            )
        event = MediaTransition(source=self.status, target=target)
        self.transitions.append(event)
        self.status = target
        if target == MediaLifecycleStatus.APPROVED:
            self.approved_at = utcnow()
        return event

    def public_dict(self) -> dict[str, Any]:
        error = None
        if self.error_code or self.error_message:
            error = {"code": self.error_code, "message": self.error_message}
        return {
            "id": self.id,
            "status": self.status.value,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "source": self.source.value,
            "original_prompt": self.original_prompt,
            "enhanced_prompt": self.enhanced_prompt,
            "model": self.model,
            "provider": self.provider,
            "media_url": f"/api/v1/generation/{self.id}/media",
            "created_at": self.created_at.isoformat(),
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "error": error,
        }
