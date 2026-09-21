"""OpenAI image generation provider.

Receives a validated prompt, calls the configured OpenAI image model, stores the
bytes through the storage service, and returns an internal GeneratedImage record.

This provider does not publish to Instagram, access OAuth tokens, or execute tools.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Any
from uuid import uuid4

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from PIL import Image, UnidentifiedImageError

from config import Settings
from models.errors import AppError, ErrorCode
from services.logging import log_step, redact_text

from ai.image_generator import GeneratedImageStore, ImageGenerationProvider, LocalGeneratedImageStore
from ai.schemas import ContentSource, GeneratedImage, GenerationStatus, ImageGenerationRequest

logger = logging.getLogger(__name__)


class OpenAIImageGenerationProvider(ImageGenerationProvider):
    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        storage: GeneratedImageStore | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._storage = storage or LocalGeneratedImageStore(settings)
        self._http = http_client
        self._sleep = sleeper or asyncio.sleep

    def _require_configuration(self) -> None:
        if not self._settings.openai_api_key.strip():
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "OpenAI is not configured.",
                http_status=503,
            )
        if not self._settings.image_model.strip():
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "An image model is not configured.",
                http_status=503,
            )

    def _get_client(self) -> Any:
        if self._client is None:
            self._require_configuration()
            self._client = AsyncOpenAI(
                api_key=self._settings.openai_api_key,
                timeout=self._settings.request_timeout_seconds,
            )
        else:
            self._require_configuration()
        return self._client

    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        self._require_configuration()
        last_error: AppError | None = None
        attempts = max(1, int(self._settings.image_max_attempts))

        for attempt in range(1, attempts + 1):
            try:
                image_bytes, mime_type, enhanced_prompt = await self._create_image(request)
                width, height, mime_type = inspect_image(image_bytes, mime_type)
                stored = self._storage.save(
                    user_id=request.user_id,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                )
                record = GeneratedImage(
                    id=str(uuid4()),
                    user_id=request.user_id,
                    original_prompt=request.original_prompt or request.prompt,
                    enhanced_prompt=enhanced_prompt or request.prompt,
                    model=self._settings.image_model,
                    provider=self._settings.image_provider,
                    filename=stored.filename,
                    storage_path=stored.storage_path,
                    mime_type=stored.mime_type,
                    width=width,
                    height=height,
                    generation_status=GenerationStatus.GENERATED,
                    source=request.source,
                    public_url=stored.public_url,
                )
                log_step(
                    logger,
                    event="IMAGE_GENERATED",
                    task_id=record.id,
                    step="generate_image",
                    tool="OpenAIImageGenerationProvider",
                    status="success",
                    attempt=attempt,
                    model=self._settings.image_model,
                    provider=self._settings.image_provider,
                    source=record.source.value,
                )
                return record
            except AppError as exc:
                last_error = exc
                if exc.code in {
                    ErrorCode.OPENAI_CONFIGURATION_ERROR,
                    ErrorCode.STORAGE_FAILURE,
                    ErrorCode.STORAGE_TEMPORARY_FAILURE,
                    ErrorCode.STORAGE_NOT_CONFIGURED,
                }:
                    raise
                if not exc.retryable or attempt >= attempts:
                    raise
                await self._sleep(self._settings.openai_retry_delay_seconds)

        raise last_error or AppError(
            ErrorCode.OPENAI_API_ERROR,
            "Image generation failed.",
            http_status=502,
        )

    async def _create_image(self, request: ImageGenerationRequest) -> tuple[bytes, str, str]:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self._settings.image_model,
            "prompt": request.prompt,
            "n": 1,
        }
        if self._settings.image_size:
            kwargs["size"] = self._settings.image_size
        try:
            response = await client.images.generate(**kwargs)
        except AuthenticationError as exc:
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "OpenAI is not configured.",
                http_status=503,
            ) from exc
        except RateLimitError as exc:
            raise _api_error("The OpenAI image API rate limit was reached.", retryable=True, status=429) from exc
        except (APITimeoutError, APIConnectionError) as exc:
            raise _api_error("The OpenAI image API request timed out.", retryable=True, status=504) from exc
        except BadRequestError as exc:
            raise _api_error("The OpenAI image API rejected the prompt.", retryable=False) from exc
        except APIStatusError as exc:
            retryable = exc.status_code >= 500 or exc.status_code == 429
            raise _api_error(
                "The OpenAI image API request failed.",
                retryable=retryable,
                status=exc.status_code,
            ) from exc
        except Exception as exc:
            raise _api_error("The OpenAI image API request failed.", retryable=True) from exc

        return await self._extract_image(response)

    async def _extract_image(self, response: Any) -> tuple[bytes, str, str]:
        data = getattr(response, "data", None) or []
        if not data:
            raise AppError(
                ErrorCode.OPENAI_INVALID_RESPONSE,
                "The OpenAI image response did not include any images.",
                http_status=502,
                retryable=True,
            )
        item = data[0]
        enhanced = getattr(item, "revised_prompt", None) or ""
        b64 = getattr(item, "b64_json", None)
        if isinstance(b64, str) and b64.strip():
            try:
                image_bytes = base64.b64decode(b64)
            except (ValueError, TypeError) as exc:
                raise AppError(
                    ErrorCode.OPENAI_INVALID_RESPONSE,
                    "The OpenAI image payload could not be decoded.",
                    http_status=502,
                    retryable=True,
                ) from exc
            return image_bytes, "image/png", enhanced

        url = getattr(item, "url", None)
        if isinstance(url, str) and url.strip():
            image_bytes = await self._download(url)
            return image_bytes, "image/png", enhanced

        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The OpenAI image response did not include image data.",
            http_status=502,
            retryable=True,
        )

    async def _download(self, url: str) -> bytes:
        client = self._http or httpx.AsyncClient(timeout=self._settings.request_timeout_seconds)
        owns_client = self._http is None
        try:
            response = await client.get(url)
            response.raise_for_status()
            if not response.content:
                raise AppError(
                    ErrorCode.OPENAI_INVALID_RESPONSE,
                    "The generated image download was empty.",
                    http_status=502,
                    retryable=True,
                )
            return response.content
        except AppError:
            raise
        except Exception as exc:
            raise _api_error("The generated image could not be downloaded.", retryable=True) from exc
        finally:
            if owns_client:
                await client.aclose()


def inspect_image(image_bytes: bytes, fallback_mime: str) -> tuple[int, int, str]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            width, height = image.size
            fmt = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The generated image could not be read.",
            http_status=502,
        ) from exc
    mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(fmt, fallback_mime)
    return width, height, mime


def _api_error(message: str, *, retryable: bool, status: int | None = None) -> AppError:
    details: dict[str, Any] = {}
    if status is not None:
        details["status_code"] = status
    return AppError(
        ErrorCode.OPENAI_API_ERROR,
        redact_text(message),
        http_status=504 if status == 504 else 502,
        retryable=retryable,
        details=details,
    )


def source_from_reason(reason: str) -> ContentSource:
    mapping = {
        "user_prompt": ContentSource.USER_PROMPT,
        "daily_automation": ContentSource.DAILY_AUTOMATION,
        "festival_campaign": ContentSource.FESTIVAL_AUTOMATION,
        "festival_automation": ContentSource.FESTIVAL_AUTOMATION,
    }
    return mapping.get((reason or "").strip().lower(), ContentSource.USER_PROMPT)
