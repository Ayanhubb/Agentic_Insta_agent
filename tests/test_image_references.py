"""Owner-scoped logo and product files reach the OpenAI edit call."""

from __future__ import annotations

import base64
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from agent.content_agent import ContentAgent
from agent.content_orchestrator import ContentOrchestrator
from backend.ai.image.openai import OpenAIImageProvider
from backend.mcp import MCPClient, build_registry, trusted_tenant
from backend.mcp.errors import AssetAccessDenied
from backend.mcp.sources import RepositoryGateway
from db.models import BusinessAsset, ProductAsset
from db.schemas import (
    AutomationSettingsWrite,
    BrandWrite,
    BusinessProfileWrite,
    ProductWrite,
)
from db.session import create_engine_from_url, init_db, session_factory
from db.uow import Database
from models.content import ContentMode, ContentStrategyRequest
from models.creative import ContentOrchestrationRequest, CreativePlan, ImageQAVerdict
from services.asset_catalog import AssetCatalog
from services.clock import FrozenClock
from tests.helpers import no_sleep, test_settings
from tests.test_content_agent import FakeLLM
from tests.test_content_orchestrator import FakeStore, ScriptedCreative

CREATIVE = "Close-up of the Gold ring on linen in warm shop light, no added lettering."


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (48, 48), color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class _Images:
    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.generate_calls: list[dict] = []
        self.edit_calls: list[dict] = []

    async def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return self._next()

    async def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        return self._next()

    def _next(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Client:
    def __init__(self, script: list[object]) -> None:
        self.images = _Images(script)


def _response() -> SimpleNamespace:
    payload = _png((20, 40, 60))
    return SimpleNamespace(
        created=1_700_000_000,
        data=[SimpleNamespace(b64_json=base64.b64encode(payload).decode("ascii"), url=None, revised_prompt="")],
        usage=None,
        request_id="req_ref",
        size="1024x1024",
        quality=None,
        output_format="png",
        background=None,
    )


class _Publisher:
    def __init__(self) -> None:
        self.calls = 0

    async def publish_generated_image(self, **kwargs):
        del kwargs
        self.calls += 1
        raise AssertionError("Instagram publishing must not run")


def _settings(tmp_path: Path):
    return test_settings(
        tmp_path,
        openai_api_key="sk-test-not-a-real-openai-key-xxxxx",
        image_provider="openai",
        image_model="configured-image-model",
        image_size="",
        image_max_attempts=1,
        openai_retry_delay_seconds=0.0,
    )


def _world(tmp_path: Path, *, files: bool = True):
    settings = _settings(tmp_path)
    engine = create_engine_from_url(settings.database_url)
    init_db(engine)
    factory = session_factory(engine)
    session = factory()
    db = Database(session)
    user_a = db.users.create(email="owner@example.com", password_hash="hashed-a")
    user_b = db.users.create(email="other@example.com", password_hash="hashed-b")
    db.business.upsert(
        user_a.id,
        BusinessProfileWrite(
            business_name="Yotto Atelier",
            business_type="retail",
            products=["Gold ring"],
            description="Family jeweller",
        ),
    )
    db.automation.upsert(
        user_a.id,
        AutomationSettingsWrite(daily_enabled=True, auto_daily_publish=False, timezone="Asia/Kolkata"),
    )
    catalog = AssetCatalog(session, settings)
    logo_bytes = _png((12, 34, 56))
    ring_bytes = _png((180, 40, 40))
    side_bytes = _jpeg((20, 140, 60))
    other_bytes = _png((10, 10, 180))
    foreign_bytes = _png((200, 200, 10))
    logo_id = ring_id = side_id = other_image_id = foreign_logo_id = None
    product_id = None
    if files:
        logo = catalog.upload(
            user_a.id,
            filename="logo.png",
            data=logo_bytes,
            claimed_mime="image/png",
            role="logo_png",
        )
        logo_id = logo["id"]
        catalog.upload(
            user_a.id,
            filename="notes.txt",
            data=b"Use the ink color on white.",
            claimed_mime="text/plain",
            role="guideline",
        )
        catalog.upsert_brand(
            user_a.id,
            BrandWrite(
                company_name="Yotto Atelier",
                logo_png_asset_id=logo_id,
                guidelines="Use the ink color on white.",
            ),
        )
        ring = catalog.upload(
            user_a.id,
            filename="ring.png",
            data=ring_bytes,
            claimed_mime="image/png",
            role="product_image",
        )
        ring_id = ring["id"]
        product = catalog.create_product(
            user_a.id,
            ProductWrite(
                name="Gold ring",
                description="Daily ring",
                category="jewelry",
                price="499.50",
                sku="RING-1",
                offer="20% off",
                image_asset_id=ring_id,
            ),
        )
        product_id = product["id"]
        side = catalog.upload(
            user_a.id,
            filename="side.jpg",
            data=side_bytes,
            claimed_mime="image/jpeg",
            role="product_image",
        )
        side_id = side["id"]
        session.add(ProductAsset(user_id=user_a.id, product_id=product_id, asset_id=side_id, role="alternate"))
        other = catalog.create_product(user_a.id, ProductWrite(name="Plain stud", sku="STUD-1"))
        other_image = catalog.upload(
            user_a.id,
            filename="stud.png",
            data=other_bytes,
            claimed_mime="image/png",
            role="product_image",
            product_id=other["id"],
        )
        other_image_id = other_image["id"]
        foreign_logo = catalog.upload(
            user_b.id,
            filename="foreign.png",
            data=foreign_bytes,
            claimed_mime="image/png",
            role="logo_png",
        )
        foreign_logo_id = foreign_logo["id"]
        catalog.upsert_brand(user_b.id, BrandWrite(company_name="Other Shop", logo_png_asset_id=foreign_logo_id))
    else:
        product = catalog.create_product(user_a.id, ProductWrite(name="Gold ring", sku="RING-1"))
        product_id = product["id"]
    session.commit()
    return SimpleNamespace(
        settings=settings,
        session=session,
        factory=factory,
        user_a=user_a.id,
        user_b=user_b.id,
        logo_id=logo_id,
        logo_bytes=logo_bytes,
        ring_id=ring_id,
        ring_bytes=ring_bytes,
        side_id=side_id,
        side_bytes=side_bytes,
        other_bytes=other_bytes,
        other_image_id=other_image_id,
        foreign_logo_id=foreign_logo_id,
        foreign_bytes=foreign_bytes,
        product_id=product_id,
    )


def _provider(world, script: list[object] | None = None) -> tuple[OpenAIImageProvider, _Client]:
    client = _Client(script or [_response(), _response(), _response()])
    provider = OpenAIImageProvider(world.settings, client=client, sleeper=no_sleep)
    return provider, client


def _mcp(world) -> MCPClient:
    return MCPClient(build_registry(RepositoryGateway(world.factory)))


def _agent(world, provider, plans: list[dict]) -> ContentAgent:
    return ContentAgent(
        FakeLLM(plans),
        provider,
        session=world.session,
        settings=world.settings,
        mcp=_mcp(world),
    )


def _plan(**overrides) -> dict:
    payload = {
        "content_type": "PRODUCT",
        "theme": "gold_ring_linen",
        "image_prompt": CREATIVE,
        "business_context": "Show the Gold ring from Yotto Atelier.",
        "reason": "daily product rotation",
        "caption_hint": "Gold ring on linen.",
        "featured_product_or_service": "Gold ring",
        "logo_required": False,
    }
    payload.update(overrides)
    return payload


def _attached(client: _Client) -> list[bytes]:
    blobs: list[bytes] = []
    for call in client.images.edit_calls:
        for item in call.get("image") or []:
            blobs.append(item[1])
    return blobs


def _clear_image_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "OPENAI_IMAGE_MODEL", "OPENAI_IMAGE_SIZE", "OPENAI_IMAGE_QUALITY", "OPENAI_IMAGE_OUTPUT_FORMAT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_image_env(monkeypatch)


@pytest.mark.asyncio
async def test_mcp_returns_owner_scoped_logo_product_and_brand_asset(tmp_path: Path) -> None:
    world = _world(tmp_path)
    client = _mcp(world)
    tenant_a = trusted_tenant(world.user_a)
    tenant_b = trusted_tenant(world.user_b)

    logo = await client.invoke("get_company_logo", {"user_id": world.user_b}, tenant_a)
    assert logo.data["asset"]["id"] == world.logo_id
    assert logo.data["asset"]["role"] == "logo_png"
    assert "storage_key" not in logo.data["asset"]
    rendered = str(logo.data)
    assert "storage_key" not in rendered
    assert str(world.settings.media_root) not in rendered

    brand = await client.invoke("get_brand_guidelines", {}, tenant_a)
    assert brand.data["guidelines"]["sections"][0]["body"] == "Use the ink color on white."
    roles = {item["role"] for item in brand.data["assets"]}
    assert "logo_png" in roles
    assert "guideline" in roles
    assert "storage_key" not in str(brand.data)

    product = await client.invoke("get_product", {"name": "gold ring"}, tenant_a)
    assert product.data["product"]["name"] == "Gold ring"
    assert product.data["product"]["sku"] == "RING-1"
    assert product.data["product"]["price"] == "499.50"
    assert product.data["product"]["offer"] == "20% off"
    assert product.data["product"]["image_ids"][0] == world.ring_id
    assert world.side_id in product.data["product"]["image_ids"]

    image = await client.invoke("get_product_image", {"product": "Gold ring", "user_id": world.user_b}, tenant_a)
    ids = [item["id"] for item in image.data["assets"]]
    assert ids[0] == world.ring_id
    assert world.side_id in ids
    assert world.other_image_id not in ids
    assert "storage_key" not in str(image.data)

    with pytest.raises(AssetAccessDenied):
        await client.invoke("get_company_logo", {"asset_id": world.foreign_logo_id}, tenant_a)
    with pytest.raises(AssetAccessDenied):
        await client.invoke("get_product_image", {"image_id": world.ring_id}, tenant_b)
    missing = await client.invoke("get_company_logo", {}, tenant_b)
    assert missing.data["asset"]["id"] == world.foreign_logo_id


@pytest.mark.asyncio
async def test_product_asset_reaches_openai_adapter(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider, client = _provider(world)
    agent = _agent(world, provider, [_plan(product_ids=[world.product_id])])
    result = await agent.run(
        ContentStrategyRequest(
            user_id=world.user_a,
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the Gold ring on linen.",
        )
    )

    assert client.images.generate_calls == []
    blobs = _attached(client)
    assert world.ring_bytes in blobs
    assert world.side_bytes in blobs
    assert world.other_bytes not in blobs
    assert world.logo_bytes not in blobs
    prompt = client.images.edit_calls[0]["prompt"]
    assert CREATIVE in prompt
    assert "product photograph" in prompt
    assert "Use ring.png" not in prompt
    assert "Use the product" not in prompt
    assert world.user_a in Path(result.generated_image.storage_path).parts
    assert world.user_b not in Path(result.generated_image.storage_path).parts
    assert Path(result.generated_image.storage_path).is_file()


@pytest.mark.asyncio
async def test_company_logo_reaches_openai_adapter(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider, client = _provider(world)
    agent = _agent(
        world,
        provider,
        [_plan(content_type="BRAND", featured_product_or_service=None, product_ids=[], logo_required=True, logo_asset_id=world.logo_id, reason="brand mark")],
    )
    await agent.run(
        ContentStrategyRequest(
            user_id=world.user_a,
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the company mark on a linen card.",
        )
    )

    blobs = _attached(client)
    assert blobs == [world.logo_bytes]
    prompt = client.images.edit_calls[0]["prompt"]
    assert CREATIVE in prompt
    assert "company logo" in prompt
    assert "Use logo.png" not in prompt
    assert "Use the company logo" not in prompt
    assert client.images.generate_calls == []


@pytest.mark.asyncio
async def test_missing_asset_stays_text_only(tmp_path: Path) -> None:
    world = _world(tmp_path, files=False)
    provider, client = _provider(world)
    agent = _agent(world, provider, [_plan(logo_required=True)])
    await agent.run(
        ContentStrategyRequest(
            user_id=world.user_a,
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the Gold ring on linen.",
        )
    )

    assert client.images.edit_calls == []
    assert client.images.generate_calls
    prompt = client.images.generate_calls[0]["prompt"]
    assert prompt == CREATIVE
    assert "Use the company logo" not in prompt
    assert "Use the product" not in prompt


@pytest.mark.asyncio
async def test_foreign_asset_is_not_sent(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider, client = _provider(world)
    agent = _agent(
        world,
        provider,
        [
            _plan(
                content_type="BRAND",
                featured_product_or_service=None,
                product_ids=[],
                logo_required=True,
                logo_asset_id=world.foreign_logo_id,
                asset_ids=[world.foreign_logo_id],
                reason="brand mark",
            )
        ],
    )
    await agent.run(
        ContentStrategyRequest(
            user_id=world.user_a,
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the company mark on a linen card.",
        )
    )

    blobs = _attached(client)
    assert world.foreign_bytes not in blobs
    assert world.logo_bytes in blobs
    prompt = client.images.edit_calls[0]["prompt"]
    assert "Use foreign.png" not in prompt


@pytest.mark.asyncio
async def test_invalid_file_is_not_sent(tmp_path: Path) -> None:
    world = _world(tmp_path)
    logo = world.session.get(BusinessAsset, world.logo_id)
    path = world.settings.media_root / logo.storage_key
    path.write_bytes(b"not-a-real-image")
    world.session.commit()

    provider, client = _provider(world)
    agent = _agent(world, provider, [_plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id])])
    await agent.run(
        ContentStrategyRequest(
            user_id=world.user_a,
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the Gold ring on linen.",
        )
    )

    blobs = _attached(client)
    assert world.logo_bytes not in blobs
    assert b"not-a-real-image" not in blobs
    assert world.ring_bytes in blobs
    prompt = client.images.edit_calls[0]["prompt"]
    assert "company logo" not in prompt
    assert "product photograph" in prompt


@pytest.mark.asyncio
async def test_deleted_asset_is_not_sent(tmp_path: Path) -> None:
    world = _world(tmp_path)
    AssetCatalog(world.session, world.settings).delete_asset(world.user_a, world.logo_id)
    world.session.commit()
    client = _mcp(world)
    logo = await client.invoke("get_company_logo", {}, trusted_tenant(world.user_a))
    assert logo.data["found"] is False

    provider, image_client = _provider(world)
    agent = _agent(world, provider, [_plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id])])
    await agent.run(
        ContentStrategyRequest(
            user_id=world.user_a,
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the Gold ring on linen.",
        )
    )

    blobs = _attached(image_client)
    assert world.logo_bytes not in blobs
    assert world.ring_bytes in blobs


@pytest.mark.asyncio
async def test_manual_generation_sends_logo_and_product_files(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider, client = _provider(world)
    plan = CreativePlan(
        campaign_type="USER_PROMPT",
        business_type="retail",
        caption="Yotto Atelier. Gold ring.",
        hashtags=["#Yotto"],
        creative_direction="linen still life",
        image_prompt=CREATIVE,
        product_ids=[world.product_id],
        qa_requirements=["The ring stays recognizable."],
    )
    orchestrator = ContentOrchestrator(
        ScriptedCreative([plan], [ImageQAVerdict(passed=True, issues=[])]),
        provider,
        _mcp(world),
        store=FakeStore(),
        session=world.session,
        settings=world.settings,
    )
    result = await orchestrator.run(
        ContentOrchestrationRequest(
            user_id=world.user_a,
            authenticated=True,
            mode="USER_PROMPT",
            user_prompt="Show the Gold ring on linen.",
            product_id=world.product_id,
        )
    )

    blobs = _attached(client)
    assert world.logo_bytes in blobs
    assert world.ring_bytes in blobs
    assert world.side_bytes in blobs
    assert world.other_bytes not in blobs
    prompt = client.images.edit_calls[0]["prompt"]
    assert CREATIVE in prompt
    assert "Use logo.png" not in prompt
    assert result.published is False
    assert world.user_a in Path(result.strategy.generated_image.storage_path).parts


@pytest.mark.asyncio
async def test_scheduler_sends_references_and_does_not_publish(tmp_path: Path) -> None:
    world = _world(tmp_path)
    provider, client = _provider(world)
    agent = _agent(
        world,
        provider,
        [_plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id])],
    )
    publisher = _Publisher()
    clock = FrozenClock(datetime(2026, 9, 24, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
    import api.app  # noqa: F401
    from db.models import User
    from db.repositories import AutomationRepository
    from scheduler.daily_scheduler import DailyScheduler

    scheduler = DailyScheduler(world.settings, world.session, agent, publisher, clock)
    user = world.session.get(User, world.user_a)
    automation = AutomationRepository(world.session).get_for_user(world.user_a)
    outcome = await scheduler.run_user(user, automation, force=True)

    assert outcome["status"] == "generated_pending_approval"
    assert publisher.calls == 0
    blobs = _attached(client)
    assert world.logo_bytes in blobs
    assert world.ring_bytes in blobs
    assert world.side_bytes in blobs
    assert world.foreign_bytes not in blobs
    prompt = client.images.edit_calls[0]["prompt"]
    assert CREATIVE in prompt
    assert "Use logo.png" not in prompt
    assert "Use ring.png" not in prompt
