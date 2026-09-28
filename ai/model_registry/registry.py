"""Resolve an agent's model bindings through the existing provider factories.

The registry does not open vendor HTTP clients of its own. It copies the
implementation name onto a private Settings object and calls the factory that
already exists for that role. The copy is not logged, stored, or returned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from ai.creative_model import GroundedCreativeModel, get_creative_model
from ai.image_generator import get_image_generation_provider, select_image_provider
from ai.llm.deepseek import DeepSeekCreativeClient, DeepSeekLLMProvider
from ai.llm_client import get_llm_provider, select_reasoning_provider
from ai.mocks import MockImageGenerationProvider, MockLLMProvider
from ai.model_registry.binding import (
    IMAGE_ROLES,
    INTERFACE_ONLY_ROLES,
    REASONING_ROLES,
    AgentModelConfig,
    ModelBinding,
    ProviderRole,
)
from ai.model_registry.interfaces import VisionProvider
from ai.openai_image_generator import OpenAIImageGenerationProvider
from ai.openai_llm import OpenAILLMProvider
from ai.vision.deepseek import DeepSeekVisionProvider, get_vision_provider
from backend.ai.image.factory import get_openai_image_provider
from backend.ai.image.openai import OpenAIImageProvider
from config import Settings
from models.errors import AppError, ErrorCode
from services.logging import is_secret_key, log_step, redact_text

logger = logging.getLogger(__name__)

_BLOCKED_OPERATIONS = frozenset(
    {
        "publish",
        "publish_instagram_media",
        "create_instagram_media",
        "upload_image",
        "media_publish",
        "post_to_instagram",
        "instagram_publish",
        "publish_media",
        "create_media",
    }
)


class ProviderAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    MOCK = "mock"


class ProviderStatus(BaseModel):
    """Safe provider view. Never includes keys, tokens, or prompts."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    role: str
    requested_implementation: str
    implementation: str
    model: str
    availability: ProviderAvailability
    configured: bool
    timeout_seconds: float
    call_timeout_seconds: float

    def public_dict(self) -> dict[str, str | float | bool]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "requested_implementation": self.requested_implementation,
            "implementation": self.implementation,
            "model": self.model,
            "availability": self.availability.value,
            "configured": self.configured,
            "timeout_seconds": self.timeout_seconds,
            "call_timeout_seconds": self.call_timeout_seconds,
        }


class ResolvedProvider:
    """A factory result plus a secret-free status. The provider object is not serialized."""

    __slots__ = ("provider", "status")

    def __init__(self, provider: Any, status: ProviderStatus) -> None:
        self.provider = provider
        self.status = status


def provider_timeout_seconds(settings: Settings, implementation: str) -> float:
    """Per-request HTTP timeout already used by the vendor clients."""
    if implementation == "deepseek":
        configured = float(settings.deepseek_timeout_seconds or 0)
        if configured > 0:
            return configured
    request = float(settings.request_timeout_seconds or 0)
    if request > 0:
        return request
    agent = float(settings.agent_timeout_seconds or 0)
    return agent if agent > 0 else 30.0


def call_timeout_seconds(settings: Settings, implementation: str, override: float | None = None) -> float:
    """Ceiling around one registry call, including the provider's own retries."""
    if override is not None and override > 0:
        return float(override)
    agent = float(settings.agent_timeout_seconds or 0)
    provider = provider_timeout_seconds(settings, implementation)
    if agent > 0:
        return max(agent, provider)
    return provider


class ProviderRegistry:
    """Agent configuration → existing factory → provider object."""

    def __init__(self, settings: Settings, *, overrides: dict[str, Any] | None = None) -> None:
        self._settings = settings
        self._overrides = dict(overrides or {})
        self._cache: dict[tuple[str, str, str, bool], Any] = {}

    def describe(self, agent: AgentModelConfig, role: ProviderRole | str) -> ProviderStatus:
        binding = self._binding(agent, role)
        if binding.role in INTERFACE_ONLY_ROLES:
            return self._status(
                agent,
                binding,
                implementation=binding.implementation,
                availability=ProviderAvailability.UNAVAILABLE,
                configured=False,
            )
        resolved = self._materialize(agent, binding, live=False)
        return resolved.status

    def describe_all(self, agent: AgentModelConfig) -> list[ProviderStatus]:
        return [self.describe(agent, binding.role) for binding in agent.bindings]

    def resolve(
        self,
        agent: AgentModelConfig,
        role: ProviderRole | str,
        *,
        live: bool = False,
        require_available: bool = False,
    ) -> ResolvedProvider:
        binding = self._binding(agent, role)
        if binding.role in INTERFACE_ONLY_ROLES:
            raise self._interface_only(binding)
        resolved = self._materialize(agent, binding, live=live)
        if require_available and resolved.status.availability is ProviderAvailability.UNAVAILABLE:
            error = self._unavailable(binding)
            self._log(
                "MODEL_PROVIDER_RESOLVED",
                agent_id=agent.agent_id,
                role=resolved.status.role,
                implementation=resolved.status.implementation,
                model=resolved.status.model,
                availability=resolved.status.availability.value,
                status="unavailable",
                error_code=error.code.value,
            )
            raise error
        self._log(
            "MODEL_PROVIDER_RESOLVED",
            agent_id=agent.agent_id,
            role=resolved.status.role,
            implementation=resolved.status.implementation,
            model=resolved.status.model,
            availability=resolved.status.availability.value,
            status="resolved",
        )
        return resolved

    async def call(
        self,
        agent: AgentModelConfig,
        role: ProviderRole | str,
        operation: str,
        /,
        *args: Any,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run one async provider operation. Arguments are not logged."""
        self._reject_operation(operation)
        resolved = self.resolve(agent, role, live=True, require_available=True)
        if resolved.provider is None:
            raise self._unavailable(self._binding(agent, role))
        method = getattr(resolved.provider, operation, None)
        if method is None:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "The model provider does not support this operation.",
                http_status=503,
                details={"role": resolved.status.role, "implementation": resolved.status.implementation},
            )
        timeout = call_timeout_seconds(self._settings, resolved.status.implementation, timeout_seconds)
        started = time.perf_counter()
        try:
            result = method(*args, **kwargs)
            if not asyncio.iscoroutine(result):
                raise AppError(
                    ErrorCode.CONFIGURATION_ERROR,
                    "The model provider operation is not asynchronous.",
                    http_status=503,
                    details={"role": resolved.status.role},
                )
            value = await asyncio.wait_for(result, timeout=timeout)
        except AppError as exc:
            self._log_call(agent, resolved.status, started, status="error", error_code=exc.code.value)
            raise
        except TimeoutError as exc:
            self._log_call(agent, resolved.status, started, status="timeout", error_code=ErrorCode.TIMEOUT.value)
            raise AppError(
                ErrorCode.TIMEOUT,
                "The model provider timed out.",
                http_status=504,
                retryable=True,
                details={
                    "role": resolved.status.role,
                    "implementation": resolved.status.implementation,
                    "model": resolved.status.model,
                    "timeout_seconds": timeout,
                },
            ) from exc
        except Exception as exc:
            self._log_call(agent, resolved.status, started, status="error", error_code=ErrorCode.INTERNAL_ERROR.value)
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "The model provider request failed.",
                http_status=500,
            ) from exc
        self._log_call(agent, resolved.status, started, status="success", error_code="")
        return value

    def _materialize(self, agent: AgentModelConfig, binding: ModelBinding, *, live: bool) -> ResolvedProvider:
        override = self._overrides.get(binding.role.value)
        if override is not None:
            status = self._status(
                agent,
                binding,
                implementation=binding.implementation,
                availability=ProviderAvailability.AVAILABLE,
                configured=True,
            )
            return ResolvedProvider(provider=override, status=status)

        scoped = self._scoped_settings(binding)
        cache_key = self._cache_key(binding, live)
        if cache_key in self._cache:
            provider = self._cache[cache_key]
        else:
            provider = self._construct(binding, scoped, live=live)
            self._cache[cache_key] = provider
        implementation, availability, configured, model = self._observe(binding, scoped, provider)
        return ResolvedProvider(
            provider=provider,
            status=self._status(
                agent,
                binding,
                implementation=implementation,
                availability=availability,
                configured=configured,
                model=model,
            ),
        )

    def _construct(self, binding: ModelBinding, settings: Settings, *, live: bool) -> Any:
        role = binding.role
        if role in {ProviderRole.REASONING_SCHEDULER, ProviderRole.REASONING_TREND}:
            if live:
                return get_llm_provider(settings)
            return select_reasoning_provider(settings)
        if role is ProviderRole.REASONING_STUDIO:
            return get_creative_model(settings)
        if role is ProviderRole.IMAGE_GENERATE:
            if live:
                return get_image_generation_provider(settings)
            return select_image_provider(settings)
        if role is ProviderRole.IMAGE_EDIT:
            return self._construct_image_edit(settings, live=live)
        if role is ProviderRole.VISION_QA:
            try:
                return get_vision_provider(settings)
            except AppError:
                if live:
                    raise
                return None
        raise self._interface_only(binding)

    def _construct_image_edit(self, settings: Settings, *, live: bool) -> Any:
        name = (settings.image_provider or "").strip().lower()
        if name == "mock":
            return None
        if name != "openai":
            if live:
                return get_openai_image_provider(settings)
            return None
        if not live and not _image_configured(settings):
            return None
        return get_openai_image_provider(settings)

    def _observe(
        self,
        binding: ModelBinding,
        settings: Settings,
        provider: Any,
    ) -> tuple[str, ProviderAvailability, bool, str]:
        configured = _configured(binding, settings)
        model = _model_label(binding, settings)
        if provider is None:
            if binding.implementation == "mock":
                return "mock", ProviderAvailability.MOCK, False, model or "mock"
            return binding.implementation, ProviderAvailability.UNAVAILABLE, False, model
        if isinstance(provider, (MockLLMProvider, MockImageGenerationProvider)):
            return "mock", ProviderAvailability.MOCK, False, "mock"
        if isinstance(provider, GroundedCreativeModel):
            return "grounded", ProviderAvailability.MOCK, False, "grounded"
        if isinstance(provider, (DeepSeekLLMProvider, DeepSeekCreativeClient, DeepSeekVisionProvider)):
            state = ProviderAvailability.AVAILABLE if configured else ProviderAvailability.UNAVAILABLE
            return "deepseek", state, configured, model
        if isinstance(provider, (OpenAILLMProvider, OpenAIImageGenerationProvider, OpenAIImageProvider)):
            state = ProviderAvailability.AVAILABLE if configured else ProviderAvailability.UNAVAILABLE
            return "openai", state, configured, model
        if isinstance(provider, VisionProvider):
            state = ProviderAvailability.AVAILABLE if configured else ProviderAvailability.UNAVAILABLE
            return binding.implementation, state, configured, model
        return binding.implementation, ProviderAvailability.UNAVAILABLE, False, model

    def _scoped_settings(self, binding: ModelBinding) -> Settings:
        updates: dict[str, str] = {}
        if binding.role in REASONING_ROLES:
            updates["llm_provider"] = binding.implementation
            if binding.model and binding.implementation == "deepseek":
                updates["deepseek_model"] = binding.model
            elif binding.model and binding.implementation == "openai":
                updates["llm_model"] = binding.model
        elif binding.role in IMAGE_ROLES:
            updates["image_provider"] = binding.implementation
            if binding.model:
                updates["image_model"] = binding.model
        elif binding.role is ProviderRole.VISION_QA:
            updates["vision_provider"] = binding.implementation
            if binding.model and binding.implementation == "deepseek":
                updates["deepseek_model"] = binding.model
        return self._settings.model_copy(update=updates)

    def _cache_key(self, binding: ModelBinding, live: bool) -> tuple[str, str, str, bool]:
        if binding.role in {ProviderRole.REASONING_SCHEDULER, ProviderRole.REASONING_TREND}:
            family = "reasoning.llm"
        else:
            family = binding.role.value
        return (family, binding.implementation, binding.model, live)

    def _binding(self, agent: AgentModelConfig, role: ProviderRole | str) -> ModelBinding:
        try:
            wanted = role if isinstance(role, ProviderRole) else ProviderRole(str(role))
        except ValueError as exc:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "The provider role is not supported.",
                http_status=503,
                details={"role": str(role), "agent_id": agent.agent_id},
            ) from exc
        binding = agent.binding_for(wanted)
        if binding is None:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "The agent is not bound to this provider role.",
                http_status=503,
                details={"role": wanted.value, "agent_id": agent.agent_id},
            )
        return binding

    def _status(
        self,
        agent: AgentModelConfig,
        binding: ModelBinding,
        *,
        implementation: str,
        availability: ProviderAvailability,
        configured: bool,
        model: str | None = None,
    ) -> ProviderStatus:
        shown_model = binding.model if model is None else model
        return ProviderStatus(
            agent_id=agent.agent_id,
            role=binding.role.value,
            requested_implementation=self._redact(binding.implementation),
            implementation=self._redact(implementation),
            model=self._redact(shown_model),
            availability=availability,
            configured=configured,
            timeout_seconds=provider_timeout_seconds(self._settings, implementation),
            call_timeout_seconds=call_timeout_seconds(self._settings, implementation),
        )

    def _log_call(
        self,
        agent: AgentModelConfig,
        provider_status: ProviderStatus,
        started: float,
        *,
        status: str,
        error_code: str,
    ) -> None:
        duration_ms = int((time.perf_counter() - started) * 1000)
        self._log(
            "MODEL_PROVIDER_CALL",
            agent_id=agent.agent_id,
            role=provider_status.role,
            implementation=provider_status.implementation,
            model=provider_status.model,
            availability=provider_status.availability.value,
            status=status,
            duration_ms=duration_ms,
            error_code=error_code,
        )

    def _log(self, event: str, **fields: Any) -> None:
        safe: dict[str, Any] = {}
        for key, value in fields.items():
            if is_secret_key(str(key)):
                continue
            if isinstance(value, str):
                safe[key] = self._redact(value)
            elif isinstance(value, (int, float, bool)) or value is None:
                safe[key] = value
        parts = [event]
        for key in ("agent_id", "role", "implementation", "model", "availability", "status", "error_code"):
            if safe.get(key):
                parts.append(f"{key}={safe[key]}")
        message = self._redact(" ".join(parts))
        log_step(
            logger,
            event=message,
            task_id=str(safe.get("agent_id") or "model-registry"),
            step=str(safe.get("role") or "resolve"),
            tool="ProviderRegistry",
            status=str(safe.get("status") or ""),
            duration_ms=safe.get("duration_ms") if isinstance(safe.get("duration_ms"), int) else None,
            provider=str(safe.get("implementation") or ""),
            model=str(safe.get("model") or ""),
            availability=str(safe.get("availability") or ""),
            error_code=str(safe.get("error_code") or ""),
        )

    def _redact(self, value: str) -> str:
        text = redact_text(value)
        for secret in (
            self._settings.openai_api_key,
            self._settings.deepseek_api_key,
            self._settings.meta_access_token,
            self._settings.jwt_secret,
            self._settings.token_encryption_key,
            self._settings.canva_client_secret,
        ):
            token = (secret or "").strip()
            if token and token in text:
                text = text.replace(token, "[REDACTED]")
        return text

    def _unavailable(self, binding: ModelBinding) -> AppError:
        role = binding.role
        name = binding.implementation
        if role in IMAGE_ROLES:
            code = ErrorCode.OPENAI_CONFIGURATION_ERROR
            message = "The image provider is not available."
        elif role is ProviderRole.VISION_QA:
            code = ErrorCode.DEEPSEEK_CONFIGURATION_ERROR if name == "deepseek" else ErrorCode.VISION_PROVIDER_ERROR
            message = "The vision provider is not available."
        elif name == "deepseek":
            code = ErrorCode.DEEPSEEK_CONFIGURATION_ERROR
            message = "The reasoning provider is not available."
        elif name == "openai":
            code = ErrorCode.OPENAI_CONFIGURATION_ERROR
            message = "The reasoning provider is not available."
        else:
            code = ErrorCode.CONFIGURATION_ERROR
            message = "The model provider is not available."
        return AppError(
            code,
            message,
            http_status=503,
            details={"role": role.value, "implementation": name, "availability": ProviderAvailability.UNAVAILABLE.value},
        )

    def _interface_only(self, binding: ModelBinding) -> AppError:
        return AppError(
            ErrorCode.CONFIGURATION_ERROR,
            "This model capability is not available.",
            http_status=503,
            details={
                "role": binding.role.value,
                "implementation": binding.implementation,
                "availability": ProviderAvailability.UNAVAILABLE.value,
                "reason": "interface_only",
            },
        )

    def _reject_operation(self, operation: str) -> None:
        name = (operation or "").strip()
        if not name or name.lower() in _BLOCKED_OPERATIONS:
            raise AppError(
                ErrorCode.TOOL_NOT_ALLOWED,
                "The model registry cannot publish.",
                http_status=403,
                details={"operation": name or "empty"},
            )


def _image_configured(settings: Settings) -> bool:
    return bool(settings.openai_configured and (settings.image_model.strip() or settings.openai_image_model.strip()))


def _configured(binding: ModelBinding, settings: Settings) -> bool:
    name = binding.implementation
    if binding.role is ProviderRole.REASONING_STUDIO:
        return name == "deepseek" and settings.deepseek_configured
    if binding.role in {ProviderRole.REASONING_SCHEDULER, ProviderRole.REASONING_TREND}:
        if name == "deepseek":
            return bool(settings.deepseek_configured and settings.deepseek_model.strip())
        if name == "openai":
            return bool(settings.openai_configured and settings.llm_model.strip())
        return False
    if binding.role in IMAGE_ROLES:
        return name == "openai" and _image_configured(settings)
    if binding.role is ProviderRole.VISION_QA:
        return name == "deepseek" and settings.deepseek_configured
    return False


def _model_label(binding: ModelBinding, settings: Settings) -> str:
    if binding.model:
        return binding.model
    if binding.implementation == "deepseek":
        return (settings.deepseek_model or "").strip()
    if binding.role in IMAGE_ROLES:
        return (settings.image_model or settings.openai_image_model or "").strip()
    if binding.implementation == "openai":
        return settings.llm_model.strip()
    if binding.implementation == "mock":
        return "mock"
    return ""


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    return ProviderRegistry(settings)
