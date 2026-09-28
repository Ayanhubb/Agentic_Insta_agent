"""Model registry: bindings select factories. Keys stay on Settings."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai.creative_model import GroundedCreativeModel
from ai.llm.deepseek import DeepSeekCreativeClient, DeepSeekLLMProvider
from ai.llm_client import LLMProvider
from ai.mocks import MockImageGenerationProvider, MockLLMProvider
from ai.model_registry import (
    AgentModelConfig,
    EmbeddingProvider,
    ModelBinding,
    ModerationProvider,
    ProviderAvailability,
    ProviderRegistry,
    ProviderRole,
    VisionProvider,
    instagram_marketing_model,
)
from ai.openai_image_generator import OpenAIImageGenerationProvider
from ai.openai_llm import OpenAILLMProvider
from ai.schemas import ContentPlanRequest
from ai.vision.deepseek import DeepSeekVisionProvider
from backend.ai.image.openai import OpenAIImageProvider
from models.errors import AppError, ErrorCode
from tests.helpers import test_settings

OPENAI_KEY = "sk-test-not-a-real-openai-key-xxxxx"
DEEPSEEK_KEY = "sk-test-not-a-real-deepseek-key-xxxx"


def _plan() -> ContentPlanRequest:
    return ContentPlanRequest(user_prompt="A festive window display for this shop", reason_hint="user_prompt")


def _reference(tmp_path: Path, **overrides: object):
    settings = test_settings(tmp_path, **overrides)
    return settings, instagram_marketing_model(settings), ProviderRegistry(settings)


def test_reference_agent_uses_deepseek_for_reasoning_and_openai_for_images(tmp_path: Path) -> None:
    settings, agent, registry = _reference(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
        image_provider="openai",
        image_model="configured-image-model",
        vision_provider="deepseek",
    )
    del settings
    scheduler = registry.resolve(agent, ProviderRole.REASONING_SCHEDULER, live=True)
    trend = registry.resolve(agent, "reasoning.trend", live=True)
    studio = registry.resolve(agent, "reasoning.studio", live=True)
    image = registry.resolve(agent, "image.generate", live=True)
    edit = registry.resolve(agent, "image.edit", live=True)
    vision = registry.resolve(agent, "vision.qa", live=True)

    assert scheduler.provider is trend.provider
    assert isinstance(scheduler.provider, DeepSeekLLMProvider)
    assert not isinstance(scheduler.provider, OpenAILLMProvider)
    assert isinstance(studio.provider, DeepSeekCreativeClient)
    assert isinstance(image.provider, OpenAIImageGenerationProvider)
    assert isinstance(edit.provider, OpenAIImageProvider)
    assert isinstance(vision.provider, DeepSeekVisionProvider)
    assert isinstance(vision.provider, VisionProvider)
    assert scheduler.status.availability is ProviderAvailability.AVAILABLE
    assert image.status.availability is ProviderAvailability.AVAILABLE
    assert image.status.configured is True
    dumped = agent.model_dump_json()
    assert DEEPSEEK_KEY not in dumped
    assert OPENAI_KEY not in dumped


def test_binding_selects_deepseek_even_when_process_llm_provider_is_openai(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="openai",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
    )
    agent = AgentModelConfig(
        agent_id="instagram_marketing",
        bindings=[
            ModelBinding(role="reasoning.scheduler", implementation="deepseek", model="deepseek-flash"),
            ModelBinding(role="reasoning.trend", implementation="deepseek", model="deepseek-flash"),
        ],
    )
    resolved = ProviderRegistry(settings).resolve(agent, "reasoning.scheduler", live=True)
    assert isinstance(resolved.provider, DeepSeekLLMProvider)
    assert resolved.status.requested_implementation == "deepseek"
    assert resolved.status.implementation == "deepseek"


def test_openai_reasoning_is_explicit_and_does_not_require_deepseek(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key="",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
    )
    agent = AgentModelConfig(
        agent_id="planner",
        bindings=[ModelBinding(role="reasoning.scheduler", implementation="openai", model="configured-llm-model")],
    )
    resolved = ProviderRegistry(settings).resolve(agent, "reasoning.scheduler", live=True, require_available=True)
    assert isinstance(resolved.provider, OpenAILLMProvider)
    assert resolved.status.availability is ProviderAvailability.AVAILABLE


def test_image_generation_does_not_require_deepseek(tmp_path: Path) -> None:
    settings, agent, registry = _reference(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key="",
        image_provider="openai",
        openai_api_key=OPENAI_KEY,
        image_model="configured-image-model",
    )
    image = registry.resolve(agent, "image.generate", live=True, require_available=True)
    edit = registry.resolve(agent, "image.edit", live=True, require_available=True)
    assert isinstance(image.provider, OpenAIImageGenerationProvider)
    assert isinstance(edit.provider, OpenAIImageProvider)
    assert settings.deepseek_configured is False


def test_deepseek_is_not_an_image_provider(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        image_provider="openai",
        openai_api_key=OPENAI_KEY,
        image_model="configured-image-model",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
    )
    agent = AgentModelConfig(
        agent_id="instagram_marketing",
        bindings=[ModelBinding(role="image.generate", implementation="deepseek", model="deepseek-flash")],
    )
    registry = ProviderRegistry(settings)
    startup = registry.describe(agent, "image.generate")
    assert isinstance(registry.resolve(agent, "image.generate", live=False).provider, MockImageGenerationProvider)
    assert startup.requested_implementation == "deepseek"
    assert startup.implementation == "mock"
    with pytest.raises(AppError) as exc:
        registry.resolve(agent, "image.generate", live=True)
    assert exc.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR
    assert DEEPSEEK_KEY not in exc.value.message


def test_missing_keys_are_unavailable_and_do_not_switch_vendors(tmp_path: Path) -> None:
    settings, agent, registry = _reference(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key="",
        deepseek_model="deepseek-flash",
        openai_api_key="",
        image_provider="openai",
        image_model="configured-image-model",
        llm_model="configured-llm-model",
    )
    del settings
    reasoning = registry.resolve(agent, "reasoning.scheduler", live=False)
    assert isinstance(reasoning.provider, MockLLMProvider)
    assert reasoning.status.availability is ProviderAvailability.MOCK
    live_reasoning = registry.resolve(agent, "reasoning.scheduler", live=True)
    assert isinstance(live_reasoning.provider, DeepSeekLLMProvider)
    assert live_reasoning.status.availability is ProviderAvailability.UNAVAILABLE
    with pytest.raises(AppError) as reasoning_exc:
        registry.resolve(agent, "reasoning.scheduler", live=True, require_available=True)
    assert reasoning_exc.value.code == ErrorCode.DEEPSEEK_CONFIGURATION_ERROR
    assert OPENAI_KEY not in reasoning_exc.value.message

    image = registry.resolve(agent, "image.generate", live=False)
    assert isinstance(image.provider, MockImageGenerationProvider)
    with pytest.raises(AppError) as image_exc:
        registry.resolve(agent, "image.generate", live=True, require_available=True)
    assert image_exc.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR
    assert DEEPSEEK_KEY not in image_exc.value.message

    studio = registry.resolve(agent, "reasoning.studio")
    assert isinstance(studio.provider, GroundedCreativeModel)
    assert studio.status.availability is ProviderAvailability.MOCK

    vision = registry.describe(agent, "vision.qa")
    assert vision.availability is ProviderAvailability.UNAVAILABLE
    assert vision.configured is False
    with pytest.raises(AppError) as vision_exc:
        registry.resolve(agent, "vision.qa", live=True, require_available=True)
    assert vision_exc.value.code == ErrorCode.DEEPSEEK_CONFIGURATION_ERROR


def test_mock_bindings_win_when_live_keys_are_present(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
        openai_api_key=OPENAI_KEY,
        image_model="configured-image-model",
        image_provider="openai",
    )
    agent = AgentModelConfig(
        agent_id="instagram_marketing",
        bindings=[
            ModelBinding(role="reasoning.scheduler", implementation="mock"),
            ModelBinding(role="image.generate", implementation="mock"),
        ],
    )
    registry = ProviderRegistry(settings)
    assert isinstance(registry.resolve(agent, "reasoning.scheduler", live=True).provider, MockLLMProvider)
    assert isinstance(registry.resolve(agent, "image.generate", live=True).provider, MockImageGenerationProvider)


def test_unbound_role_and_canva_are_rejected(tmp_path: Path) -> None:
    settings, agent, registry = _reference(tmp_path, deepseek_api_key=DEEPSEEK_KEY, deepseek_model="deepseek-flash")
    del settings
    bare = AgentModelConfig(
        agent_id="instagram_marketing",
        bindings=[ModelBinding(role="reasoning.scheduler", implementation="deepseek", model="deepseek-flash")],
    )
    with pytest.raises(AppError) as missing:
        registry.resolve(bare, "image.generate")
    assert missing.value.code == ErrorCode.CONFIGURATION_ERROR
    assert missing.value.details["role"] == "image.generate"
    with pytest.raises(AppError) as canva:
        registry.resolve(agent, "creative.export")
    assert canva.value.code == ErrorCode.CONFIGURATION_ERROR
    assert "creative.export" in str(canva.value.details["role"])


def test_embedding_and_moderation_stay_interfaces(tmp_path: Path) -> None:
    settings = test_settings(tmp_path)
    agent = AgentModelConfig(
        agent_id="future",
        bindings=[
            ModelBinding(role="embedding", implementation="openai"),
            ModelBinding(role="moderation", implementation="openai"),
        ],
    )
    registry = ProviderRegistry(settings)
    for role in ("embedding", "moderation"):
        status = registry.describe(agent, role)
        assert status.availability is ProviderAvailability.UNAVAILABLE
        assert status.configured is False
        with pytest.raises(AppError) as exc:
            registry.resolve(agent, role, live=True)
        assert exc.value.code == ErrorCode.CONFIGURATION_ERROR
        assert exc.value.details["reason"] == "interface_only"
        assert OPENAI_KEY not in exc.value.message
    with pytest.raises(TypeError):
        EmbeddingProvider()
    with pytest.raises(TypeError):
        ModerationProvider()


def test_bindings_reject_secrets_and_status_omits_them(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ModelBinding(role="reasoning.scheduler", implementation="deepseek", api_key=DEEPSEEK_KEY)
    with pytest.raises(ValidationError):
        ModelBinding(role="reasoning.scheduler", implementation="deepseek", model=DEEPSEEK_KEY)
    with pytest.raises(ValidationError):
        AgentModelConfig(
            agent_id="instagram_marketing",
            bindings=[
                ModelBinding(role="reasoning.scheduler", implementation="deepseek"),
                ModelBinding(role="reasoning.scheduler", implementation="openai"),
            ],
        )
    settings, agent, registry = _reference(
        tmp_path,
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
        openai_api_key=OPENAI_KEY,
        image_model="configured-image-model",
    )
    status = registry.describe(agent, "reasoning.scheduler")
    public = status.public_dict()
    assert set(public) == {
        "agent_id",
        "role",
        "requested_implementation",
        "implementation",
        "model",
        "availability",
        "configured",
        "timeout_seconds",
        "call_timeout_seconds",
    }
    blob = str(public)
    assert DEEPSEEK_KEY not in blob
    assert OPENAI_KEY not in blob
    assert settings.jwt_secret not in blob


def test_call_timeout_and_telemetry_do_not_leak_secrets(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    settings = test_settings(
        tmp_path,
        deepseek_api_key=DEEPSEEK_KEY,
        openai_api_key=OPENAI_KEY,
        agent_timeout_seconds=30,
        deepseek_model="deepseek-flash",
    )

    class _Slow(LLMProvider):
        async def generate_content_plan(self, request: ContentPlanRequest):
            del request
            await asyncio.sleep(1)
            raise AssertionError("timeout should win")

    agent = AgentModelConfig(
        agent_id="instagram_marketing",
        bindings=[ModelBinding(role="reasoning.scheduler", implementation="deepseek", model="deepseek-flash")],
    )
    registry = ProviderRegistry(settings, overrides={"reasoning.scheduler": _Slow()})
    caplog.set_level(logging.INFO)
    with pytest.raises(AppError) as exc:
        asyncio.run(
            registry.call(agent, "reasoning.scheduler", "generate_content_plan", _plan(), timeout_seconds=0.05)
        )
    assert exc.value.code == ErrorCode.TIMEOUT
    assert exc.value.retryable is True
    assert exc.value.http_status == 504
    assert DEEPSEEK_KEY not in exc.value.message
    assert OPENAI_KEY not in str(exc.value.details)
    text = caplog.text
    assert "reasoning.scheduler" in text
    assert "deepseek" in text
    assert "MODEL_PROVIDER_CALL" in text
    assert DEEPSEEK_KEY not in text
    assert OPENAI_KEY not in text
    assert settings.jwt_secret not in text


def test_call_uses_a_mock_and_refuses_publish(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    settings = test_settings(tmp_path, deepseek_api_key=DEEPSEEK_KEY, openai_api_key=OPENAI_KEY)
    mock = MockLLMProvider()
    agent = AgentModelConfig(
        agent_id="instagram_marketing",
        bindings=[ModelBinding(role="reasoning.scheduler", implementation="mock")],
    )
    registry = ProviderRegistry(settings, overrides={"reasoning.scheduler": mock})
    caplog.set_level(logging.INFO)
    plan = asyncio.run(registry.call(agent, "reasoning.scheduler", "generate_content_plan", _plan()))
    assert mock.calls == 1
    assert plan.image_prompt
    assert DEEPSEEK_KEY not in caplog.text
    with pytest.raises(AppError) as exc:
        asyncio.run(registry.call(agent, "reasoning.scheduler", "publish_instagram_media"))
    assert exc.value.code == ErrorCode.TOOL_NOT_ALLOWED


def test_configuration_helper_follows_provider_names(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="openai",
        image_provider="mock",
        vision_provider="deepseek",
        llm_model="configured-llm-model",
    )
    agent = instagram_marketing_model(settings)
    by_role = {binding.role: binding.implementation for binding in agent.bindings}
    assert by_role[ProviderRole.REASONING_SCHEDULER] == "openai"
    assert by_role[ProviderRole.REASONING_TREND] == "openai"
    assert by_role[ProviderRole.IMAGE_GENERATE] == "mock"
    assert by_role[ProviderRole.IMAGE_EDIT] == "mock"
    assert by_role[ProviderRole.VISION_QA] == "deepseek"


def test_registry_package_does_not_reference_frontend_secrets_or_meta_publish() -> None:
    root = Path(__file__).resolve().parents[1] / "ai" / "model_registry"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    for token in ("VITE_", "NEXT_PUBLIC_", "instagram_client", "enqueue_instagram_publication", "graph.facebook.com"):
        assert token not in source
