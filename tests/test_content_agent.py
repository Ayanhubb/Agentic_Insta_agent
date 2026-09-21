"""Content Strategy Agent tests. Providers are always mocked."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agent.content_agent import (
    ContentAgent,
    DiversityPolicy,
    SemanticSimilarityBackend,
    TokenOverlapSimilarity,
    build_content_agent,
)
from agent.planner import ALLOWED_TOOL_SET
from models.content import (
    ApprovalStatus,
    AutomationSettingsSnapshot,
    BusinessProfileSnapshot,
    ContentMode,
    ContentSource,
    ContentStrategyRequest,
    ContentType,
    FestivalContext,
    GeneratedImageSnapshot,
    RecentContent,
)
from models.errors import AppError, ErrorCode

JEWELRY = BusinessProfileSnapshot(
    id="bp-1",
    user_id="user-1",
    business_name="Pal Jewels",
    business_type="retail",
    business_category="jewellery",
    description="Handcrafted gold and kundan jewellery from Kolkata.",
    target_audience="Indian families buying bridal and festive jewellery",
    location="Kolkata",
    brand_style="warm heritage luxury, close-up product photography",
    preferred_language="en-IN",
    products=["gold jhumkas", "kundan necklace", "bridal set"],
    services=["custom bridal jewellery"],
)

GYM = BusinessProfileSnapshot(
    id="bp-2",
    user_id="user-2",
    business_name="Howrah Strength Lab",
    business_type="fitness",
    business_category="gym",
    description="Strength training gym for working professionals.",
    location="Howrah",
    brand_style="high contrast, athletic, industrial lighting",
    products=["strength coaching", "group HIIT"],
    services=["personal training"],
)

DAILY_AUTO = AutomationSettingsSnapshot(
    daily_enabled=True,
    daily_posts_per_day=1,
    festival_enabled=True,
    festival_posts_per_festival=2,
    auto_daily_publish=True,
    auto_festival_publish=True,
    timezone="Asia/Kolkata",
)

APPROVAL_AUTO_OFF = AutomationSettingsSnapshot(
    daily_enabled=True,
    festival_enabled=True,
    auto_daily_publish=False,
    auto_festival_publish=False,
)

DIWALI = FestivalContext(name="Diwali", date=datetime(2026, 10, 29).date(), year=2026, required_posts=2)


def jewelry_plan(**overrides: Any) -> dict[str, Any]:
    payload = {
        "content_type": "PRODUCT",
        "theme": "kundan_necklace_evening",
        "image_prompt": (
            "Close-up of Pal Jewels kundan necklace on warm Kolkata heritage silk, "
            "luxury jewellery product photography, gold details catching light."
        ),
        "business_context": "Show Pal Jewels kundan necklace to bridal shoppers in Kolkata.",
        "reason": "daily product rotation",
        "caption_hint": "Heritage kundan, made in Kolkata.",
        "featured_product_or_service": "kundan necklace",
    }
    payload.update(overrides)
    return payload


class FakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append({"system": system_prompt, "user": user_prompt, "schema": schema})
        if not self.responses:
            raise AssertionError("FakeLLM has no remaining responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeImageGenerator:
    def __init__(self, fail: bool = False) -> None:
        self.prompts: list[str] = []
        self.fail = fail

    async def generate(self, *, prompt: str, user_id: str, source: str | None = None, **_: Any) -> GeneratedImageSnapshot:
        self.prompts.append(prompt)
        if self.fail:
            raise AppError(ErrorCode.OPENAI_API_ERROR, "image provider failed")
        return GeneratedImageSnapshot(
            id="img-1",
            user_id=user_id,
            original_prompt=prompt,
            enhanced_prompt=prompt,
            filename="img_test.png",
            storage_path=f"storage/generated/{user_id}/img_test.png",
            mime_type="image/png",
            width=1024,
            height=1024,
            generation_status="GENERATED",
            source=source,
        )


class MemoryStore:
    def __init__(
        self,
        *,
        profile: BusinessProfileSnapshot | None = JEWELRY,
        automation: AutomationSettingsSnapshot | None = DAILY_AUTO,
        posts: list[RecentContent] | None = None,
        generated: list[RecentContent] | None = None,
        festival: FestivalContext | None = DIWALI,
    ) -> None:
        self.profile = profile
        self.automation = automation
        self.posts = posts or []
        self.generated = generated or []
        self.festival = festival
        self.saved_images: list[GeneratedImageSnapshot] = []
        self.saved_tasks: list[Any] = []

    async def get_business_profile(self, user_id: str) -> BusinessProfileSnapshot | None:
        if self.profile and (self.profile.user_id in {None, user_id}):
            return self.profile
        return None

    async def get_automation_settings(self, user_id: str) -> AutomationSettingsSnapshot | None:
        return self.automation

    async def list_recent_posts(self, user_id: str, limit: int = 10) -> list[RecentContent]:
        return self.posts[:limit]

    async def list_recent_generated(self, user_id: str, limit: int = 10) -> list[RecentContent]:
        return self.generated[:limit]

    async def get_festival_context(self, user_id: str, on_date: datetime) -> FestivalContext | None:
        return self.festival

    async def save_generated_image(self, record: GeneratedImageSnapshot) -> GeneratedImageSnapshot:
        self.saved_images.append(record)
        return record

    async def save_content_task(self, task: Any) -> Any:
        self.saved_tasks.append(task)
        return task


def _agent(llm: FakeLLM, **kwargs: Any) -> ContentAgent:
    store = kwargs.pop("context_store", MemoryStore())
    images = kwargs.pop("image_generator", FakeImageGenerator())
    return build_content_agent(llm, images, context_store=store, **kwargs)


@pytest.mark.asyncio
async def test_user_prompt_strategy_awaits_approval_and_does_not_publish() -> None:
    llm = FakeLLM([jewelry_plan(reason="user asked for jhumkas", content_type="NEW_ARRIVAL")])
    images = FakeImageGenerator()
    publisher_calls: list[Any] = []

    class ForbiddenInstagram:
        async def run(self, *args: Any, **kwargs: Any) -> None:
            publisher_calls.append((args, kwargs))
            raise AssertionError("Content Agent must not publish")

    agent = ContentAgent(
        llm,
        images,
        context_store=MemoryStore(automation=DAILY_AUTO),
    )
    agent._instagram = ForbiddenInstagram()  # type: ignore[attr-defined]
    result = await agent.run(
        ContentStrategyRequest(
            user_id="user-1",
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show our new gold jhumkas for the evening collection.",
        )
    )

    assert result.mode == ContentMode.USER_PROMPT
    assert result.source == ContentSource.USER_PROMPT
    assert result.plan.content_type == ContentType.NEW_ARRIVAL
    assert "Pal Jewels" in llm.calls[0]["user"]
    assert "gold jhumkas" in llm.calls[0]["user"]
    assert result.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert result.task.requires_approval is True
    assert result.handoff.ready is False
    assert result.published is False
    assert publisher_calls == []
    assert result.generated_image is not None
    assert result.generated_image.approval_status == ApprovalStatus.PENDING_APPROVAL.value
    assert images.prompts
    assert "cannot publish to Instagram" in llm.calls[0]["system"]


@pytest.mark.asyncio
async def test_daily_strategy_uses_profile_date_and_auto_approves() -> None:
    llm = FakeLLM([jewelry_plan()])
    clock = lambda: datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    store = MemoryStore(automation=DAILY_AUTO)
    agent = _agent(llm, context_store=store, clock=clock)
    result = await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))

    user_prompt = llm.calls[0]["user"]
    assert "2026-09-21" in user_prompt
    assert "Pal Jewels" in user_prompt
    assert "auto_daily_publish" in user_prompt
    assert result.approval_status == ApprovalStatus.AUTO_APPROVED
    assert result.handoff.ready is True
    assert result.handoff.auto_approved is True
    assert result.published is False
    assert result.source == ContentSource.DAILY_AUTOMATION
    assert store.saved_tasks
    assert store.saved_images


@pytest.mark.asyncio
async def test_festival_strategy_is_business_specific() -> None:
    llm = FakeLLM(
        [
            jewelry_plan(
                content_type="FESTIVAL",
                theme="diwali_kundan_gifting",
                image_prompt=(
                    "Pal Jewels kundan necklace arranged with a single diya on a Kolkata brass tray, "
                    "heritage jewellery gifting scene for Diwali, not generic fireworks."
                ),
                business_context="Diwali gifting campaign for Pal Jewels bridal kundan in Kolkata.",
                reason="festival_campaign",
                featured_product_or_service="kundan necklace",
            )
        ]
    )
    result = await _agent(llm).run(
        ContentStrategyRequest(user_id="user-1", mode=ContentMode.FESTIVAL, festival=DIWALI)
    )
    prompt = llm.calls[0]["user"]
    assert "Diwali" in prompt
    assert "ONLY belong to this business" in prompt or "jewellery" in prompt.lower() or "Pal Jewels" in prompt
    assert result.plan.content_type == ContentType.FESTIVAL
    assert "Pal Jewels" in result.plan.image_prompt
    assert result.source == ContentSource.FESTIVAL_AUTOMATION
    assert result.published is False


@pytest.mark.asyncio
async def test_festival_rejects_generic_images_for_every_business() -> None:
    generic = {
        "content_type": "FESTIVAL",
        "theme": "happy_diwali",
        "image_prompt": "Happy Diwali fireworks and diyas festive greetings for everyone celebrating.",
        "business_context": "Generic Diwali wishes with fireworks.",
        "reason": "festival_campaign",
    }
    llm = FakeLLM([generic, generic])
    agent = _agent(llm, max_plan_attempts=2)
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.FESTIVAL, festival=DIWALI))
    assert caught.value.code == ErrorCode.CONTENT_INVALID_PLAN


@pytest.mark.asyncio
async def test_business_specific_strategy_includes_profile_products() -> None:
    llm = FakeLLM([jewelry_plan()])
    gym_llm = FakeLLM(
        [
            {
                "content_type": "LIFESTYLE",
                "theme": "evening_strength_session",
                "image_prompt": (
                    "Howrah Strength Lab athlete under industrial lighting performing a barbell lift, "
                    "athletic high-contrast gym photography."
                ),
                "business_context": "Daily lifestyle post for Howrah Strength Lab personal training.",
                "reason": "daily_automation",
                "featured_product_or_service": "personal training",
            }
        ]
    )
    jewelry_agent = _agent(llm, context_store=MemoryStore(profile=JEWELRY))
    gym_agent = _agent(gym_llm, context_store=MemoryStore(profile=GYM, festival=None))

    jewelry = await jewelry_agent.create_plan(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    gym = await gym_agent.create_plan(ContentStrategyRequest(user_id="user-2", mode=ContentMode.DAILY))

    assert "jhumkas" in llm.calls[0]["user"] or "kundan" in llm.calls[0]["user"]
    assert "Howrah Strength Lab" in gym_llm.calls[0]["user"]
    assert jewelry.image_prompt != gym.image_prompt
    assert "Pal Jewels" in jewelry.business_context
    assert "Howrah Strength Lab" in gym.business_context


@pytest.mark.asyncio
async def test_repeated_content_prevention_blocks_daily_duplicates() -> None:
    recent = RecentContent(
        id="old-1",
        content_type="PRODUCT",
        theme="kundan_necklace_evening",
        enhanced_prompt=(
            "Close-up of Pal Jewels kundan necklace on warm Kolkata heritage silk, "
            "luxury jewellery product photography, gold details catching light."
        ),
        product="kundan necklace",
    )
    llm = FakeLLM([jewelry_plan(), jewelry_plan()])
    agent = _agent(
        llm,
        context_store=MemoryStore(generated=[recent]),
        max_plan_attempts=2,
        diversity=DiversityPolicy(prompt_threshold=0.5, max_same_type=1),
    )
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.CONTENT_DIVERSITY_REJECTED
    assert llm.calls  # retried / attempted


@pytest.mark.asyncio
async def test_repeated_content_is_retried_then_accepted() -> None:
    recent = RecentContent(
        id="old-1",
        content_type="PRODUCT",
        theme="kundan_necklace_evening",
        enhanced_prompt="Close-up of Pal Jewels kundan necklace on warm Kolkata heritage silk.",
        product="kundan necklace",
    )
    llm = FakeLLM(
        [
            jewelry_plan(),
            jewelry_plan(
                content_type="EDUCATIONAL",
                theme="how_to_store_gold_jhumkas",
                image_prompt=(
                    "Educational flat-lay of Pal Jewels gold jhumkas beside a jewellery box in Kolkata, "
                    "showing careful storage, heritage brand style."
                ),
                business_context="Teach Pal Jewels customers how to store gold jhumkas.",
                reason="diversity retry",
                featured_product_or_service="gold jhumkas",
            ),
        ]
    )
    result = await _agent(
        llm,
        context_store=MemoryStore(generated=[recent]),
        diversity=DiversityPolicy(prompt_threshold=0.5, max_same_type=1),
    ).run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert result.plan.content_type == ContentType.EDUCATIONAL
    assert result.llm_attempts == 2
    assert result.diversity.accepted is True


@pytest.mark.asyncio
async def test_missing_business_profile() -> None:
    agent = _agent(FakeLLM([jewelry_plan()]), context_store=MemoryStore(profile=None))
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.CONTENT_PROFILE_MISSING


@pytest.mark.asyncio
async def test_llm_failure() -> None:
    agent = _agent(FakeLLM([AppError(ErrorCode.OPENAI_API_ERROR, "upstream failed")]))
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.OPENAI_API_ERROR


@pytest.mark.asyncio
async def test_llm_unexpected_exception_is_mapped() -> None:
    agent = _agent(FakeLLM([RuntimeError("provider down")]))
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.OPENAI_API_ERROR
    assert "provider down" not in caught.value.message


@pytest.mark.asyncio
async def test_invalid_structured_output() -> None:
    agent = _agent(FakeLLM(["not-json", "{bad"]), max_plan_attempts=2)
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.OPENAI_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_invalid_content_type_is_rejected() -> None:
    agent = _agent(
        FakeLLM(
            [
                jewelry_plan(content_type="REEL_STORY"),
                jewelry_plan(content_type="carousel"),
            ]
        ),
        max_plan_attempts=2,
    )
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.OPENAI_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_user_prompt_never_auto_publishes_even_when_automation_is_on() -> None:
    llm = FakeLLM([jewelry_plan(content_type="CUSTOMER_FOCUSED")])
    result = await _agent(llm, context_store=MemoryStore(automation=DAILY_AUTO)).run(
        ContentStrategyRequest(
            user_id="user-1",
            mode=ContentMode.USER_PROMPT,
            user_prompt="Make a customer love story around our bridal set.",
        )
    )
    assert result.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert result.handoff.ready is False
    assert result.published is False


@pytest.mark.asyncio
async def test_daily_disabled_is_rejected() -> None:
    agent = _agent(FakeLLM([jewelry_plan()]), context_store=MemoryStore(automation=AutomationSettingsSnapshot(daily_enabled=False)))
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.CONTENT_AUTOMATION_DISABLED


@pytest.mark.asyncio
async def test_policy_rejects_instagram_tool_selection() -> None:
    poisoned = jewelry_plan()
    poisoned["tool"] = "publish_instagram_media"
    agent = _agent(FakeLLM([poisoned, poisoned]), max_plan_attempts=2)
    with pytest.raises(AppError) as caught:
        await agent.run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert caught.value.code == ErrorCode.CONTENT_POLICY_VIOLATION
    assert "publish_instagram_media" in ALLOWED_TOOL_SET


@pytest.mark.asyncio
async def test_product_promotion_alias_from_llm_schema() -> None:
    llm = FakeLLM([jewelry_plan(content_type="product_promotion")])
    plan = await _agent(llm).create_plan(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    assert plan.content_type == ContentType.PROMOTION


@pytest.mark.asyncio
async def test_daily_without_auto_publish_awaits_approval() -> None:
    llm = FakeLLM([jewelry_plan(content_type="BRAND")])
    result = await _agent(llm, context_store=MemoryStore(automation=APPROVAL_AUTO_OFF)).run(
        ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY)
    )
    assert result.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert result.handoff.ready is False


def test_semantic_similarity_extension_point_is_inert_in_v1() -> None:
    backend = SemanticSimilarityBackend()
    assert backend.enabled is False
    assert backend.score("gold jhumkas", "gold earrings") == 0.0
    backend.enabled = True
    with pytest.raises(NotImplementedError):
        backend.score("gold jhumkas", "gold earrings")


def test_token_overlap_detects_near_duplicates() -> None:
    overlap = TokenOverlapSimilarity()
    left = "Pal Jewels kundan necklace on heritage silk in Kolkata"
    right = "Pal Jewels kundan necklace on heritage silk in Kolkata luxury shot"
    assert overlap.score(left, right) >= 0.7
    assert overlap.score(left, "Howrah Strength Lab barbell industrial lighting") < 0.2


@pytest.mark.asyncio
async def test_content_agent_does_not_use_instagram_tool_registry() -> None:
    llm = FakeLLM([jewelry_plan()])
    result = await _agent(llm).run(ContentStrategyRequest(user_id="user-1", mode=ContentMode.DAILY))
    blob = f"{result.plan.image_prompt} {result.plan.reason}".lower()
    for tool in ALLOWED_TOOL_SET:
        assert tool not in blob
    assert result.handoff.requires_instagram_agent is True
    assert result.published is False
