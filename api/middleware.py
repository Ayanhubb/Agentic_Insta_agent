"""Request-size limits, request IDs, and structured error handling."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import Settings
from models.errors import AppError, ErrorCode, ErrorBody
from services.logging import redact_text

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable]):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                length = 0
            extra = 1024 * 1024
            if request.url.path.endswith("/instagram/publish") and length > self._settings.max_image_bytes + extra:
                return _error_response(
                    request_id,
                    ErrorBody(
                        code=ErrorCode.IMAGE_TOO_LARGE,
                        message=f"Upload exceeds the maximum size of {self._settings.max_image_size_mb} MB.",
                    ),
                    413,
                )

        try:
            response = await call_next(request)
        except AppError as exc:
            return _error_response(request_id, exc.to_body(), exc.http_status)
        except Exception:
            logger.exception("Unhandled request error", extra={"request_id": request_id})
            return _error_response(
                request_id,
                ErrorBody(code=ErrorCode.INTERNAL_ERROR, message="Internal server error."),
                500,
            )
        response.headers["X-Request-ID"] = request_id
        return response


def _error_response(request_id: str, error: ErrorBody, status_code: int) -> JSONResponse:
    payload = {
        "success": False,
        "task_id": None,
        "request_id": request_id,
        "platform": "instagram",
        "status": "failed",
        "error": {
            "code": error.code.value,
            "message": redact_text(error.message),
        },
    }
    response = JSONResponse(payload, status_code=status_code)
    response.headers["X-Request-ID"] = request_id
    return response
