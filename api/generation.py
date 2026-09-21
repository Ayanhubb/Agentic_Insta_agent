"""Owner-scoped generated-image APIs. Filesystem paths are never returned."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from api.security import CurrentUser, get_current_user
from models.errors import AppError, ErrorCode
from services.media_storage import MediaStorageService


def create_generation_router(media_service: MediaStorageService) -> APIRouter:
    router = APIRouter()

    @router.api_route("/generation/{image_id}/media", methods=["GET", "HEAD"])
    async def get_generation_media(
        image_id: str,
        user: Annotated[CurrentUser, Depends(get_current_user)],
        stage: Annotated[str | None, Query()] = None,
    ) -> FileResponse:
        if stage is not None and ("/" in stage or "\\" in stage or ".." in stage):
            raise AppError(ErrorCode.INVALID_REQUEST, "Invalid media stage.", http_status=400)
        path, mime_type = media_service.resolve_owned_media(user.id, image_id, stage)
        return FileResponse(path, media_type=mime_type)

    return router
