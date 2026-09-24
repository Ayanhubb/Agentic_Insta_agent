"""Reasoning stays on DeepSeek. Images stay on OpenAI. Default tests never call live APIs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest
from openai import APITimeoutError

from agent.content_agent import ContentAgent
from ai.creative_model import GroundedCreativeModel, get_creative_model
from ai.image_generator import get_image_generation_provider, select_image_provider
from ai.llm.deepseek import DeepSeekCreativeClient, DeepSeekLLMProvider
from ai.llm_client import get_llm_provider, log_provider_selection, select_reasoning_provider
from ai.mocks import MockImageGenerationProvider, MockLLMProvider
from ai.openai_image_generator import OpenAIImageGenerationProvider
from ai.openai_llm import OpenAILLMProvider
from ai.schemas import ContentPlanRequest, ImageGenerationRequest
from api.app import create_app
from models.errors import AppError, ErrorCode
from scheduler.trend_scheduler import TrendIntelligenceScheduler
from tests.helpers import DummyInstagramClient, no_sleep, test_settings

PROMPT = "A boutique window display of silk sarees with warm diya lighting for Instagram."
OPENAI_KEY = "sk-test-not-a-real-openai-key-xxxxx"
DEEPSEEK_KEY = "sk-test-not-a-real-deepseek-key-xxxx"


class _TimeoutTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, payload: dict) -> dict:
        del payload
        self.calls += 1
        raise httpx.TimeoutException("timed out")


class _TimeoutImages:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, **kwargs):
        del kwargs
        self.calls += 1
        raise APITimeoutError(httpx.Request("POST", "https://api.openai.com/v1/images/generations"))


class _TimeoutImageClient:
    def __init__(self) -> None:
        self.images = _TimeoutImages()


def _plan_request() -> ContentPlanRequest:
    return ContentPlanRequest(user_prompt="A festive window display for this shop", reason_hint="user_prompt")


def _image_request() -> ImageGenerationRequest:
    return ImageGenerationRequest(prompt=PROMPT, user_id="user-1", original_prompt="window display")


def test_deepseek_is_selected_for_reasoning_even_when_openai_key_is_set(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
    )
    provider = get_llm_provider(settings)
    selected = select_reasoning_provider(settings)
    assert isinstance(provider, DeepSeekLLMProvider)
    assert isinstance(selected, DeepSeekLLMProvider)
    assert not isinstance(selected, OpenAILLMProvider)
    assert isinstance(get_creative_model(settings), DeepSeekCreativeClient)


def test_openai_is_selected_for_image_generation(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        image_provider="openai",
        openai_api_key=OPENAI_KEY,
        image_model="configured-image-model",
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
    )
    provider = get_image_generation_provider(settings)
    selected = select_image_provider(settings)
    assert isinstance(provider, OpenAIImageGenerationProvider)
    assert isinstance(selected, OpenAIImageGenerationProvider)
    assert not hasattr(DeepSeekLLMProvider, "generate")


def test_missing_deepseek_key_errors_only_on_live_call_and_startup_still_works(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key="",
        deepseek_model="deepseek-flash",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
    )
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    assert isinstance(app.state.llm_provider, MockLLMProvider)
    assert not isinstance(app.state.llm_provider, OpenAILLMProvider)
    provider = get_llm_provider(settings)
    assert isinstance(provider, DeepSeekLLMProvider)
    with pytest.raises(AppError) as exc:
        asyncio.run(provider.generate_content_plan(_plan_request()))
    assert exc.value.code == ErrorCode.DEEPSEEK_CONFIGURATION_ERROR
    assert DEEPSEEK_KEY not in exc.value.message
    assert isinstance(get_creative_model(settings), GroundedCreativeModel)


def test_missing_openai_key_errors_only_when_image_generation_is_requested(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        image_provider="openai",
        openai_api_key="",
        image_model="configured-image-model",
        llm_provider="deepseek",
        deepseek_api_key="",
    )
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    assert isinstance(app.state.image_provider, MockImageGenerationProvider)
    provider = get_image_generation_provider(settings)
    assert isinstance(provider, OpenAIImageGenerationProvider)
    with pytest.raises(AppError) as exc:
        asyncio.run(provider.generate(_image_request()))
    assert exc.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR
    assert OPENAI_KEY not in exc.value.message


def test_deepseek_timeout_is_a_structured_error(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
        llm_max_attempts=1,
    )
    transport = _TimeoutTransport()
    provider = DeepSeekLLMProvider(settings, transport=transport, sleeper=no_sleep)
    with pytest.raises(AppError) as exc:
        asyncio.run(provider.reason("Plan a post for this shop"))
    assert exc.value.code == ErrorCode.DEEPSEEK_TIMEOUT
    assert exc.value.retryable is True
    assert transport.calls == 1
    assert DEEPSEEK_KEY not in exc.value.message


def test_openai_image_timeout_is_a_structured_error(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        image_provider="openai",
        openai_api_key=OPENAI_KEY,
        image_model="configured-image-model",
        image_max_attempts=1,
        openai_retry_delay_seconds=0.0,
    )
    client = _TimeoutImageClient()
    provider = OpenAIImageGenerationProvider(settings, client=client, sleeper=no_sleep)
    with pytest.raises(AppError) as exc:
        asyncio.run(provider.generate(_image_request()))
    assert exc.value.code == ErrorCode.OPENAI_API_ERROR
    assert exc.value.http_status == 504
    assert exc.value.retryable is True
    assert client.images.calls == 1
    assert OPENAI_KEY not in exc.value.message


def test_mock_mode_stays_available_when_live_keys_are_present(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="mock",
        image_provider="mock",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
        image_model="configured-image-model",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
    )
    assert isinstance(get_llm_provider(settings), MockLLMProvider)
    assert isinstance(select_reasoning_provider(settings), MockLLMProvider)
    assert isinstance(get_image_generation_provider(settings), MockImageGenerationProvider)
    assert isinstance(select_image_provider(settings), MockImageGenerationProvider)
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    assert isinstance(app.state.llm_provider, MockLLMProvider)
    assert isinstance(app.state.image_provider, MockImageGenerationProvider)


def test_provider_fallback_does_not_promote_openai_to_reasoning(tmp_path: Path) -> None:
    missing_key = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key="",
        deepseek_model="deepseek-flash",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
        image_provider="deepseek",
        image_model="configured-image-model",
    )
    assert isinstance(select_reasoning_provider(missing_key), MockLLMProvider)
    assert isinstance(select_image_provider(missing_key), MockImageGenerationProvider)
    with pytest.raises(AppError) as image_exc:
        get_image_generation_provider(missing_key)
    assert image_exc.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR

    unknown = test_settings(tmp_path / "unknown", llm_provider="unknown-vendor")
    assert isinstance(select_reasoning_provider(unknown), MockLLMProvider)
    with pytest.raises(AppError) as llm_exc:
        get_llm_provider(unknown)
    assert llm_exc.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR

    explicit = test_settings(
        tmp_path / "openai",
        llm_provider="openai",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
    )
    assert isinstance(select_reasoning_provider(explicit), OpenAILLMProvider)


def test_scheduler_content_agent_and_trend_analyst_use_deepseek(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
        openai_api_key=OPENAI_KEY,
        llm_model="configured-llm-model",
        image_provider="openai",
        image_model="configured-image-model",
    )
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    runner = app.state.automation_runner
    assert isinstance(runner._llm, DeepSeekLLMProvider)
    assert isinstance(app.state.image_provider, OpenAIImageGenerationProvider)
    content = ContentAgent(runner._llm, runner._images, settings=settings)
    assert content._llm is runner._llm
    assert isinstance(get_creative_model(settings), DeepSeekCreativeClient)

    session = app.state.session_factory()
    try:
        trend = TrendIntelligenceScheduler(settings, session, app.state.clock, llm=runner._llm)
        assert trend._analyzer is not None
        assert trend._analyzer._provider is runner._llm
    finally:
        session.close()

    scheduler_source = Path(__file__).resolve().parents[1].joinpath("scheduler", "trend_scheduler.py").read_text(encoding="utf-8")
    assert "api.deepseek.com" not in scheduler_source
    runner_source = Path(__file__).resolve().parents[1].joinpath("scheduler", "scheduler.py").read_text(encoding="utf-8")
    assert "api.deepseek.com" not in runner_source


def test_image_generation_uses_openai_and_instagram_agent_stays_on_meta(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        deepseek_api_key=DEEPSEEK_KEY,
        deepseek_model="deepseek-flash",
        image_provider="openai",
        openai_api_key=OPENAI_KEY,
        image_model="configured-image-model",
    )
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    assert isinstance(app.state.image_provider, OpenAIImageGenerationProvider)
    assert app.state.image_provider is not app.state.llm_provider
    agent_source = Path(__file__).resolve().parents[1].joinpath("agent", "agent.py").read_text(encoding="utf-8")
    assert "get_llm_provider" not in agent_source
    assert "DeepSeek" not in agent_source
    assert "OpenAIImage" not in agent_source


def test_provider_selection_logs_names_and_not_secrets(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="deepseek",
        image_provider="openai",
        deepseek_api_key=DEEPSEEK_KEY,
        openai_api_key=OPENAI_KEY,
    )
    with caplog.at_level(logging.INFO, logger="ai.llm_client"):
        log_provider_selection(settings)
    text = caplog.text
    assert "LLM provider: deepseek" in text
    assert "Image provider: openai" in text
    assert DEEPSEEK_KEY not in text
    assert OPENAI_KEY not in text
    assert "Authorization" not in text
    assert "Bearer" not in text
