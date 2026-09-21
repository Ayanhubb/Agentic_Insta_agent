"""Explicit opt-in OpenAI integration tests.

Default pytest excludes this module via `-m "not real_openai"`.
Even when selected, tests skip unless RUN_OPENAI_INTEGRATION=1 and OPENAI_API_KEY are set.
"""

from __future__ import annotations

import os

import pytest

from ai.openai_image_generator import OpenAIImageGenerationProvider
from ai.openai_llm import OpenAILLMProvider
from ai.schemas import BusinessProfileContext, ContentPlanRequest, ImageGenerationRequest
from config import Settings

pytestmark = pytest.mark.real_openai

_RUN = os.getenv("RUN_OPENAI_INTEGRATION") == "1"
_KEY = bool(os.getenv("OPENAI_API_KEY", "").strip())
_MODEL = bool(os.getenv("LLM_MODEL", "").strip())
_IMAGE_MODEL = bool(os.getenv("IMAGE_MODEL", "").strip())


def _live_settings() -> Settings:
    return Settings.from_env()


@pytest.mark.skipif(not (_RUN and _KEY and _MODEL), reason="Set RUN_OPENAI_INTEGRATION=1, OPENAI_API_KEY, and LLM_MODEL")
@pytest.mark.asyncio
async def test_real_openai_content_plan() -> None:
    provider = OpenAILLMProvider(_live_settings())
    plan = await provider.generate_content_plan(
        ContentPlanRequest(
            user_prompt="Create a modest product photo concept for a local bakery.",
            current_date="2026-09-21",
            reason_hint="user_prompt",
            business_profile=BusinessProfileContext(
                business_name="Test Bakery",
                business_type="retail",
                products=["bread"],
                brand_style="warm and simple",
            ),
        )
    )
    assert plan.image_prompt
    assert plan.content_type
    assert plan.theme


@pytest.mark.skipif(
    not (_RUN and _KEY and _IMAGE_MODEL),
    reason="Set RUN_OPENAI_INTEGRATION=1, OPENAI_API_KEY, and IMAGE_MODEL",
)
@pytest.mark.asyncio
async def test_real_openai_image_generation(tmp_path) -> None:
    settings = _live_settings()
    settings.media_root = tmp_path / "storage"
    settings.ensure_directories()
    provider = OpenAIImageGenerationProvider(settings)
    record = await provider.generate(
        ImageGenerationRequest(
            prompt="A simple bakery storefront with fresh bread on a wooden counter, natural light, no text.",
            user_id="integration-test",
            original_prompt="bakery storefront",
        )
    )
    assert record.generation_status.value == "GENERATED"
    assert record.width and record.height
