"""Verify that published Instagram media exists via the Graph API.

HTTP 200 is not treated as proof that the post exists. The verifier requires
a matching media id and IMAGE media_type from GET /{ig-media-id}.
"""

from __future__ import annotations

import asyncio

from config import Settings
from models.errors import AppError, ErrorCode, OperationCertainty
from models.observations import Observation
from models.state import AgentState
from tools.base import Tool
from tools.instagram_media import InstagramMediaService


class InstagramVerifier(Tool):
    name = "verify_publication"
    purpose = "Verify publication"
    description = "Verify that the published Instagram media id refers to an image."

    def __init__(self, settings: Settings, client: InstagramMediaService) -> None:
        self._settings = settings
        self._client = client

    async def execute(self, state: AgentState) -> Observation:
        last_error: AppError | None = None
        for attempt in range(self._settings.verification_retry_attempts):
            try:
                return await self._verify_once(state)
            except AppError as exc:
                last_error = exc
                if exc.code != ErrorCode.VERIFICATION_DELAY:
                    raise
                if attempt + 1 < self._settings.verification_retry_attempts:
                    await asyncio.sleep(self._settings.verification_retry_delay_seconds)
        if last_error:
            raise last_error
        raise AppError(ErrorCode.VERIFICATION_FAILED, "Publication could not be verified.")

    async def _verify_once(self, state: AgentState) -> Observation:
        if state.instagram_media_id:
            media = await self._client.get_media(state.instagram_media_id)
            media_id = str(media.get("id") or "")
            if media_id != state.instagram_media_id:
                raise AppError(
                    ErrorCode.VERIFICATION_FAILED,
                    "Instagram did not confirm the published media id.",
                    http_status=502,
                )
            media_type = str(media.get("media_type") or "").upper()
            if media_type and media_type != "IMAGE":
                raise AppError(
                    ErrorCode.VERIFICATION_FAILED,
                    "The published Instagram media is not an image.",
                    http_status=502,
                )
            return Observation(
                success=True,
                tool=self.name,
                data={
                    "verified": True,
                    "instagram_media_id": media_id,
                    "permalink": media.get("permalink"),
                    "media_type": media_type or "IMAGE",
                    "api_response": {
                        "id": media_id,
                        "media_type": media.get("media_type"),
                    },
                },
            )

        if state.publish_certainty == OperationCertainty.UNKNOWN and state.instagram_container_id:
            status = await self._client.get_container_status(state.instagram_container_id)
            if status.get("status_code") != "PUBLISHED":
                raise AppError(
                    ErrorCode.VERIFICATION_DELAY,
                    "Publication is not yet visible; retrying verification without publishing again.",
                    retryable=True,
                    certainty=OperationCertainty.UNKNOWN,
                )
            recent = await self._client.list_recent_media(limit=5)
            if recent:
                latest_id = str(recent[0].get("id") or "")
                if latest_id:
                    media = await self._client.get_media(latest_id)
                    if str(media.get("id") or "") != latest_id:
                        raise AppError(
                            ErrorCode.VERIFICATION_FAILED,
                            "Instagram did not confirm the published media id.",
                            http_status=502,
                        )
                    return Observation(
                        success=True,
                        tool=self.name,
                        data={
                            "verified": True,
                            "instagram_media_id": latest_id,
                            "permalink": media.get("permalink"),
                            "ambiguous_resolved": True,
                        },
                    )
            raise AppError(
                ErrorCode.VERIFICATION_DELAY,
                "Publication is not yet visible; retrying verification without publishing again.",
                retryable=True,
                certainty=OperationCertainty.UNKNOWN,
            )

        raise AppError(
            ErrorCode.VERIFICATION_FAILED,
            "No Instagram media ID is available to verify.",
            certainty=OperationCertainty.UNKNOWN
            if state.publish_certainty == OperationCertainty.UNKNOWN
            else OperationCertainty.FAILED,
        )
