"""Validate that an uploaded file is a usable still image."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from config import Settings
from models.errors import AppError, ErrorCode
from models.observations import Observation
from models.state import AgentState
from tools.base import Tool

SUPPORTED_FORMATS = frozenset({"JPEG", "PNG"})
FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
}
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/jpeg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/png": "image/png",
    "image/x-png": "image/png",
}


class ImageLimits(BaseModel):
    max_bytes: int = 8 * 1024 * 1024
    min_width: int = 320
    min_height: int = 320
    max_width: int = 8192
    max_height: int = 8192
    allowed_formats: tuple[str, ...] = ("JPEG", "PNG")
    allowed_mime_types: tuple[str, ...] = ("image/jpeg", "image/jpg", "image/png")


class ImageValidationResult(BaseModel):
    valid: bool
    format: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class _ContentHint:
    format: str | None
    mime_type: str | None


class ImageValidator(Tool):
    name = "validate_image"
    purpose = "Validate uploaded image"
    description = (
        "Validate that the source file exists, is a JPEG or PNG still image, "
        "and meets configured size constraints."
    )

    def __init__(
        self,
        settings: Settings | ImageLimits | None = None,
        *,
        limits: ImageLimits | None = None,
        max_image_bytes: int | None = None,
        min_image_side: int | None = None,
        max_image_side: int | None = None,
    ) -> None:
        if isinstance(settings, Settings):
            self._limits = ImageLimits(
                max_bytes=int(settings.max_image_bytes or 8 * 1024 * 1024),
                min_width=settings.min_image_width,
                min_height=settings.min_image_height,
                max_width=settings.max_image_side,
                max_height=settings.max_image_side,
            )
            return
        if isinstance(settings, ImageLimits):
            self._limits = settings
            return
        if limits is not None:
            self._limits = limits
            return
        min_side = min_image_side if min_image_side is not None else 320
        max_side = max_image_side if max_image_side is not None else 8192
        self._limits = ImageLimits(
            max_bytes=max_image_bytes if max_image_bytes is not None else 8 * 1024 * 1024,
            min_width=min_side,
            min_height=min_side,
            max_width=max_side,
            max_height=max_side,
        )

    async def execute(self, state: AgentState) -> Observation:
        if not state.image_path:
            raise AppError(ErrorCode.INVALID_IMAGE, "No image was supplied to the Agent.")
        result = self.validate(Path(state.image_path))
        return Observation(
            success=True,
            tool=self.name,
            data=result.model_dump(),
        )

    def validate(self, image_path: Path, *, claimed_mime: str | None = None) -> ImageValidationResult:
        result = self.inspect(image_path, claimed_mime=claimed_mime)
        if not result.valid:
            raise AppError(
                result.error_code or ErrorCode.INVALID_IMAGE,
                result.error_message or "The provided image is invalid.",
                http_status=400,
            )
        return result

    def validate_bytes(self, data: bytes, *, claimed_mime: str | None = None) -> ImageValidationResult:
        result = self.inspect_bytes(data, claimed_mime=claimed_mime)
        if not result.valid:
            raise AppError(
                result.error_code or ErrorCode.INVALID_IMAGE,
                result.error_message or "The provided image is invalid.",
                http_status=400,
            )
        return result

    def validate_path(self, image_path: str, *, claimed_mime: str | None = None) -> dict[str, object]:
        return self.validate(Path(image_path), claimed_mime=claimed_mime).model_dump()

    def inspect(self, image_path: Path, *, claimed_mime: str | None = None) -> ImageValidationResult:
        path = Path(image_path)
        if not path.exists() or not path.is_file():
            return self._invalid(ErrorCode.INVALID_FILE, "The provided image could not be found.")

        try:
            with path.open("rb") as handle:
                header = handle.read(16)
                if not header:
                    return self._invalid(ErrorCode.INVALID_FILE, "The provided image is empty.")
            size_bytes = path.stat().st_size
        except OSError:
            return self._invalid(ErrorCode.INVALID_FILE, "The provided image could not be read.")

        return self._inspect_content(
            header,
            size_bytes,
            opener_factory=lambda: Image.open(path),
            claimed_mime=claimed_mime,
        )

    def inspect_bytes(self, data: bytes, *, claimed_mime: str | None = None) -> ImageValidationResult:
        if not data:
            return self._invalid(ErrorCode.INVALID_FILE, "The provided image is empty.")
        return self._inspect_content(
            data[:16],
            len(data),
            opener_factory=lambda: Image.open(BytesIO(data)),
            claimed_mime=claimed_mime,
        )

    def _inspect_content(
        self,
        header: bytes,
        size_bytes: int,
        *,
        opener_factory: Callable[[], Image.Image],
        claimed_mime: str | None,
    ) -> ImageValidationResult:
        if size_bytes <= 0:
            return self._invalid(ErrorCode.INVALID_FILE, "The provided image is empty.")
        if size_bytes > self._limits.max_bytes:
            return self._invalid(
                ErrorCode.FILE_TOO_LARGE,
                "The image exceeds the maximum allowed file size.",
            )

        claimed = self._normalize_mime(claimed_mime)
        if claimed_mime and claimed_mime.strip() and claimed is None:
            return self._invalid(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Only JPEG and PNG images are supported.",
            )

        hint = self._detect_from_bytes(header)
        if claimed and hint.mime_type and claimed != hint.mime_type:
            return self._invalid(
                ErrorCode.UNSUPPORTED_FORMAT,
                "The declared MIME type does not match the file contents.",
            )

        try:
            with opener_factory() as image:
                image.verify()
            with opener_factory() as image:
                image.load()
                fmt = (image.format or "").upper()
                if fmt == "JPG":
                    fmt = "JPEG"
                width, height = image.size
                mime_type = FORMAT_TO_MIME.get(fmt) or Image.MIME.get(fmt)
        except UnidentifiedImageError:
            if hint.format and hint.format not in self._limits.allowed_formats:
                return self._invalid(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    "Only JPEG and PNG images are supported.",
                )
            return self._invalid(ErrorCode.CORRUPTED_IMAGE, "The file is not a readable image.")
        except (OSError, ValueError, SyntaxError):
            return self._invalid(
                ErrorCode.CORRUPTED_IMAGE,
                "The image is corrupted or could not be decoded.",
            )

        if fmt not in frozenset(self._limits.allowed_formats):
            return self._invalid(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Only JPEG and PNG images are supported.",
            )

        resolved_mime = mime_type or hint.mime_type or FORMAT_TO_MIME.get(fmt)
        allowed_mimes = {self._normalize_mime(item) for item in self._limits.allowed_mime_types}
        allowed_mimes.discard(None)
        if resolved_mime and self._normalize_mime(resolved_mime) not in allowed_mimes:
            return self._invalid(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Only JPEG and PNG images are supported.",
            )
        if claimed and self._normalize_mime(resolved_mime) != claimed:
            return self._invalid(
                ErrorCode.UNSUPPORTED_FORMAT,
                "The declared MIME type does not match the file contents.",
            )
        if hint.format and hint.format != fmt:
            return self._invalid(
                ErrorCode.CORRUPTED_IMAGE,
                "The image is corrupted or could not be decoded.",
            )

        if width <= 0 or height <= 0:
            return self._invalid(
                ErrorCode.INVALID_DIMENSIONS,
                "Image width and height must be positive.",
            )
        if width < self._limits.min_width or height < self._limits.min_height:
            return self._invalid(
                ErrorCode.IMAGE_TOO_SMALL,
                "The image is smaller than the minimum required dimensions.",
            )
        if width > self._limits.max_width or height > self._limits.max_height:
            return self._invalid(
                ErrorCode.IMAGE_DIMENSIONS_INVALID,
                "The image exceeds the maximum allowed dimensions.",
            )

        return ImageValidationResult(
            valid=True,
            format=fmt,
            mime_type=resolved_mime,
            width=width,
            height=height,
            size_bytes=size_bytes,
        )

    def _normalize_mime(self, mime_type: str | None) -> str | None:
        if not mime_type:
            return None
        value = mime_type.split(";", 1)[0].strip().lower()
        if not value:
            return None
        return _MIME_ALIASES.get(value)

    def _detect_from_bytes(self, header: bytes) -> _ContentHint:
        if header.startswith(_JPEG_MAGIC):
            return _ContentHint(format="JPEG", mime_type="image/jpeg")
        if header.startswith(_PNG_MAGIC):
            return _ContentHint(format="PNG", mime_type="image/png")
        return _ContentHint(format=None, mime_type=None)

    def _invalid(self, code: ErrorCode | str, message: str) -> ImageValidationResult:
        value = code.value if isinstance(code, ErrorCode) else str(code)
        return ImageValidationResult(valid=False, error_code=value, error_message=message)
