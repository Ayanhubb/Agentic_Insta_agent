"""Load owner-scoped logo and product bytes for the OpenAI image edit path.

Which file to load is decided by ``services.asset_resolution``. This module
reads a file only after that asset id is confirmed for the caller. Missing,
foreign, deleted, and unreadable files return no image. It does not invent a
product photo or a logo.
"""

from __future__ import annotations

import inspect
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

from backend.ai.image.base import CONTENT_AGENT_CALLER, ImageInput, ImageRequest
from models.errors import AppError, ErrorCode

RASTER_MIMES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})
MAX_REFERENCE_IMAGES = 8


def asset_ids_from_mcp(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict) or not payload.get("found"):
        return []
    ids: list[str] = []
    for item in payload.get("assets") or []:
        if isinstance(item, dict) and item.get("id"):
            token = str(item["id"]).strip()
            if token and token not in ids:
                ids.append(token)
    asset = payload.get("asset")
    if isinstance(asset, dict) and asset.get("id"):
        token = str(asset["id"]).strip()
        if token and token not in ids:
            ids.insert(0, token)
    return ids


def load_reference_image(session: Any, settings: Any, user_id: str, asset_id: str) -> ImageInput | None:
    """Return image bytes only when this user owns a readable raster file."""
    token = (asset_id or "").strip()
    if not token or any(char in token for char in "/\\") or ".." in token:
        return None
    if session is None or settings is None:
        return None
    from services.asset_catalog import AssetCatalog

    try:
        path, mime, filename = AssetCatalog(session, settings).open_media(user_id, token)
        data = path.read_bytes()
    except (AppError, OSError):
        return None
    normalized = (mime or "").split(";", 1)[0].strip().lower()
    if normalized not in RASTER_MIMES or not data:
        return None
    limit = int(getattr(settings, "max_image_bytes", 0) or 0)
    if limit and len(data) > limit:
        return None
    if not _readable_raster(data):
        return None
    return ImageInput(data=data, mime_type=normalized, filename=filename)


def accepts_content_agent_caller(generate: Any) -> bool:
    try:
        return "caller" in inspect.signature(generate).parameters
    except (TypeError, ValueError):
        return False


async def submit_creative_image(
    provider: Any,
    request: ImageRequest,
    *,
    settings: Any | None = None,
) -> Any:
    """Send a creative to the OpenAI image provider.

    When reference bytes are present, the existing edit implementation in
    `backend.ai.image` receives the files. A text-only provider is used only
    when this request has no loaded images and already speaks that contract.
    """
    generate = getattr(provider, "generate", None)
    if not callable(generate):
        raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, "Image generation is not configured.")
    if accepts_content_agent_caller(generate):
        return await generate(request, caller=CONTENT_AGENT_CALLER)
    if request.has_input_images:
        edit_settings = settings if settings is not None else getattr(provider, "_settings", None)
        if edit_settings is None:
            raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, "Image generation is not configured.")
        from backend.ai.image.factory import get_openai_image_provider

        editor = get_openai_image_provider(edit_settings, client=getattr(provider, "_client", None))
        return await editor.generate(request, caller=CONTENT_AGENT_CALLER)
    raise AppError(ErrorCode.CONTENT_IMAGE_FAILED, "Image generation is not configured.")


def bound_reference_images(
    *,
    company_logo: ImageInput | None,
    product_image: ImageInput | None,
    extras: list[ImageInput],
) -> list[ImageInput]:
    used = int(company_logo is not None) + int(product_image is not None)
    room = max(0, MAX_REFERENCE_IMAGES - used)
    return extras[:room]


def _readable_raster(data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            return (image.format or "").upper() in {"PNG", "JPEG", "WEBP"}
    except (UnidentifiedImageError, OSError, ValueError):
        return False
