"""Content checks for tenant asset uploads.

Type is decided from file bytes. The client filename is never used as a storage path.
"""

from __future__ import annotations

import re

from tools.image_validator import ImageLimits, ImageValidator

from models.errors import AppError, ErrorCode

ROLE_SCOPES = {
    "logo_png": "brand",
    "logo_svg": "brand",
    "guideline": "brand",
    "product_image": "product",
    "campaign": "campaign",
    "other": None,
}
ROLE_MIME_TYPES = {
    "logo_png": frozenset({"image/png"}),
    "logo_svg": frozenset({"image/svg+xml"}),
    "product_image": frozenset({"image/png", "image/jpeg"}),
    "guideline": frozenset({"text/plain", "text/markdown", "application/pdf"}),
    "campaign": frozenset({"image/png", "image/jpeg"}),
    "other": frozenset({"image/png", "image/jpeg", "image/svg+xml", "application/pdf", "text/plain", "text/markdown"}),
}
RASTER_MIME_TYPES = frozenset({"image/png", "image/jpeg"})
_SVG_FORBIDDEN = re.compile(
    r"(<\s*script\b|javascript\s*:|<\s*foreignObject\b|<\s*iframe\b|<\s*embed\b|<\s*object\b|"
    r"<!DOCTYPE|<!ENTITY|on[a-z]+\s*=|"
    r"""(?:\bhref|\bxlink:href)\s*=\s*["']\s*(?:https?:|//))""",
    re.IGNORECASE,
)
_PDF_ACTIVE = re.compile(br"/JavaScript|/JS\b|/Launch|/OpenAction|/AA\b")
_SVG_WIDTH = re.compile(r"""\bwidth\s*=\s*["'](\d+(?:\.\d+)?)""", re.IGNORECASE)
_SVG_HEIGHT = re.compile(r"""\bheight\s*=\s*["'](\d+(?:\.\d+)?)""", re.IGNORECASE)
_SVG_VIEWBOX = re.compile(
    r"""\bviewBox\s*=\s*["']\s*[-\d.]+\s+[-\d.]+\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)""",
    re.IGNORECASE,
)


def normalize_mime(value: str | None) -> str | None:
    if not value:
        return None
    base = value.split(";", 1)[0].strip().lower()
    if base in {"image/jpg", "image/pjpeg"}:
        return "image/jpeg"
    if base == "image/x-png":
        return "image/png"
    return base or None


def expected_scope(role: str, scope: str | None) -> str:
    if role not in ROLE_SCOPES:
        raise AppError(ErrorCode.INVALID_REQUEST, "Invalid asset role.", http_status=400)
    required = ROLE_SCOPES[role]
    chosen = (scope or required or "").strip().lower()
    if chosen not in {"brand", "product", "campaign"}:
        raise AppError(ErrorCode.INVALID_REQUEST, "Invalid asset scope.", http_status=400)
    if required is not None and chosen != required:
        raise AppError(ErrorCode.INVALID_REQUEST, "This asset role does not match the selected scope.", http_status=400)
    return chosen


def validate_asset_bytes(
    data: bytes,
    *,
    role: str,
    claimed_mime: str | None,
    limits: ImageLimits,
) -> tuple[str, int | None, int | None]:
    if role not in ROLE_MIME_TYPES:
        raise AppError(ErrorCode.INVALID_REQUEST, "Invalid asset role.", http_status=400)
    if not data:
        raise AppError(ErrorCode.INVALID_FILE, "The provided file is empty.", http_status=400)
    if len(data) > limits.max_bytes:
        raise AppError(ErrorCode.FILE_TOO_LARGE, "The file exceeds the maximum allowed size.", http_status=413)

    detected = _detect_mime(data)
    claimed = normalize_mime(claimed_mime)
    if claimed and claimed not in ROLE_MIME_TYPES[role] and claimed != detected:
        raise AppError(ErrorCode.UNSUPPORTED_FORMAT, "This file type is not supported.", http_status=400)
    mime = detected or claimed
    if mime not in ROLE_MIME_TYPES[role]:
        raise AppError(ErrorCode.UNSUPPORTED_FORMAT, "This file type is not supported.", http_status=400)
    if claimed and detected and claimed != detected:
        raise AppError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "The declared file type does not match the file contents.",
            http_status=400,
        )

    if mime in RASTER_MIME_TYPES:
        result = ImageValidator(limits=limits).inspect_bytes(data, claimed_mime=mime)
        if not result.valid:
            code = ErrorCode(result.error_code) if result.error_code else ErrorCode.INVALID_FILE
            raise AppError(code, result.error_message or "The provided image is invalid.")
        return mime, result.width, result.height
    if mime == "image/svg+xml":
        return mime, *_svg_dimensions(data, limits)
    if mime == "application/pdf":
        _validate_pdf(data)
        return mime, None, None
    _validate_text(data)
    return mime, None, None


def _detect_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    sample = data[:512].lstrip()
    lowered = sample.lower()
    if lowered.startswith(b"<svg") or (lowered.startswith(b"<?xml") and b"<svg" in lowered):
        return "image/svg+xml"
    if b"\x00" not in data[:1024]:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "text/plain"
    return None


def _svg_dimensions(data: bytes, limits: ImageLimits) -> tuple[int | None, int | None]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError(ErrorCode.INVALID_FILE, "The SVG file is not valid text.", http_status=400) from exc
    if "\x00" in text or "<svg" not in text.lower():
        raise AppError(ErrorCode.INVALID_FILE, "The file is not an SVG image.", http_status=400)
    if _SVG_FORBIDDEN.search(text):
        raise AppError(ErrorCode.INVALID_FILE, "The file content is not allowed.", http_status=400)
    width = _optional_px(_SVG_WIDTH.search(text))
    height = _optional_px(_SVG_HEIGHT.search(text))
    if width is None or height is None:
        viewbox = _SVG_VIEWBOX.search(text)
        if viewbox:
            width = width if width is not None else int(float(viewbox.group(1)))
            height = height if height is not None else int(float(viewbox.group(2)))
    for side in (width, height):
        if side is None:
            continue
        if side < 1 or side > limits.max_width:
            raise AppError(
                ErrorCode.INVALID_DIMENSIONS,
                "The image exceeds the maximum allowed dimensions.",
                http_status=400,
            )
    return width, height


def _optional_px(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    return int(float(match.group(1)))


def _validate_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF-"):
        raise AppError(ErrorCode.UNSUPPORTED_FORMAT, "This file type is not supported.", http_status=400)
    if _PDF_ACTIVE.search(data):
        raise AppError(ErrorCode.INVALID_FILE, "The file content is not allowed.", http_status=400)


def _validate_text(data: bytes) -> None:
    if b"\x00" in data:
        raise AppError(ErrorCode.INVALID_FILE, "The file content is not allowed.", http_status=400)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError(ErrorCode.INVALID_FILE, "The text file must be UTF-8.", http_status=400) from exc
