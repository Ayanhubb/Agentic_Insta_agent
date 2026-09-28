"""Model capability interfaces.

LLMProvider and ImageGenerationProvider stay the classes in ``ai.llm_client``
and ``ai.image_generator``. This module does not replace them.

VisionProvider matches the DeepSeek vision methods that already exist.
EmbeddingProvider and ModerationProvider are interfaces only. Nothing
implements them, and the registry does not invent a vendor client for them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from ai.image_generator import ImageGenerationProvider
from ai.llm_client import LLMProvider

__all__ = [
    "EmbeddingProvider",
    "ImageGenerationProvider",
    "LLMProvider",
    "ModerationProvider",
    "VisionProvider",
]


@runtime_checkable
class VisionProvider(Protocol):
    """Image understanding and QA. Does not generate pixels or publish."""

    async def understand_image(self, image: Any, *, prompt: str | None = None) -> Any: ...

    async def answer_image_question(self, image: Any, question: str) -> Any: ...

    async def review_image(self, image: Any = None, **kwargs: Any) -> Any: ...


class EmbeddingProvider(ABC):
    """Interface only. No embedding vendor is registered."""

    @abstractmethod
    async def embed(self, texts: list[str], *, user_id: str) -> list[list[float]]:
        raise NotImplementedError


class ModerationProvider(ABC):
    """Interface only. No moderation vendor is registered."""

    @abstractmethod
    async def moderate(self, text: str, *, user_id: str) -> dict[str, Any]:
        raise NotImplementedError
