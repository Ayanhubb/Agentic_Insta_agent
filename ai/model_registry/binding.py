"""Agent model bindings.

A binding names a role and an implementation. It does not carry an API key,
a token, or a vendor SDK client. Credentials stay on process Settings, which
are loaded from the environment.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai.llm_client import image_provider_name, reasoning_provider_name
from config import Settings

_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "access_token",
    "secret",
    "password",
    "credential",
    "authorization",
    "sk-",
    "bearer ",
)


class ProviderRole(str, Enum):
    REASONING_STUDIO = "reasoning.studio"
    REASONING_SCHEDULER = "reasoning.scheduler"
    REASONING_TREND = "reasoning.trend"
    IMAGE_GENERATE = "image.generate"
    IMAGE_EDIT = "image.edit"
    VISION_QA = "vision.qa"
    EMBEDDING = "embedding"
    MODERATION = "moderation"


INTERFACE_ONLY_ROLES = frozenset({ProviderRole.EMBEDDING, ProviderRole.MODERATION})

REASONING_ROLES = frozenset(
    {
        ProviderRole.REASONING_STUDIO,
        ProviderRole.REASONING_SCHEDULER,
        ProviderRole.REASONING_TREND,
    }
)

IMAGE_ROLES = frozenset({ProviderRole.IMAGE_GENERATE, ProviderRole.IMAGE_EDIT})


def _reject_secret_text(value: str, *, field_name: str) -> str:
    text = value.strip()
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"{field_name} cannot carry credentials.")
    if any(character in text for character in ("\n", "\r", "\x00")):
        raise ValueError(f"{field_name} cannot carry credentials.")
    return text


class ModelBinding(BaseModel):
    """One role on an agent. ``implementation`` is a provider name, not a secret."""

    model_config = ConfigDict(extra="forbid")

    role: ProviderRole
    implementation: str
    model: str = ""

    @field_validator("implementation")
    @classmethod
    def implementation_name(cls, value: str) -> str:
        text = _reject_secret_text(value, field_name="implementation")
        if not text or len(text) > 64:
            raise ValueError("implementation must be a provider name.")
        normalized = text.lower()
        if any(character.isspace() for character in normalized):
            raise ValueError("implementation must be a provider name.")
        return normalized

    @field_validator("model")
    @classmethod
    def model_name(cls, value: str) -> str:
        if not value:
            return ""
        text = _reject_secret_text(value, field_name="model")
        if len(text) > 128:
            raise ValueError("model name is too long.")
        return text


class AgentModelConfig(BaseModel):
    """Provider roles an agent may ask the registry to resolve."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    bindings: list[ModelBinding] = Field(default_factory=list)

    @field_validator("agent_id")
    @classmethod
    def agent_identifier(cls, value: str) -> str:
        text = _reject_secret_text(value, field_name="agent_id")
        if not text or len(text) > 64:
            raise ValueError("agent_id must be a name.")
        return text

    @model_validator(mode="after")
    def one_binding_per_role(self) -> AgentModelConfig:
        roles = [binding.role for binding in self.bindings]
        if len(roles) != len(set(roles)):
            raise ValueError("An agent binds each provider role once.")
        return self

    def binding_for(self, role: ProviderRole | str) -> ModelBinding | None:
        wanted = role if isinstance(role, ProviderRole) else ProviderRole(role)
        for binding in self.bindings:
            if binding.role is wanted:
                return binding
        return None


def _reasoning_model(settings: Settings, implementation: str) -> str:
    if implementation == "openai":
        return settings.llm_model.strip()
    if implementation == "deepseek":
        return (settings.deepseek_model or "").strip()
    return ""


def _image_model(settings: Settings) -> str:
    return (settings.image_model or settings.openai_image_model or "").strip()


def _vision_name(settings: Settings) -> str:
    return (settings.vision_provider or "deepseek").strip().lower() or "deepseek"


def instagram_marketing_model(settings: Settings) -> AgentModelConfig:
    """Reference agent bindings taken from process configuration.

    The names come from ``LLM_PROVIDER``, ``IMAGE_PROVIDER``, and
    ``VISION_PROVIDER``. Keys are not copied onto the binding.
    """
    reasoning = reasoning_provider_name(settings)
    image = image_provider_name(settings)
    vision = _vision_name(settings)
    reasoning_model = _reasoning_model(settings, reasoning)
    image_model = _image_model(settings)
    vision_model = (settings.deepseek_model or "").strip() if vision == "deepseek" else ""
    return AgentModelConfig(
        agent_id="instagram_marketing",
        bindings=[
            ModelBinding(role=ProviderRole.REASONING_STUDIO, implementation=reasoning, model=reasoning_model),
            ModelBinding(role=ProviderRole.REASONING_SCHEDULER, implementation=reasoning, model=reasoning_model),
            ModelBinding(role=ProviderRole.REASONING_TREND, implementation=reasoning, model=reasoning_model),
            ModelBinding(role=ProviderRole.IMAGE_GENERATE, implementation=image, model=image_model),
            ModelBinding(role=ProviderRole.IMAGE_EDIT, implementation=image, model=image_model),
            ModelBinding(role=ProviderRole.VISION_QA, implementation=vision, model=vision_model),
        ],
    )
