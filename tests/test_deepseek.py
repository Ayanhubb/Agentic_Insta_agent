"""Mocked DeepSeek text and vision tests. Default pytest never calls the API."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ai.llm.deepseek import (
    CaptionRequest,
    CreativePlan,
    DeepSeekLLMProvider,
    FestivalStrategy,
    TextReasoning,
)
from ai.llm_client import LLMProvider, get_llm_provider
from ai.schemas import BusinessProfileContext, ContentPlanRequest, FestivalContext
from ai.vision.deepseek import (
    DeepSeekVisionProvider,
    ImageUnderstanding,
    VisionImage,
    VisionQaResult,
    get_vision_provider,
)
from models.errors import AppError, ErrorCode
from services.logging import redact_text, redact_value
from services.media_paths import MediaPaths
from tests.helpers import no_sleep, test_settings, write_jpeg

SECRET = "sk-deepseek-test-key-not-real-xxxx"


def _settings(tmp_path: Path, **overrides):
    values = dict(
        deepseek_api_key=SECRET,
        deepseek_model="deepseek-flash",
        llm_provider="deepseek",
        llm_max_attempts=2,
        openai_retry_delay_seconds=0.0,
    )
    values.update(overrides)
    return test_settings(tmp_path, **values)


def _completion(content: str, *, reasoning: str | None = None) -> dict:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "id": "chatcmpl-test",
        "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
    }


class ScriptedTransport:
    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls = 0
        self.payloads: list[dict] = []

    async def chat(self, payload: dict) -> dict:
        self.calls += 1
        self.payloads.append(payload)
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]


def _status(code: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    headers = {"retry-after": retry_after} if retry_after else None
    response = httpx.Response(code, request=request, headers=headers)
    return httpx.HTTPStatusError("failed", request=request, response=response)


def _plan_request() -> ContentPlanRequest:
    return ContentPlanRequest(
        user_prompt="Plan a Diwali post",
        current_date="2026-09-24",
        reason_hint="festival_campaign",
        business_profile=BusinessProfileContext(business_name="Silk House", products=["silk sarees"]),
        festival=FestivalContext(festival_name="Diwali", festival_date="2026-11-08", year=2026),
    )


def _owned_jpeg(settings, user_id: str) -> tuple[Path, str]:
    filename = f"img_{uuid4().hex}.jpg"
    path = MediaPaths(settings.media_root).image_file("generated", user_id, filename)
    write_jpeg(path, color=(12, 34, 56))
    return path, f"generated/{user_id}/{filename}"


@pytest.mark.asyncio
async def test_mocked_deepseek_text_response(tmp_path: Path) -> None:
    festival = {
        "festival_name": "Diwali",
        "strategy": "Silk House merchandises its silk sarees for Diwali.",
        "post_ideas": ["Window display"],
        "caption": "Silk House lights the window for Diwali.",
        "hashtags": ["Diwali", "SilkHouse"],
        "image_direction": "Silk House boutique window with silk sarees and warm diya light.",
        "business_context": "Silk House festive silk collection.",
    }
    transport = ScriptedTransport(
        [
            _completion("Feature Silk House silk for Diwali.", reasoning="Use the boutique, not a generic card."),
            _completion(json.dumps({"caption": "Silk House silk in the Diwali window.", "language": "English"})),
            _completion(json.dumps(festival)),
        ]
    )
    provider = DeepSeekLLMProvider(_settings(tmp_path), transport=transport, sleeper=no_sleep)

    reasoned = await provider.reason("Plan a Diwali post for Silk House")
    caption = await provider.write_caption(CaptionRequest(theme="diwali_silk", business_name="Silk House"))
    strategy = await provider.plan_festival(_plan_request())

    assert isinstance(reasoned, TextReasoning)
    assert reasoned.provider == "deepseek"
    assert reasoned.model == "deepseek-flash"
    assert reasoned.text == "Feature Silk House silk for Diwali."
    assert reasoned.reasoning == "Use the boutique, not a generic card."
    assert caption.caption.startswith("Silk House")
    assert isinstance(strategy, FestivalStrategy)
    assert "Silk House" in strategy.strategy
    assert strategy.hashtags[0] == "#Diwali"
    assert transport.payloads[0]["model"] == "deepseek-flash"
    assert transport.payloads[0]["thinking"]["type"] == "enabled"
    assert transport.payloads[1]["response_format"] == {"type": "json_object"}
    assert SECRET not in json.dumps(transport.payloads)
    assert "deepseek_api_key" not in reasoned.model_dump()


@pytest.mark.asyncio
async def test_mocked_deepseek_vision_response(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _path, ref = _owned_jpeg(settings, "user-a")
    understanding = {
        "summary": "A red silk saree in a boutique window.",
        "objects": ["saree"],
        "visible_text": [],
        "brand_cues": ["warm light"],
        "quality_notes": ["sharp still"],
        "suitable_for_instagram": True,
    }
    review = {
        "passed": True,
        "score": 0.91,
        "reasons": ["Shows Silk House silk."],
        "violations": [],
        "requires_regenerate": False,
    }
    transport = ScriptedTransport([_completion(json.dumps(understanding)), _completion(json.dumps(review))])
    provider = DeepSeekVisionProvider(settings, transport=transport, sleeper=no_sleep)
    image = VisionImage(user_id="user-a", storage_ref=ref)

    seen = await provider.understand_image(image)
    qa = await provider.review_image(image, brief="Silk House Diwali silk still.")

    assert isinstance(seen, ImageUnderstanding)
    assert seen.summary.startswith("A red silk")
    assert seen.suitable_for_instagram is True
    assert isinstance(qa, VisionQaResult)
    assert qa.passed is True
    block = transport.payloads[0]["messages"][1]["content"][1]
    assert block["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert SECRET not in json.dumps(transport.payloads)
    assert "deepseek_api_key" not in seen.model_dump()


@pytest.mark.asyncio
async def test_public_url_is_not_sent_as_base64(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    transport = ScriptedTransport(
        [
            _completion(
                json.dumps(
                    {
                        "answer": "A storefront window.",
                        "observations": ["silk"],
                    }
                )
            )
        ]
    )
    provider = DeepSeekVisionProvider(settings, transport=transport, sleeper=no_sleep)
    answer = await provider.answer_image_question(
        VisionImage(user_id="user-a", public_url="https://cdn.example.test/photo.jpg"),
        "What is in this photo?",
    )
    url = transport.payloads[0]["messages"][1]["content"][1]["image_url"]["url"]
    assert url == "https://cdn.example.test/photo.jpg"
    assert "base64" not in url
    assert answer.answer == "A storefront window."


@pytest.mark.asyncio
async def test_malformed_deepseek_response(tmp_path: Path) -> None:
    transport = ScriptedTransport([_completion("this is not json"), _completion("still not json")])
    provider = DeepSeekLLMProvider(_settings(tmp_path), transport=transport, sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.write_caption(CaptionRequest(theme="diwali"))
    assert exc_info.value.code == ErrorCode.DEEPSEEK_INVALID_RESPONSE
    assert SECRET not in exc_info.value.message
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_deepseek_timeout_is_retried(tmp_path: Path) -> None:
    transport = ScriptedTransport([httpx.TimeoutException("timed out"), httpx.TimeoutException("timed out")])
    provider = DeepSeekLLMProvider(_settings(tmp_path), transport=transport, sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.reason("Plan a post")
    assert exc_info.value.code == ErrorCode.DEEPSEEK_TIMEOUT
    assert exc_info.value.retryable is True
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_deepseek_rate_limit_is_retried(tmp_path: Path) -> None:
    transport = ScriptedTransport([_status(429, retry_after="0"), _completion("Recovered after the limit.")])
    provider = DeepSeekLLMProvider(_settings(tmp_path), transport=transport, sleeper=no_sleep)
    result = await provider.reason("Plan a post")
    assert result.text == "Recovered after the limit."
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_deepseek_rejected_request_is_not_retried(tmp_path: Path) -> None:
    transport = ScriptedTransport([_status(400)])
    provider = DeepSeekLLMProvider(_settings(tmp_path), transport=transport, sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.reason("Plan a post")
    assert exc_info.value.code == ErrorCode.DEEPSEEK_API_ERROR
    assert exc_info.value.retryable is False
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_deepseek_unavailable_after_retry(tmp_path: Path) -> None:
    transport = ScriptedTransport([_status(503), _status(503)])
    provider = DeepSeekLLMProvider(_settings(tmp_path), transport=transport, sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.reason("Plan a post")
    assert exc_info.value.code == ErrorCode.DEEPSEEK_UNAVAILABLE
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_missing_deepseek_key(tmp_path: Path) -> None:
    transport = ScriptedTransport([_completion("should not be called")])
    provider = DeepSeekLLMProvider(
        _settings(tmp_path, deepseek_api_key=""),
        transport=transport,
        sleeper=no_sleep,
    )
    with pytest.raises(AppError) as exc_info:
        await provider.reason("Plan a post")
    assert exc_info.value.code == ErrorCode.DEEPSEEK_CONFIGURATION_ERROR
    assert transport.calls == 0


@pytest.mark.asyncio
async def test_tenant_safe_image_access(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    owned, ref = _owned_jpeg(settings, "user-b")
    outside = tmp_path / "outside.jpg"
    write_jpeg(outside)
    blocked = ScriptedTransport([_completion("{}")])
    provider = DeepSeekVisionProvider(settings, transport=blocked, sleeper=no_sleep)

    for image in (
        VisionImage(user_id="user-a", storage_ref=ref),
        VisionImage(user_id="user-a", local_path=str(owned)),
        VisionImage(user_id="user-a", local_path=str(outside)),
        VisionImage(user_id="user-a", storage_ref=f"generated/user-a/../user-b/{owned.name}"),
        VisionImage(user_id="user-a", public_url="https://127.0.0.1/secret.jpg"),
        VisionImage(user_id="user-a", public_url="http://cdn.example.test/photo.jpg"),
    ):
        with pytest.raises(AppError) as exc_info:
            await provider.understand_image(image)
        assert exc_info.value.code in {ErrorCode.NOT_FOUND, ErrorCode.INVALID_REQUEST, ErrorCode.INVALID_IMAGE_URL}
        assert SECRET not in exc_info.value.message
    assert blocked.calls == 0

    allowed = ScriptedTransport(
        [
            _completion(
                json.dumps(
                    {
                        "summary": "Silk House product still.",
                        "objects": ["saree"],
                        "visible_text": [],
                        "brand_cues": [],
                        "quality_notes": [],
                        "suitable_for_instagram": True,
                    }
                )
            )
        ]
    )
    owner_provider = DeepSeekVisionProvider(settings, transport=allowed, sleeper=no_sleep)
    seen = await owner_provider.understand_image(VisionImage(user_id="user-b", storage_ref=ref))
    assert seen.summary == "Silk House product still."
    assert allowed.calls == 1
    assert allowed.payloads[0]["user"] == "user-b"


def test_factory_returns_deepseek_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    provider = get_llm_provider(settings)
    vision = get_vision_provider(settings)
    assert isinstance(provider, DeepSeekLLMProvider)
    assert isinstance(provider, LLMProvider)
    assert isinstance(vision, DeepSeekVisionProvider)
    status = settings.public_ai_status()
    assert status["deepseek_configured"] is True
    assert status["deepseek_model"] == "deepseek-flash"
    assert "deepseek_api_key" not in status
    assert SECRET not in json.dumps(status)


def test_deepseek_key_is_redacted() -> None:
    assert redact_text(f"DEEPSEEK_API_KEY={SECRET}") == "DEEPSEEK_API_KEY=[REDACTED]"
    redacted = redact_value({"deepseek_api_key": SECRET, "model": "deepseek-flash"})
    assert redacted["deepseek_api_key"] == "[REDACTED]"
    assert redacted["model"] == "deepseek-flash"


def test_deepseek_modules_do_not_publish_or_generate_images() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "ai" / "llm" / "deepseek.py").read_text(encoding="utf-8")
    source += (root / "ai" / "vision" / "deepseek.py").read_text(encoding="utf-8")
    for token in (
        "instagram_client",
        "media_publish",
        "subprocess",
        "os.system",
        "eval(",
        "exec(",
        "images.generate",
        "NEXT_PUBLIC_",
        "VITE_",
    ):
        assert token not in source


def test_creative_plan_ignores_tool_fields() -> None:
    plan = CreativePlan.model_validate(
        {
            "provider": "deepseek",
            "model": "deepseek-flash",
            "content_type": "festival",
            "theme": "diwali_silk",
            "concept": "Silk House window.",
            "image_prompt": "Silk House boutique window with silk sarees and warm diya light.",
            "caption": "Silk House for Diwali.",
            "hashtags": ["Diwali"],
            "business_context": "Silk House silk.",
            "reason": "festival_campaign",
            "command": "rm -rf /",
            "token": "should-be-ignored",
        }
    )
    assert "command" not in plan.model_dump()
    assert "token" not in plan.model_dump()
