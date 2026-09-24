"""Shape a Canva design export into the image object QA already reviews.

This module does not open a Canva session, does not hold tokens, and does not publish.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from models.errors import AppError, ErrorCode

UNAVAILABLE_CODES = frozenset(
    {
        ErrorCode.CANVA_NOT_CONNECTED.value,
        ErrorCode.CANVA_AUTHORIZATION_FAILED.value,
        ErrorCode.CANVA_DISABLED.value,
    }
)

CANVA_ACTIONS = frozenset({"apply_template", "use_reference"})

_MESSAGES = {
    ErrorCode.CANVA_NOT_CONNECTED.value: "Canva is not connected for this user.",
    ErrorCode.CANVA_AUTHORIZATION_FAILED.value: "Canva authorization expired.",
    ErrorCode.CANVA_DISABLED.value: "Canva is disabled.",
}


def unavailable_code(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if isinstance(code, str) and code in UNAVAILABLE_CODES:
        return code
    return None


def unavailable_message(code: str) -> str:
    return _MESSAGES.get(code, "Canva is not available for this user.")


def canva_selected(action: Any) -> bool:
    value = getattr(action, "value", action)
    return str(value or "").strip().lower() in CANVA_ACTIONS


def image_from_canva(payload: dict[str, Any], *, user_id: str, prompt: str, settings: Any | None = None) -> Any:
    """Return an image record with bytes for QA. Tokens and export URLs are dropped."""
    raw = payload.get("image_bytes")
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva did not return an image.", http_status=503)
    data = bytes(raw)
    width, height = _dimensions(data)
    filename = None
    storage_path = None
    if settings is not None and getattr(settings, "media_root", None):
        from services.media_storage import MediaStorage

        stored = MediaStorage(settings).save_bytes(user_id, "generated", data, "image/png")
        storage_path = str(stored)
        filename = stored.name
    return SimpleNamespace(
        id=str(uuid4()),
        user_id=user_id,
        original_prompt=prompt,
        enhanced_prompt=prompt,
        model="canva-mcp",
        provider="canva",
        filename=filename,
        storage_path=storage_path,
        mime_type="image/png",
        width=width,
        height=height,
        image_bytes=data,
    )


def _dimensions(data: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            return image.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None, None
