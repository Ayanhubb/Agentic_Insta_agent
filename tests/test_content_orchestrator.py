"""Content Agent orchestration: MCP context, grounded plans, QA retries, no publish."""

from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from agent.content_orchestrator import PLAN_ATTEMPT_CAP, QA_ATTEMPT_CAP, ContentOrchestrator
from agent.creative_validation import validate_creative_plan
from models.creative import (
    BrandFact,
    BusinessFact,
    CampaignType,
    CanvaFact,
    ContentOrchestrationRequest,
    CreativeContext,
    CreativePlan,
    ImageQAVerdict,
    SourcedFestival,
    SourcedProduct,
)
from models.errors import AppError, ErrorCode


class FakeMCP:
    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def invoke(self, name: str, arguments: dict, tenant) -> SimpleNamespace:
        del arguments, tenant
        self.calls.append(name)
        if name not in self.responses:
            raise AssertionError(name)
        return SimpleNamespace(data=self.responses[name])


class ScriptedCreative:
    def __init__(self, plans: list[CreativePlan], reviews: list[ImageQAVerdict] | None = None) -> None:
        self.plans = plans
        self.reviews = list(reviews or [ImageQAVerdict(passed=True, issues=[])])
        self.payloads: list[dict] = []
        self.plan_calls = 0
        self.qa_calls = 0

    async def create_plan(self, context: CreativeContext, *, feedback: str | None = None) -> CreativePlan:
        del feedback
        self.plan_calls += 1
        self.payloads.append(context.for_model())
        if len(self.plans) == 1:
            return self.plans[0]
        return self.plans.pop(0)

    async def review_image(self, **kwargs) -> ImageQAVerdict:
        del kwargs
        self.qa_calls += 1
        if len(self.reviews) == 1:
            return self.reviews[0]
        return self.reviews.pop(0)


class FakeImages:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.calls = 0
        self.requests = []

    async def generate(self, request, **kwargs):
        del kwargs
        self.calls += 1
        self.requests.append(request)
        buffer = BytesIO()
        Image.new("RGB", (128, 128), (20, 40, 60)).save(buffer, format="PNG")
        path = self.directory / f"img_{self.calls}.png"
        path.write_bytes(buffer.getvalue())
        return SimpleNamespace(
            id=str(uuid4()),
            user_id=request.user_id,
            original_prompt=getattr(request, "original_prompt", request.prompt),
            enhanced_prompt=request.prompt,
            model="mock-image",
            provider="openai",
            filename=path.name,
            storage_path=str(path),
            mime_type="image/png",
            width=128,
            height=128,
            image_bytes=path.read_bytes(),
        )


class FakeStore:
    def __init__(self) -> None:
        self.images = []
        self.tasks = []

    async def save_generated_image(self, record):
        self.images.append(record)
        return record

    async def save_content_task(self, task):
        self.tasks.append(task)
        return task


class RecordingCanva:
    def __init__(self) -> None:
        self.calls = 0

    async def query(self, *, user_id: str) -> dict:
        del user_id
        self.calls += 1
        return {"queried": True, "action": "none", "asset_ids": []}


def _context(**overrides) -> CreativeContext:
    base = dict(
        user_id="user-1",
        campaign_type=CampaignType.USER_PROMPT,
        business=BusinessFact(name="Pal Jewels", business_type="retail"),
        brand=BrandFact(style="heritage luxury"),
        products=[SourcedProduct(id="product:kundan-necklace", name="kundan necklace")],
        current_date=date(2026, 9, 21),
    )
    base.update(overrides)
    return CreativeContext(**base)


def _plan(**overrides) -> CreativePlan:
    base = dict(
        campaign_type=CampaignType.USER_PROMPT,
        festival=None,
        business_type="retail",
        audience=None,
        caption="Pal Jewels. kundan necklace.",
        hashtags=["#PalJewels"],
        creative_direction="heritage luxury",
        image_prompt="Photorealistic Instagram still image of Pal Jewels showing only the kundan necklace in warm light.",
        product_ids=["product:kundan-necklace"],
        asset_ids=[],
        canva_action="none",
        qa_requirements=["The image is a readable still."],
    )
    base.update(overrides)
    return CreativePlan(**base)


def _mcp() -> FakeMCP:
    return FakeMCP(
        {
            "get_business_profile": {
                "found": True,
                "profile": {
                    "business_name": "Pal Jewels",
                    "business_type": "retail",
                    "products": ["kundan necklace"],
                    "brand_style": "heritage luxury",
                },
            },
            "get_product": {"found": True, "product": {"name": "kundan necklace"}},
            "get_product_image": {"found": False, "asset": None, "assets": []},
            "get_active_offers": {"offers": []},
            "get_brand_guidelines": {
                "found": True,
                "guidelines": {"brand_style": "heritage luxury", "target_audience": None},
            },
            "get_company_logo": {"found": False, "asset": None},
            "get_upcoming_festivals": {"festivals": []},
        }
    )


def _request(**overrides) -> ContentOrchestrationRequest:
    payload = dict(
        user_id="user-1",
        authenticated=True,
        mode="USER_PROMPT",
        user_prompt="Show the kundan necklace on a linen tray in warm light.",
    )
    payload.update(overrides)
    return ContentOrchestrationRequest(**payload)


async def _run(tmp_path: Path, **kwargs):
    creative = kwargs.pop("creative", ScriptedCreative([_plan()]))
    images = kwargs.pop("images", FakeImages(tmp_path))
    mcp = kwargs.pop("mcp", _mcp())
    store = kwargs.pop("store", FakeStore())
    canva = kwargs.pop("canva", RecordingCanva())
    orchestrator = ContentOrchestrator(
        creative,
        images,
        mcp,
        store=store,
        canva=canva,
        max_plan_attempts=kwargs.pop("max_plan_attempts", 2),
        max_qa_attempts=kwargs.pop("max_qa_attempts", 2),
    )
    result = await orchestrator.run(kwargs.pop("request", _request()))
    return result, creative, images, mcp, store, canva


@pytest.mark.asyncio
async def test_workflow_queries_mcp_and_returns_pending_asset(tmp_path: Path) -> None:
    result, creative, images, mcp, store, canva = await _run(tmp_path)

    assert mcp.calls[:7] == [
        "get_business_profile",
        "get_product",
        "get_product_image",
        "get_active_offers",
        "get_brand_guidelines",
        "get_company_logo",
        "get_upcoming_festivals",
    ]
    assert canva.calls == 0
    assert result.published is False
    assert result.strategy.published is False
    assert result.strategy.handoff.ready is False
    assert result.strategy.approval_status.value == "PENDING_APPROVAL"
    assert result.creative_plan.product_ids == ["product:kundan-necklace"]
    assert images.calls == 1
    assert images.requests[0].references[0].label == "kundan necklace"
    assert store.images[0].approval_status == "PENDING_APPROVAL"
    assert "user_id" not in creative.payloads[0]
    assert "access_token" not in json.dumps(creative.payloads[0])


@pytest.mark.asyncio
async def test_authentication_is_required(tmp_path: Path) -> None:
    with pytest.raises(AppError) as caught:
        await _run(tmp_path, request=_request(authenticated=False))
    assert caught.value.code == ErrorCode.AUTHENTICATION_ERROR


@pytest.mark.asyncio
async def test_missing_business_stops_before_other_queries(tmp_path: Path) -> None:
    mcp = _mcp()
    mcp.responses["get_business_profile"] = {"found": False, "profile": None}
    with pytest.raises(AppError) as caught:
        await _run(tmp_path, mcp=mcp)
    assert caught.value.code == ErrorCode.CONTENT_PROFILE_MISSING
    assert mcp.calls == ["get_business_profile"]


@pytest.mark.asyncio
async def test_invented_price_is_rejected_without_generating(tmp_path: Path) -> None:
    images = FakeImages(tmp_path)
    priced = _plan(caption="Pal Jewels kundan necklace for ₹499 only.")
    with pytest.raises(AppError) as caught:
        await _run(tmp_path, creative=ScriptedCreative([priced]), images=images, max_plan_attempts=2)
    assert caught.value.code == ErrorCode.CONTENT_INVALID_PLAN
    assert images.calls == 0


@pytest.mark.asyncio
async def test_invented_festival_date_is_rejected(tmp_path: Path) -> None:
    context = _context(
        campaign_type=CampaignType.FESTIVAL,
        festival=SourcedFestival(name="Diwali", date=date(2026, 11, 8), year=2026),
    )
    plan = _plan(
        campaign_type=CampaignType.FESTIVAL,
        festival="Diwali",
        caption="Pal Jewels Diwali on 2026-01-01.",
        image_prompt="Photorealistic Instagram still of Pal Jewels kundan necklace for Diwali, warm light, no text.",
    )
    with pytest.raises(AppError) as caught:
        validate_creative_plan(plan, context)
    assert "date" in caught.value.message


@pytest.mark.asyncio
async def test_unknown_product_id_is_rejected(tmp_path: Path) -> None:
    images = FakeImages(tmp_path)
    plan = _plan(product_ids=["product:missing"])
    with pytest.raises(AppError) as caught:
        await _run(tmp_path, creative=ScriptedCreative([plan]), images=images)
    assert caught.value.code == ErrorCode.CONTENT_INVALID_PLAN
    assert images.calls == 0


@pytest.mark.asyncio
async def test_canva_is_queried_only_when_requested(tmp_path: Path) -> None:
    canva = RecordingCanva()
    await _run(tmp_path, canva=canva, request=_request(use_canva=True))
    assert canva.calls == 1


@pytest.mark.asyncio
async def test_qa_failure_regenerates_within_bound(tmp_path: Path) -> None:
    images = FakeImages(tmp_path)
    creative = ScriptedCreative(
        [_plan()],
        [
            ImageQAVerdict(passed=False, issues=["unreadable"]),
            ImageQAVerdict(passed=True, issues=[]),
        ],
    )
    result, _, _, _, store, _ = await _run(tmp_path, creative=creative, images=images, max_qa_attempts=2)
    assert images.calls == 2
    assert result.qa_attempts == 2
    assert result.qa.passed is True
    assert len(store.images) == 1


@pytest.mark.asyncio
async def test_qa_stops_at_the_attempt_cap(tmp_path: Path) -> None:
    images = FakeImages(tmp_path)
    creative = ScriptedCreative([_plan()], [ImageQAVerdict(passed=False, issues=["unreadable"])])
    store = FakeStore()
    with pytest.raises(AppError) as caught:
        await _run(tmp_path, creative=creative, images=images, store=store, max_qa_attempts=99)
    assert caught.value.code == ErrorCode.CONTENT_QA_FAILED
    assert images.calls == QA_ATTEMPT_CAP
    assert store.images == []


def test_attempt_caps_are_bounded() -> None:
    assert PLAN_ATTEMPT_CAP == 3
    assert QA_ATTEMPT_CAP == 3


def test_model_payload_redacts_secrets() -> None:
    context = _context(
        business=BusinessFact(
            name="Pal Jewels",
            business_type="retail",
            description="Showroom sk-abcdefghijklmnopqrstuvwxyz",
        )
    )
    payload = json.dumps(context.for_model())
    assert "sk-" not in payload
    assert "user-1" not in payload


def test_orchestrator_source_does_not_publish() -> None:
    source = Path("agent/content_orchestrator.py").read_text(encoding="utf-8")
    assert "PublicationGateway" not in source
    assert "publish_instagram_media" not in source
    assert "media_publish" not in source
