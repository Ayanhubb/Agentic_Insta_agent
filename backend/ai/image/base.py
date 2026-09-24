"""Image-provider contract.

The Content Agent is the only caller. This package generates or edits pixels
and stores them. It does not publish to Meta and it is not an LLM tool.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai.schemas import sanitize_user_id, validate_image_prompt
from config import Settings
from models.errors import AppError, ErrorCode
from services.media_paths import MediaPaths, atomic_write, is_within

CONTENT_AGENT_CALLER = "content_agent"

DEFAULT_INSTAGRAM_SIZE = "1024x1024"
DEFAULT_OUTPUT_FORMAT = "png"

GENERATE_SIZES = frozenset(
    {
        "auto",
        "256x256",
        "512x512",
        "1024x1024",
        "1536x1024",
        "1024x1536",
        "1792x1024",
        "1024x1792",
    }
)
EDIT_SIZES = frozenset(
    {
        "auto",
        "256x256",
        "512x512",
        "1024x1024",
        "1536x1024",
        "1024x1536",
    }
)
SUPPORTED_QUALITIES = frozenset({"standard", "hd", "low", "medium", "high", "xhigh", "max", "auto"})
SUPPORTED_OUTPUT_FORMATS = frozenset({"png", "jpeg", "webp"})
SUPPORTED_BACKGROUNDS = frozenset({"transparent", "opaque", "auto"})
SUPPORTED_INPUT_MIMES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})
SUPPORTED_PIL_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
MAX_INPUT_IMAGES = 8

_MIME_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}
_PIL_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


class ImageInput(BaseModel):
    """Bytes for a source image, logo, product photo, or other reference."""

    model_config = ConfigDict(extra="ignore")

    data: bytes
    mime_type: str = "image/png"
    filename: str | None = None


class ImageRequest(BaseModel):
    """Provider request. Extra keys, including a forged caller, are ignored."""

    model_config = ConfigDict(extra="ignore")

    prompt: str
    user_id: str
    size: str | None = None
    quality: str | None = None
    output_format: str | None = None
    background: str | None = None
    transparent_background: bool = False
    input_fidelity: str | None = None
    source_image: ImageInput | None = None
    company_logo: ImageInput | None = None
    product_image: ImageInput | None = None
    reference_images: list[ImageInput] = Field(default_factory=list)

    @field_validator("prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        return validate_image_prompt(value)

    @field_validator("user_id")
    @classmethod
    def _user_id(cls, value: str) -> str:
        user_id = value.strip()
        if not user_id:
            raise ValueError("user_id is required")
        return user_id

    def input_images(self) -> list[tuple[str, ImageInput]]:
        images: list[tuple[str, ImageInput]] = []
        if self.source_image is not None:
            images.append(("source", self.source_image))
        if self.company_logo is not None:
            images.append(("company_logo", self.company_logo))
        if self.product_image is not None:
            images.append(("product", self.product_image))
        for reference in self.reference_images:
            images.append(("reference", reference))
        return images

    @property
    def has_input_images(self) -> bool:
        return bool(self.input_images())


class ImageUsage(BaseModel):
    """Token usage copied from the provider response. Monetary amounts are not calculated."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class StoredImageArtifact(BaseModel):
    filename: str
    storage_path: str
    mime_type: str
    size_bytes: int


class ImageProviderResult(BaseModel):
    """Internal result. public_dict() omits bytes, filesystem paths, and secrets."""

    model_config = ConfigDict(extra="ignore")

    image_bytes: bytes
    filename: str
    storage_path: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    provider: str
    model: str
    size: str
    quality: str | None = None
    output_format: str
    background: str | None = None
    request_id: str | None = None
    usage: ImageUsage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"image_bytes", "storage_path"})
        for key in ("openai_api_key", "api_key", "access_token", "authorization"):
            payload.pop(key, None)
        return payload


class ImageArtifactStore(ABC):
    @abstractmethod
    def save(self, *, user_id: str, image_bytes: bytes, mime_type: str) -> StoredImageArtifact:
        raise NotImplementedError


class ControlledImageStore(ImageArtifactStore):
    """Writes owner-scoped files under storage/generated/{user_id}/."""

    def __init__(self, settings: Settings) -> None:
        root = Path(getattr(settings, "media_root", None) or settings.storage_dir)
        self._paths = MediaPaths(root)

    def save(self, *, user_id: str, image_bytes: bytes, mime_type: str) -> StoredImageArtifact:
        if not image_bytes:
            raise AppError(
                ErrorCode.STORAGE_FAILURE,
                "Generated image bytes were empty.",
                http_status=500,
            )
        extension = _MIME_EXTENSION.get(mime_type.lower())
        if extension is None:
            raise AppError(
                ErrorCode.UNSUPPORTED_FORMAT,
                "The generated image format cannot be stored.",
                http_status=400,
            )
        safe_user = sanitize_user_id(user_id)
        filename = f"img_{uuid4().hex}.{extension}"
        directory = self._paths.user_dir("generated", safe_user)
        destination = (directory / filename).resolve()
        if not is_within(destination, self._paths.root):
            raise AppError(ErrorCode.INVALID_REQUEST, "Invalid storage path.", http_status=400)
        atomic_write(destination, image_bytes)
        return StoredImageArtifact(
            filename=filename,
            storage_path=str(destination),
            mime_type="image/jpeg" if extension == "jpg" else mime_type.lower(),
            size_bytes=len(image_bytes),
        )


class ImageProvider(ABC):
    @abstractmethod
    async def generate(self, request: ImageRequest, *, caller: str) -> ImageProviderResult:
        raise NotImplementedError

    @abstractmethod
    async def edit(self, request: ImageRequest, *, caller: str) -> ImageProviderResult:
        raise NotImplementedError


def require_content_agent(caller: str) -> None:
    """Reject every caller except the Content Agent orchestration layer."""

    if caller != CONTENT_AGENT_CALLER:
        raise AppError(
            ErrorCode.TOOL_NOT_ALLOWED,
            "OpenAI image generation is only available through the Content Agent.",
            http_status=403,
        )


def inspect_image(image_bytes: bytes) -> tuple[int, int, str]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            width, height = image.size
            image_format = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AppError(
            ErrorCode.OPENAI_INVALID_RESPONSE,
            "The generated image could not be read.",
            http_status=502,
        ) from exc
    mime = _PIL_MIME.get(image_format)
    if mime is None:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "The generated image format is not supported.",
            http_status=400,
        )
    return width, height, mime


def output_format_for_mime(mime_type: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}[mime_type]


def safe_upload_name(role: str, filename: str | None, extension: str) -> str:
    raw = Path(filename or f"{role}.{extension}").name
    cleaned = _SAFE_FILENAME.sub("", raw).strip(".")
    if not cleaned:
        cleaned = role
    suffix = f".{extension}"
    if not cleaned.lower().endswith(suffix):
        cleaned = f"{cleaned}{suffix}"
    return cleaned[:80]
