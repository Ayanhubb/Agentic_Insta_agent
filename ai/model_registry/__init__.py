"""Platform model registry.

Agent configuration names a role and an implementation. The registry resolves
that pair through the existing provider factories. API keys stay on Settings.
"""

from ai.model_registry.binding import (
    AgentModelConfig,
    ModelBinding,
    ProviderRole,
    instagram_marketing_model,
)
from ai.model_registry.interfaces import (
    EmbeddingProvider,
    ImageGenerationProvider,
    LLMProvider,
    ModerationProvider,
    VisionProvider,
)
from ai.model_registry.registry import (
    ProviderAvailability,
    ProviderRegistry,
    ProviderStatus,
    ResolvedProvider,
    build_provider_registry,
)

__all__ = [
    "AgentModelConfig",
    "EmbeddingProvider",
    "ImageGenerationProvider",
    "LLMProvider",
    "ModelBinding",
    "ModerationProvider",
    "ProviderAvailability",
    "ProviderRegistry",
    "ProviderRole",
    "ProviderStatus",
    "ResolvedProvider",
    "VisionProvider",
    "build_provider_registry",
    "instagram_marketing_model",
]
