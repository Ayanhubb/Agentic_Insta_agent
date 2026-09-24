"""LLM provider interface and factory.

The Content Agent depends on this contract, not on OpenAI types.
The LLM cannot publish to Instagram, run tools, or access secrets.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from config import Settings
from models.errors import AppError, ErrorCode

from ai.schemas import ContentPlan, ContentPlanRequest


class LLMProvider(ABC):
    """Intelligence only: turn planning context into a schema-validated ContentPlan."""

    @abstractmethod
    async def generate_content_plan(self, request: ContentPlanRequest) -> ContentPlan:
        raise NotImplementedError


def get_llm_provider(settings: Settings, *, client: object | None = None) -> LLMProvider:
    name = (settings.llm_provider or "").strip().lower()
    if name in {"", "openai"}:
        from ai.openai_llm import OpenAILLMProvider

        return OpenAILLMProvider(settings, client=client)
    if name == "deepseek":
        from ai.llm.deepseek import DeepSeekLLMProvider

        return DeepSeekLLMProvider(settings, client=client)
    raise AppError(
        ErrorCode.OPENAI_CONFIGURATION_ERROR,
        "The configured LLM provider is not supported.",
        http_status=503,
        details={"llm_provider": name},
    )
