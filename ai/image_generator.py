"""Image generation provider interface, local storage adapter, and factory.

The image provider generates pixels and stores them. It never publishes to Instagram.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

from config import Settings
from models.errors import AppError, ErrorCode

from ai.schemas import GeneratedImage, ImageGenerationRequest, StoredGeneratedImage, sanitize_user_id

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


class ImageGenerationProvider(ABC):
    @abstractmethod
    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        raise NotImplementedError


class GeneratedImageStore(ABC):
    """Storage contract used by image generation. Implementation may be swapped by media infra."""

    @abstractmethod
    def save(self, *, user_id: str, image_bytes: bytes, mime_type: str) -> StoredGeneratedImage:
        raise NotImplementedError


class LocalGeneratedImageStore(GeneratedImageStore):
    """Writes generated files under storage/generated/{user_id}/img_<uuid>.<ext>."""

    def __init__(self, settings: Settings) -> None:
        from services.media_paths import MediaPaths, generate_internal_filename

        self._paths = MediaPaths(Path(getattr(settings, "media_root", None) or settings.storage_dir))
        self._filename = generate_internal_filename

    def save(self, *, user_id: str, image_bytes: bytes, mime_type: str) -> StoredGeneratedImage:
        if not image_bytes:
            raise AppError(
                ErrorCode.STORAGE_FAILURE,
                "Generated image bytes were empty.",
                http_status=500,
            )
        safe_user = sanitize_user_id(user_id)
        extension = "jpg" if mime_type.lower() in {"image/jpeg", "image/jpg"} else "png"
        filename = self._filename(extension)
        destination = self._paths.image_file("generated", safe_user, filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes(image_bytes)
        except OSError as exc:
            raise AppError(
                ErrorCode.STORAGE_FAILURE,
                "The generated image could not be stored.",
                http_status=500,
            ) from exc
        stored_mime = "image/jpeg" if extension == "jpg" else "image/png"
        return StoredGeneratedImage(
            filename=filename,
            storage_path=str(destination),
            mime_type=stored_mime,
            size_bytes=len(image_bytes),
        )


def safe_filename(name: str) -> str:
    cleaned = _SAFE_FILENAME.sub("", Path(name).name)
    return cleaned or f"img_{uuid4().hex}.png"


def get_image_generation_provider(
    settings: Settings,
    *,
    client: object | None = None,
    storage: GeneratedImageStore | None = None,
) -> ImageGenerationProvider:
    """Return the live image provider named by IMAGE_PROVIDER.

    DeepSeek is not an image provider. A missing OpenAI key is reported when
    generate() runs, not when this factory constructs the provider.
    """
    from ai.llm_client import image_provider_name

    name = image_provider_name(settings)
    if name == "mock":
        from ai.mocks import MockImageGenerationProvider

        return MockImageGenerationProvider(storage)
    if name == "openai":
        from ai.openai_image_generator import OpenAIImageGenerationProvider

        return OpenAIImageGenerationProvider(settings, client=client, storage=storage)
    raise AppError(
        ErrorCode.OPENAI_CONFIGURATION_ERROR,
        "The configured image provider is not supported.",
        http_status=503,
        details={"image_provider": name},
    )


def select_image_provider(
    settings: Settings,
    *,
    client: object | None = None,
    storage: GeneratedImageStore | None = None,
) -> ImageGenerationProvider:
    """Image provider used at startup.

    OpenAI is used when IMAGE_PROVIDER is openai and a key plus image model are
    set. Otherwise the mock is used so startup does not require OPENAI_API_KEY
    and does not ask DeepSeek to generate pixels.
    """
    from ai.llm_client import image_provider_name

    name = image_provider_name(settings)
    configured = settings.openai_configured and bool(settings.image_model.strip() or settings.openai_image_model.strip())
    if name == "openai" and configured:
        return get_image_generation_provider(settings, client=client, storage=storage)
    from ai.mocks import MockImageGenerationProvider

    return MockImageGenerationProvider(storage)
