"""OpenAI image generation and editing.

The Content Agent calls this provider. The model cannot call it as a tool.
This module does not publish to Meta, create a media container, or read OAuth tokens.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Any

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

from backend.ai.image.base import (
    DEFAULT_INSTAGRAM_SIZE,
    DEFAULT_OUTPUT_FORMAT,
    EDIT_SIZES,
    GENERATE_SIZES,
    MAX_INPUT_IMAGES,
    SUPPORTED_BACKGROUNDS,
    SUPPORTED_INPUT_MIMES,
    SUPPORTED_OUTPUT_FORMATS,
    SUPPORTED_PIL_FORMATS,
    SUPPORTED_QUALITIES,
    ControlledImageStore,
    ImageArtifactStore,
    ImageInput,
    ImageProvider,
    ImageProviderResult,
    ImageRequest,
    ImageUsage,
    inspect_image,
    output_format_for_mime,
    require_content_agent,
    safe_upload_name,
)

logger = logging.getLogger(__name__)

_ROLE_NOTES = {
    "source": "image to edit",
    "company_logo": "company logo; preserve the mark and do not invent a different wordmark",
    "product": "product photograph; keep the product recognizable",
    "reference": "additional visual reference",
}
_MIME_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}


@dataclass
class _PreparedCall:
    operation: str
    model: str
    prompt: str
    size: str
    quality: str | None
    output_format: str
    background: str | None
    input_fidelity: str | None
    images: list[tuple[str, bytes, str]]


class OpenAIImageProvider(ImageProvider):
    provider_name = "openai"

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        storage: ImageArtifactStore | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._storage = storage or ControlledImageStore(settings)
        self._http = http_client
        self._sleep = sleeper or asyncio.sleep

    async def generate(self, request: ImageRequest, *, caller: str) -> ImageProviderResult:
        require_content_agent(caller)
        operation = "edit" if request.has_input_images else "generate"
        return await self._execute(request, operation=operation)

    async def edit(self, request: ImageRequest, *, caller: str) -> ImageProviderResult:
        require_content_agent(caller)
        if not request.has_input_images:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "Image editing requires a source image, logo, product image, or reference image.",
                http_status=400,
            )
        return await self._execute(request, operation="edit")

    async def _execute(self, request: ImageRequest, *, operation: str) -> ImageProviderResult:
        self._require_configuration()
        prepared = self._prepare(request, operation)
        attempts = max(1, int(self._settings.image_max_attempts))
        last_error: AppError | None = None

        for attempt in range(1, attempts + 1):
            try:
                response, request_id = await self._invoke(prepared)
                image_bytes, revised_prompt = await self._extract_image(response)
                width, height, mime_type = inspect_image(image_bytes)
                stored = self._storage.save(
                    user_id=request.user_id,
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                )
                usage = _usage_from(response)
                result = ImageProviderResult(
                    image_bytes=image_bytes,
                    filename=stored.filename,
                    storage_path=stored.storage_path,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    width=width,
                    height=height,
                    provider=self.provider_name,
                    model=prepared.model,
                    size=_echo(response, "size") or prepared.size,
                    quality=_echo(response, "quality") or prepared.quality,
                    output_format=output_format_for_mime(mime_type),
                    background=_echo(response, "background") or prepared.background,
                    request_id=request_id or _echo(response, "request_id"),
                    usage=usage,
                    metadata=_metadata(response, revised_prompt, prepared.output_format),
                )
                log_step(
                    logger,
                    event="IMAGE_GENERATED",
                    task_id=result.filename,
                    step="generate_image",
                    tool="OpenAIImageProvider",
                    status="success",
                    attempt=attempt,
                    request_id=result.request_id,
                    model=result.model,
                    provider=result.provider,
                )
                return result
            except AppError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
                await self._sleep(self._settings.openai_retry_delay_seconds)

        raise last_error or AppError(
            ErrorCode.OPENAI_API_ERROR,
            "Image generation failed.",
            http_status=502,
        )

    def _require_configuration(self) -> None:
        if not self._api_key():
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "OpenAI is not configured.",
                http_status=503,
            )
        if not self._model():
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "An image model is not configured.",
                http_status=503,
            )

    def _api_key(self) -> str:
        return _first_text(self._settings.openai_api_key, os.getenv("OPENAI_API_KEY", ""))

    def _model(self) -> str:
        return _first_text(
            getattr(self._settings, "openai_image_model", ""),
            os.getenv("OPENAI_IMAGE_MODEL", ""),
            self._settings.image_model,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key(),
                timeout=self._settings.request_timeout_seconds,
            )
        return self._client

    def _prepare(self, request: ImageRequest, operation: str) -> _PreparedCall:
        images = self._validated_images(request)
        if len(images) > MAX_INPUT_IMAGES:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "Too many reference images were provided.",
                http_status=400,
            )
        size = self._resolve_size(request, operation)
        quality = self._resolve_quality(request)
        output_format = self._resolve_output_format(request)
        background = self._resolve_background(request, output_format)
        fidelity = self._resolve_fidelity(request, operation)
        return _PreparedCall(
            operation=operation,
            model=self._model(),
            prompt=_prompt_with_roles(request),
            size=size,
            quality=quality,
            output_format=output_format,
            background=background,
            input_fidelity=fidelity,
            images=images,
        )

    def _validated_images(self, request: ImageRequest) -> list[tuple[str, bytes, str]]:
        prepared: list[tuple[str, bytes, str]] = []
        limit = int(self._settings.max_image_bytes or 0)
        for role, image in request.input_images():
            data, mime, extension = _validate_input_image(image, limit=limit)
            filename = safe_upload_name(role, image.filename, extension)
            prepared.append((filename, data, mime))
        return prepared

    def _resolve_size(self, request: ImageRequest, operation: str) -> str:
        size = _first_text(
            request.size or "",
            getattr(self._settings, "openai_image_size", ""),
            os.getenv("OPENAI_IMAGE_SIZE", ""),
            self._settings.image_size,
            DEFAULT_INSTAGRAM_SIZE,
        ).lower()
        allowed = EDIT_SIZES if operation == "edit" else GENERATE_SIZES
        if size not in allowed:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "The image size is not supported.",
                http_status=400,
            )
        return size

    def _resolve_quality(self, request: ImageRequest) -> str | None:
        quality = _first_text(
            request.quality or "",
            getattr(self._settings, "openai_image_quality", ""),
            os.getenv("OPENAI_IMAGE_QUALITY", ""),
        ).lower()
        if not quality:
            return None
        if quality not in SUPPORTED_QUALITIES:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "The image quality is not supported.",
                http_status=400,
            )
        return quality

    def _resolve_output_format(self, request: ImageRequest) -> str:
        output_format = _first_text(
            request.output_format or "",
            getattr(self._settings, "openai_image_output_format", ""),
            os.getenv("OPENAI_IMAGE_OUTPUT_FORMAT", ""),
            DEFAULT_OUTPUT_FORMAT,
        ).lower()
        if output_format == "jpg":
            output_format = "jpeg"
        if output_format not in SUPPORTED_OUTPUT_FORMATS:
            raise AppError(
                ErrorCode.UNSUPPORTED_FORMAT,
                "The image output format is not supported.",
                http_status=400,
            )
        return output_format

    def _resolve_background(self, request: ImageRequest, output_format: str) -> str | None:
        if request.transparent_background:
            background = "transparent"
        else:
            background = (request.background or "").strip().lower() or None
        if background is None:
            return None
        if background not in SUPPORTED_BACKGROUNDS:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "The image background is not supported.",
                http_status=400,
            )
        if background == "transparent" and output_format == "jpeg":
            raise AppError(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Transparent backgrounds require PNG or WebP output.",
                http_status=400,
            )
        return background

    def _resolve_fidelity(self, request: ImageRequest, operation: str) -> str | None:
        if operation != "edit":
            return None
        fidelity = (request.input_fidelity or "").strip().lower()
        if not fidelity and (request.company_logo is not None or request.product_image is not None):
            fidelity = "high"
        if not fidelity:
            return None
        if fidelity not in {"high", "low"}:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "The image input fidelity is not supported.",
                http_status=400,
            )
        return fidelity

    async def _invoke(self, prepared: _PreparedCall) -> tuple[Any, str | None]:
        client = self._get_client()
        images_api = client.images
        method_name = "edit" if prepared.operation == "edit" else "generate"
        kwargs = _api_kwargs(prepared)
        raw_owner = getattr(images_api, "with_raw_response", None)
        try:
            if raw_owner is not None:
                raw = await getattr(raw_owner, method_name)(**kwargs)
                request_id = _header_request_id(raw)
                parsed = raw.parse()
                if inspect.isawaitable(parsed):
                    parsed = await parsed
                return parsed, request_id
            response = await getattr(images_api, method_name)(**kwargs)
            return response, _echo(response, "request_id")
        except AuthenticationError as exc:
            raise AppError(
                ErrorCode.OPENAI_CONFIGURATION_ERROR,
                "OpenAI rejected the API key.",
                http_status=503,
            ) from exc
        except RateLimitError as exc:
            raise _api_error("The OpenAI image API rate limit was reached.", retryable=True, status=429) from exc
        except APITimeoutError as exc:
            raise AppError(
                ErrorCode.TIMEOUT,
                "The OpenAI image API request timed out.",
                http_status=504,
                retryable=True,
            ) from exc
        except APIConnectionError as exc:
            raise _api_error("The OpenAI image API request failed.", retryable=True) from exc
        except BadRequestError as exc:
            raise _api_error("The OpenAI image API rejected the request.", retryable=False) from exc
        except APIStatusError as exc:
            if exc.status_code in {401, 403}:
                raise AppError(
                    ErrorCode.OPENAI_CONFIGURATION_ERROR,
                    "OpenAI rejected the API key.",
                    http_status=503,
                ) from exc
            retryable = exc.status_code >= 500 or exc.status_code == 429
            raise _api_error(
                "The OpenAI image API request failed.",
                retryable=retryable,
                status=exc.status_code,
            ) from exc
        except AppError:
            raise
        except Exception as exc:
            raise _api_error("The OpenAI image API request failed.", retryable=True) from exc

    async def _extract_image(self, response: Any) -> tuple[bytes, str]:
        data = getattr(response, "data", None) or []
        if not data:
            raise AppError(
                ErrorCode.OPENAI_INVALID_RESPONSE,
                "The OpenAI image response did not include any images.",
                http_status=502,
                retryable=True,
            )
        item = data[0]
        revised = getattr(item, "revised_prompt", None) or ""
        encoded = getattr(item, "b64_json", None)
        if isinstance(encoded, str) and encoded.strip():
            try:
                return base64.b64decode(encoded), revised
            except (ValueError, TypeError) as exc:
                raise AppError(
                    ErrorCode.OPENAI_INVALID_RESPONSE,
                    "The OpenAI image payload could not be decoded.",
                    http_status=502,
                    retryable=True,
                ) from exc
        url = getattr(item, "url", None)
        if isinstance(url, str) and url.strip():
            return await self._download(url), revised
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The OpenAI image response did not include image data.",
            http_status=502,
            retryable=True,
        )

    async def _download(self, url: str) -> bytes:
        if not url.lower().startswith("https://"):
            raise AppError(
                ErrorCode.OPENAI_INVALID_RESPONSE,
                "The generated image URL is not supported.",
                http_status=502,
            )
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


def _validate_input_image(image: ImageInput, *, limit: int) -> tuple[bytes, str, str]:
    mime = (image.mime_type or "").split(";", 1)[0].strip().lower()
    if mime not in SUPPORTED_INPUT_MIMES:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "The reference image format is not supported.",
            http_status=400,
        )
    if not image.data:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "The reference image was empty.",
            http_status=400,
        )
    if limit and len(image.data) > limit:
        raise AppError(
            ErrorCode.FILE_TOO_LARGE,
            "The reference image is too large.",
            http_status=413,
        )
    try:
        with Image.open(BytesIO(image.data)) as opened:
            opened.load()
            image_format = (opened.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "The reference image could not be read.",
            http_status=400,
        ) from exc
    if image_format not in SUPPORTED_PIL_FORMATS:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "The reference image format is not supported.",
            http_status=400,
        )
    extension = _MIME_EXTENSION[mime]
    return image.data, "image/jpeg" if mime == "image/jpg" else mime, extension


def _prompt_with_roles(request: ImageRequest) -> str:
    images = request.input_images()
    if not images:
        return request.prompt
    lines = [request.prompt, "", "Reference images, in order:"]
    for index, (role, _image) in enumerate(images, start=1):
        lines.append(f"{index}. {_ROLE_NOTES[role]}")
    return "\n".join(lines)


def _api_kwargs(prepared: _PreparedCall) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": prepared.model,
        "prompt": prepared.prompt,
        "n": 1,
        "size": prepared.size,
        "output_format": prepared.output_format,
    }
    if prepared.quality:
        kwargs["quality"] = prepared.quality
    if prepared.background:
        kwargs["background"] = prepared.background
    if prepared.operation == "edit":
        kwargs["image"] = list(prepared.images)
        if prepared.input_fidelity:
            kwargs["input_fidelity"] = prepared.input_fidelity
    return kwargs


def _usage_from(response: Any) -> ImageUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    raw = _plain(usage)
    if not isinstance(raw, dict) or not raw:
        return None
    return ImageUsage(
        input_tokens=_optional_int(raw.get("input_tokens")),
        output_tokens=_optional_int(raw.get("output_tokens")),
        total_tokens=_optional_int(raw.get("total_tokens")),
        raw=raw,
    )


def _metadata(response: Any, revised_prompt: str, requested_format: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"requested_output_format": requested_format}
    if isinstance(revised_prompt, str) and revised_prompt.strip():
        metadata["revised_prompt"] = revised_prompt.strip()
    created = getattr(response, "created", None)
    if isinstance(created, int):
        metadata["created"] = created
    echoed_format = _echo(response, "output_format")
    if echoed_format:
        metadata["provider_output_format"] = echoed_format
    return metadata


def _header_request_id(raw: Any) -> str | None:
    request_id = getattr(raw, "request_id", None)
    if isinstance(request_id, str) and request_id.strip():
        return request_id.strip()
    headers = getattr(raw, "headers", None)
    if headers is None:
        return None
    value = headers.get("x-request-id") or headers.get("X-Request-Id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _echo(response: Any, name: str) -> str | None:
    value = getattr(response, name, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return None
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items() if not str(key).startswith("_")}
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if hasattr(value, "__dict__"):
        return {
            key: _plain(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


def _first_text(*values: str | None) -> str:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


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
