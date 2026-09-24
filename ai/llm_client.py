"""LLM provider interface and factory.

The Content Agent depends on this contract, not on a vendor SDK.
The LLM cannot publish to Instagram, run tools, or access secrets.

LLM_PROVIDER=deepseek selects DeepSeek for reasoning. An OpenAI key does not
change that. Construction never requires a key and never calls the network.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from config import Settings
from models.errors import AppError, ErrorCode

from ai.schemas import ContentPlan, ContentPlanRequest

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Intelligence only: turn planning context into a schema-validated ContentPlan."""

    @abstractmethod
    async def generate_content_plan(self, request: ContentPlanRequest) -> ContentPlan:
        raise NotImplementedError


def reasoning_provider_name(settings: Settings) -> str:
    return (settings.llm_provider or "deepseek").strip().lower() or "deepseek"


def image_provider_name(settings: Settings) -> str:
    return (settings.image_provider or "openai").strip().lower() or "openai"


def log_provider_selection(settings: Settings) -> None:
    """Log configured provider names. Never log keys, headers, or tokens."""
    logger.info("LLM provider: %s", reasoning_provider_name(settings))
    logger.info("Image provider: %s", image_provider_name(settings))


def get_llm_provider(settings: Settings, *, client: object | None = None) -> LLMProvider:
    """Return the live provider named by LLM_PROVIDER.

    A missing API key is reported by that provider when a call is made.
    """
    name = reasoning_provider_name(settings)
    if name == "mock":
        from ai.mocks import MockLLMProvider

        return MockLLMProvider()
    if name == "openai":
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


def select_reasoning_provider(settings: Settings) -> LLMProvider:
    """Provider used by the app and scheduler.

    DeepSeek is used when LLM_PROVIDER is deepseek and a key is present.
    A missing key falls back to MockLLMProvider so startup does not call an API
    and does not switch reasoning to OpenAI. Live DeepSeek calls still go
    through get_llm_provider, which raises a configuration error without a key.
    """
    name = reasoning_provider_name(settings)
    if name == "mock":
        from ai.mocks import MockLLMProvider

        return MockLLMProvider()
    if name == "deepseek" and settings.deepseek_configured and settings.deepseek_model.strip():
        return get_llm_provider(settings)
    if name == "openai" and settings.openai_configured and settings.llm_model.strip():
        return get_llm_provider(settings)
    from ai.mocks import MockLLMProvider

    return MockLLMProvider()
