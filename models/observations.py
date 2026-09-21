"""Structured tool observations consumed by the Agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from models.errors import ErrorBody


class Observation(BaseModel):
    success: bool
    tool: str
    data: dict[str, Any] | None = None
    error: ErrorBody | None = None
    duration_ms: int = 0

    def summary(self) -> dict[str, Any]:
        if not self.success:
            return {"error_code": self.error.code.value if self.error else "UNKNOWN"}
        if not self.data:
            return {}
        keys = (
            "format",
            "width",
            "height",
            "image_url",
            "instagram_container_id",
            "instagram_media_id",
            "verified",
        )
        return {key: self.data[key] for key in keys if key in self.data}
