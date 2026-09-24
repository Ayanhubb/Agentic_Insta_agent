"""Integration wiring: providers, festival dates, QA gate, and secret status."""

from __future__ import annotations

from datetime import date

import pytest

from ai.llm_client import get_llm_provider
from ai.llm.deepseek import DeepSeekLLMProvider
from ai.vision.deepseek import get_vision_provider
from backend.integrations.canva.client import get_canva_client
from config import Settings
from festivals.mcp import get_festival_mcp
from models.content import ContentMode, ContentPlan, ContentType
from models.errors import AppError, ErrorCode
from scheduler.approval_policy import decide_approval
from tests.helpers import test_settings


def test_deepseek_factory_and_missing_key(tmp_path) -> None:
    settings = test_settings(tmp_path, llm_provider="deepseek", deepseek_api_key="", deepseek_model="deepseek-flash")
    provider = get_llm_provider(settings)
    assert isinstance(provider, DeepSeekLLMProvider)
    with pytest.raises(AppError) as exc:
        import asyncio

        asyncio.run(provider.generate_content_plan(_plan_request()))
    assert exc.value.code == ErrorCode.DEEPSEEK_CONFIGURATION_ERROR


def test_unconfigured_vision_and_canva_stay_off(tmp_path) -> None:
    settings = test_settings(tmp_path, vision_provider="deepseek", deepseek_api_key="", canva_enabled=False)
    assert get_vision_provider(settings) is None
    assert get_canva_client(settings) is None
    status = settings.public_ai_status()
    blob = str(status)
    assert "api_key" not in blob
    assert status["deepseek_configured"] is False
    assert status["canva_enabled"] is False


def test_festival_mcp_uses_catalog_date() -> None:
    context = get_festival_mcp(None).get_festival_context(
        user_id="user-a",
        on_date=date(2026, 10, 1),
        festival_name="Diwali",
    )
    assert context["valid"] is True
    assert context["date"] == "2026-11-08"
    assert context["source"] == "catalog"


def test_failed_qa_cannot_auto_publish() -> None:
    plan = ContentPlan(
        content_type=ContentType.PRODUCT,
        theme="retail",
        image_prompt="A detailed photo of the shop interior with products",
        business_context="Local retail shop",
        reason="daily",
    )
    decision = decide_approval(
        mode=ContentMode.DAILY,
        automation={"auto_daily_publish": True},
        plan=plan,
        profile={"business_name": "Shop", "products": ["ring"]},
        qa_passed=False,
        qa_malformed=False,
        ownership_ok=True,
        festival_valid=True,
        provider_malformed=False,
    )
    assert decision.publish is False
    assert "image_qa_failed" in decision.reasons


def _plan_request():
    from ai.schemas import ContentPlanRequest

    return ContentPlanRequest(user_prompt="A festive window display for this shop", reason_hint="user_prompt")
