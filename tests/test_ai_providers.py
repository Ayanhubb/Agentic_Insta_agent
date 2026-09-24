"""Provider abstraction, FastAPI wiring, and secret-boundary tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ai import (
    ImageGenerationProvider,
    LLMProvider,
    get_image_generation_provider,
    get_llm_provider,
)
from ai.openai_image_generator import OpenAIImageGenerationProvider
from ai.openai_llm import OpenAILLMProvider
from ai.schemas import ContentPlan, ContentPlanRequest, GeneratedImage
from api.app import create_app
from services.logging import redact_text, redact_value
from tests.helpers import DummyInstagramClient, test_settings


class ScriptedLLMProvider(LLMProvider):
    async def generate_content_plan(self, request: ContentPlanRequest) -> ContentPlan:
        return ContentPlan(
            content_type="product_promotion",
            theme="festive_collection",
            image_prompt="A boutique window display of silk sarees with warm diya lighting for Instagram.",
            business_context="Grounded in the boutique profile.",
            reason="festival_campaign",
        )


def test_provider_abstraction_allows_non_openai_implementation() -> None:
    provider: LLMProvider = ScriptedLLMProvider()
    assert isinstance(provider, LLMProvider)
    assert not isinstance(provider, OpenAILLMProvider)


def test_factories_return_configured_openai_providers(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        llm_provider="openai",
        openai_api_key="sk-test-not-a-real-openai-key-xxxxx",
        llm_model="configured-llm-model",
        image_model="configured-image-model",
    )
    llm = get_llm_provider(settings)
    images = get_image_generation_provider(settings)
    assert isinstance(llm, OpenAILLMProvider)
    assert isinstance(images, OpenAIImageGenerationProvider)
    assert isinstance(llm, LLMProvider)
    assert isinstance(images, ImageGenerationProvider)


def test_app_exposes_providers_without_api_key(tmp_settings) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    assert isinstance(app.state.llm_provider, LLMProvider)
    assert isinstance(app.state.image_provider, ImageGenerationProvider)
    status = tmp_settings.public_ai_status()
    assert "openai_api_key" not in status
    assert status["openai_configured"] is False
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert "openai_api_key" not in health.text
    assert "sk-" not in health.text


def test_injected_providers_are_used_by_fastapi(tmp_settings) -> None:
    llm = ScriptedLLMProvider()
    app = create_app(
        tmp_settings,
        instagram_client=DummyInstagramClient(),  # type: ignore[arg-type]
        llm_provider=llm,
    )
    assert app.state.llm_provider is llm


def test_generated_image_record_never_includes_secrets() -> None:
    record = GeneratedImage(
        user_id="user-1",
        original_prompt="prompt",
        enhanced_prompt="A detailed boutique display with silk sarees and warm lighting.",
        model="configured-image-model",
        provider="openai",
        filename="img_abc.png",
        storage_path="/tmp/img_abc.png",
        mime_type="image/png",
    )
    payload = record.public_dict()
    assert "openai_api_key" not in payload
    assert "api_key" not in payload
    assert "meta_access_token" not in payload


def test_openai_key_is_redacted_from_logs() -> None:
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"
    assert redact_text(f"OPENAI_API_KEY={secret}") == "OPENAI_API_KEY=[REDACTED]"
    assert secret not in redact_text(secret)
    redacted = redact_value({"openai_api_key": secret, "model": "configured-llm-model"})
    assert redacted["openai_api_key"] == "[REDACTED]"
    assert redacted["model"] == "configured-llm-model"


def test_llm_modules_do_not_import_instagram_or_shell() -> None:
    root = Path(__file__).resolve().parents[1]
    llm_source = (root / "ai" / "openai_llm.py").read_text(encoding="utf-8")
    image_source = (root / "ai" / "openai_image_generator.py").read_text(encoding="utf-8")
    forbidden = (
        "instagram_client",
        "subprocess",
        "os.system",
        "eval(",
        "exec(",
        "media_publish",
        "NEXT_PUBLIC_",
        "VITE_",
    )
    combined = llm_source + image_source
    for token in forbidden:
        assert token not in combined


def test_content_plan_ignores_tool_like_extra_fields() -> None:
    plan = ContentPlan.model_validate(
        {
            "content_type": "product_promotion",
            "theme": "festive_collection",
            "image_prompt": "A boutique window display of silk sarees with warm diya lighting for Instagram.",
            "business_context": "A Kolkata boutique promoting festive silk.",
            "reason": "festival_campaign",
            "command": "rm -rf /",
            "token": "should-be-ignored",
        }
    )
    assert not hasattr(plan, "command")
    dumped = plan.model_dump()
    assert "command" not in dumped
    assert "token" not in dumped
