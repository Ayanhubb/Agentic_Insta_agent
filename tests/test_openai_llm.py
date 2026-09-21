"""Mocked OpenAI LLM provider tests. Default pytest never calls the real API."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.llm_client import LLMProvider, get_llm_provider
from ai.openai_llm import OpenAILLMProvider, parse_content_plan
from ai.schemas import (
    BusinessProfileContext,
    ContentHistoryItem,
    ContentPlan,
    ContentPlanRequest,
    FestivalContext,
)
from models.errors import AppError, ErrorCode
from tests.helpers import no_sleep, test_settings

VALID_PLAN = {
    "content_type": "product_promotion",
    "theme": "festive_collection",
    "image_prompt": (
        "A boutique window display of silk sarees with warm diya lighting, "
        "shot for Instagram in a Kolkata storefront."
    ),
    "business_context": "A Kolkata boutique promoting its festive silk collection to local shoppers.",
    "reason": "festival_campaign",
}


def _ai_settings(tmp_path: Path, **overrides):
    values = dict(
        openai_api_key="sk-test-not-a-real-openai-key-xxxxx",
        llm_provider="openai",
        llm_model="configured-llm-model",
        image_provider="openai",
        image_model="configured-image-model",
        llm_max_attempts=2,
        openai_retry_delay_seconds=0.0,
    )
    values.update(overrides)
    return test_settings(tmp_path, **values)


def _plan_request() -> ContentPlanRequest:
    return ContentPlanRequest(
        user_prompt="Create a Diwali post for our new silk collection",
        current_date="2026-09-21",
        reason_hint="festival_campaign",
        business_profile=BusinessProfileContext(
            business_name="Silk House",
            business_type="retail",
            business_category="apparel",
            description="Handloom silk boutique",
            target_audience="festive shoppers in Kolkata",
            location="Kolkata",
            brand_style="warm, elegant, traditional",
            preferred_language="English",
            products=["silk sarees", "festive stoles"],
            services=["custom draping"],
        ),
        festival=FestivalContext(festival_name="Diwali", festival_date="2026-11-08", year=2026),
        recent_content=[
            ContentHistoryItem(theme="monsoon_sale", content_type="promotion"),
        ],
    )


class FakeCompletions:
    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls = 0
        self.kwargs: list[dict] = []

    async def create(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item, tool_calls=None))]
        )


class FakeLLMClient:
    def __init__(self, script: list[object]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(script))


def _provider(tmp_path: Path, script: list[object], **overrides) -> OpenAILLMProvider:
    settings = _ai_settings(tmp_path, **overrides)
    return OpenAILLMProvider(settings, client=FakeLLMClient(script), sleeper=no_sleep)


@pytest.mark.asyncio
async def test_successful_llm_generation(tmp_path: Path) -> None:
    provider = _provider(tmp_path, [json.dumps(VALID_PLAN)])
    plan = await provider.generate_content_plan(_plan_request())
    assert isinstance(plan, ContentPlan)
    assert plan.content_type == "product_promotion"
    assert plan.theme == "festive_collection"
    assert "silk sarees" in plan.image_prompt
    assert plan.reason == "festival_campaign"
    completions = provider._client.chat.completions  # type: ignore[union-attr]
    assert completions.calls == 1
    assert completions.kwargs[0]["model"] == "configured-llm-model"
    assert "sk-test" not in json.dumps(completions.kwargs[0])


@pytest.mark.asyncio
async def test_malformed_llm_output(tmp_path: Path) -> None:
    provider = _provider(tmp_path, ["this is not json"], llm_max_attempts=2)
    with pytest.raises(AppError) as exc_info:
        await provider.generate_content_plan(_plan_request())
    assert exc_info.value.code == ErrorCode.OPENAI_INVALID_RESPONSE
    assert provider._client.chat.completions.calls == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_openai_failure(tmp_path: Path) -> None:
    provider = _provider(tmp_path, [RuntimeError("upstream failed")], llm_max_attempts=2)
    with pytest.raises(AppError) as exc_info:
        await provider.generate_content_plan(_plan_request())
    assert exc_info.value.code == ErrorCode.OPENAI_API_ERROR
    assert "sk-test" not in exc_info.value.message
    assert provider._client.chat.completions.calls == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_missing_api_key(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path, openai_api_key="")
    provider = OpenAILLMProvider(settings, sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.generate_content_plan(_plan_request())
    assert exc_info.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR


@pytest.mark.asyncio
async def test_missing_model_is_configuration_error(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path, llm_model="")
    provider = OpenAILLMProvider(settings, client=FakeLLMClient([json.dumps(VALID_PLAN)]), sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.generate_content_plan(_plan_request())
    assert exc_info.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR


@pytest.mark.asyncio
async def test_retries_then_succeeds(tmp_path: Path) -> None:
    provider = _provider(tmp_path, ["not-json", json.dumps(VALID_PLAN)], llm_max_attempts=3)
    plan = await provider.generate_content_plan(_plan_request())
    assert plan.theme == "festive_collection"
    assert provider._client.chat.completions.calls == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_repeated_theme_is_retried(tmp_path: Path) -> None:
    repeat = dict(VALID_PLAN)
    repeat["theme"] = "monsoon_sale"
    unique = dict(VALID_PLAN)
    unique["theme"] = "diwali_silk_window"
    unique["image_prompt"] = (
        "Close-up of a silk saree pallu with gold zari and a single diya, elegant boutique lighting."
    )
    provider = _provider(tmp_path, [json.dumps(repeat), json.dumps(unique)], llm_max_attempts=3)
    plan = await provider.generate_content_plan(_plan_request())
    assert plan.theme == "diwali_silk_window"


def test_parse_content_plan_accepts_fenced_json() -> None:
    raw = "```json\n" + json.dumps(VALID_PLAN) + "\n```"
    plan = parse_content_plan(raw)
    assert plan.content_type == "product_promotion"


def test_parse_content_plan_rejects_unknown_type() -> None:
    payload = dict(VALID_PLAN)
    payload["content_type"] = "shell_command"
    with pytest.raises(AppError) as exc_info:
        parse_content_plan(json.dumps(payload))
    assert exc_info.value.code == ErrorCode.OPENAI_INVALID_RESPONSE


def test_factory_returns_openai_provider(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path)
    provider = get_llm_provider(settings)
    assert isinstance(provider, OpenAILLMProvider)
    assert isinstance(provider, LLMProvider)


def test_unsupported_provider(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path, llm_provider="unknown-vendor")
    with pytest.raises(AppError) as exc_info:
        get_llm_provider(settings)
    assert exc_info.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR
