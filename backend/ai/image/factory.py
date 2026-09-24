"""Factory for the OpenAI image provider.

The returned provider is for the Content Agent. It is not registered as an
Instagram tool and it cannot publish to Meta.
"""

from __future__ import annotations

from typing import Any

from config import Settings
from models.errors import AppError, ErrorCode

from backend.ai.image.base import ImageArtifactStore
from backend.ai.image.openai import OpenAIImageProvider


def get_openai_image_provider(
    settings: Settings,
    *,
    client: Any | None = None,
    storage: ImageArtifactStore | None = None,
) -> OpenAIImageProvider:
    name = (settings.image_provider or "").strip().lower()
    if name not in {"", "openai"}:
        raise AppError(
            ErrorCode.OPENAI_CONFIGURATION_ERROR,
            "The configured image provider is not supported.",
            http_status=503,
            details={"image_provider": name},
        )
    return OpenAIImageProvider(settings, client=client, storage=storage)


create_image_provider = get_openai_image_provider
