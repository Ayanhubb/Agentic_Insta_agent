"""HTTP routes for the Instagram Image Posting Agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from auth.deps import require_password_ok
from auth.isolation import assert_task_owner
from config import ALLOWED_IMAGE_MIME_TYPES, Settings
from db.models import User
from models.errors import AppError, ErrorCode, http_status_for
from models.responses import PublishResponse, TaskStatusResponse
from models.state import TaskStatus
from tools.image_storage import ImageStorageTool

from api.generation import create_generation_router
from api.task_store import TaskStore
from services.publication import InstagramPublicationRequest

CHUNK_SIZE = 64 * 1024
CAPTION_MAX_LENGTH = 2200


def _normalize_caption(caption: str | None) -> str | None:
    if caption is None:
        return None
    text = caption.strip()
    if not text:
        return None
    if len(text) > CAPTION_MAX_LENGTH:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"Caption must be {CAPTION_MAX_LENGTH} characters or fewer.",
            http_status=400,
        )
    return text


def _safe_suffix(content_type: str | None) -> str:
    if content_type in {"image/png"}:
        return ".png"
    return ".jpg"


async def _save_upload(upload: UploadFile, settings: Settings) -> Path:
    content_type = (upload.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise AppError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "Only JPEG and PNG uploads are accepted.",
            http_status=415,
        )
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.temp_dir / f"{uuid4().hex}{_safe_suffix(content_type)}"
    total = 0
    try:
        with destination.open("wb") as handle:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_image_bytes:
                    raise AppError(
                        ErrorCode.IMAGE_TOO_LARGE,
                        f"Upload exceeds the maximum size of {settings.max_image_size_mb} MB.",
                        http_status=413,
                    )
                handle.write(chunk)
    except AppError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise AppError(ErrorCode.STORAGE_FAILURE, "Could not store the uploaded image.") from exc
    if total == 0:
        destination.unlink(missing_ok=True)
        raise AppError(ErrorCode.INVALID_IMAGE, "Uploaded file is empty.")
    return destination


def create_router(
    settings: Settings,
    store: TaskStore,
    _agent_factory,
    media_service=None,
) -> APIRouter:
    router = APIRouter()
    storage = ImageStorageTool(settings)
    if media_service is None:
        from services.media_storage import MediaStorageService

        media_service = MediaStorageService(settings)
    router.include_router(create_generation_router(media_service))

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/instagram/publish")
    async def publish(
        request: Request,
        image: Annotated[UploadFile | None, File()] = None,
        file: Annotated[UploadFile | None, File()] = None,
        caption: Annotated[str | None, Form()] = None,
        wait: Annotated[bool, Query()] = False,
        user: User = Depends(require_password_ok),
    ) -> JSONResponse:
        upload = image or file
        if upload is None:
            raise AppError(ErrorCode.INVALID_REQUEST, "An image file is required.")
        request_id = getattr(request.state, "request_id", str(uuid4()))
        saved_path = await _save_upload(upload, settings)
        shared_caption = _normalize_caption(caption)
        gateway = getattr(request.app.state, "publication_gateway", None)
        if gateway is None:
            raise AppError(
                ErrorCode.INSTAGRAM_NOT_CONNECTED,
                "Connect an Instagram professional account before publishing.",
                http_status=409,
            )
        state = await gateway.enqueue_publication(
            InstagramPublicationRequest(
                user_id=user.id,
                image_path=str(saved_path),
                source="UPLOAD",
                original_filename=Path(upload.filename or "upload").name,
                request_id=request_id,
                caption=shared_caption,
                wait=wait,
                allow_environment_fallback=settings.legacy_environment_credentials_allowed(),
            )
        )
        if wait:
            response = PublishResponse.from_state(state)
            status_code = 200 if response.success else http_status_for(
                state.error.code if state.error else ErrorCode.INTERNAL_ERROR
            )
            return JSONResponse(response.model_dump(mode="json"), status_code=status_code)
        accepted = PublishResponse.from_state(state, message="Publishing started")
        payload = accepted.model_dump(mode="json")
        payload["message"] = "Publishing started"
        return JSONResponse(payload, status_code=202)

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, user: User = Depends(require_password_ok)) -> TaskStatusResponse:
        state = await store.get(task_id)
        assert_task_owner(state.user_id, user)
        return TaskStatusResponse.from_state(state)

    @router.get("/tasks/{task_id}/events")
    async def task_events(task_id: str, user: User = Depends(require_password_ok)) -> StreamingResponse:
        state = await store.get(task_id)
        assert_task_owner(state.user_id, user)

        async def event_stream():
            async for state in store.subscribe(task_id):
                payload = TaskStatusResponse.from_state(state).model_dump(mode="json")
                yield f"event: status\ndata: {json.dumps(payload, default=str)}\n\n"
                if state.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
                    break

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.api_route("/media/{filename}", methods=["GET", "HEAD"])
    async def public_media(filename: str) -> FileResponse:
        try:
            path = storage.resolve_public_file(filename)
        except AppError as exc:
            raise AppError(ErrorCode.INVALID_REQUEST, "Invalid media path.", http_status=400) from exc
        if not path.is_file():
            raise AppError(ErrorCode.TASK_NOT_FOUND, "Media file was not found.", http_status=404)
        return FileResponse(path, media_type="image/jpeg")

    return router
