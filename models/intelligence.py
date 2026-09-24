"""Normalized Instagram account intelligence. These models do not carry access tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MetricReading(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metric: str
    status: Literal["available", "unavailable"]
    value: float | int | str | None = None
    reason: str | None = None
    unit: str | None = None


class InstagramAccountSnapshot(BaseModel):
    user_id: str
    instagram_account_pk: str
    instagram_account_id: str
    username: str | None = None
    name: str | None = None
    captured_at: datetime
    fields: list[MetricReading] = Field(default_factory=list)


class InstagramMediaSnapshot(BaseModel):
    user_id: str
    instagram_media_id: str
    media_type: str | None = None
    media_product_type: str | None = None
    caption: str | None = None
    published_at: datetime | None = None
    permalink: str | None = None
    like_count: int | None = None
    comments_count: int | None = None
    engagement: int | None = None
    publication_status: str
    labels: list[str] = Field(default_factory=list)
    captured_at: datetime
    metrics: list[MetricReading] = Field(default_factory=list)


class InstagramInsightSnapshot(BaseModel):
    user_id: str
    object_id: str
    object_type: Literal["account", "media"]
    metric: str
    period: str
    status: Literal["available", "unavailable"]
    value: float | None = None
    reason: str | None = None
    captured_at: datetime


class ContentPerformanceSnapshot(BaseModel):
    user_id: str
    captured_at: datetime
    sample_size: int
    metrics: list[MetricReading] = Field(default_factory=list)
    content_mix: dict[str, int] = Field(default_factory=dict)
    observations: list[str] = Field(default_factory=list)
    top_content: list[dict[str, Any]] = Field(default_factory=list)
    low_content: list[dict[str, Any]] = Field(default_factory=list)
