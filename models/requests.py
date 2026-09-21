"""Request models for the Instagram publish flow."""

from __future__ import annotations

from typing import Literal

from datetime import datetime

from pydantic import BaseModel, Field


class InstagramConnectBody(BaseModel):
    instagram_account_id: str
    access_token: str
    token_expires_at: datetime | None = None


class InstagramPublishTask(BaseModel):
    """Internal task created from a user image upload."""

    task_id: str
    image_path: str
    platform: Literal["instagram"] = "instagram"


class InstagramPublishRequest(BaseModel):
    """Documented API contract. The image itself is sent as multipart `file`."""

    platform: Literal["instagram"] = Field(
        default="instagram",
        description="Target platform. V1 supports instagram only.",
    )
