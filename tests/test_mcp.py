"""MCP discovery, invocation, tenant isolation, and argument checks."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.mcp import (
    AssetAccessDenied,
    ContextBuilder,
    MalformedArguments,
    MCPClient,
    MCPTimeout,
    RepositoryGateway,
    TenantContextRequired,
    ToolNotAllowed,
    UnknownTool,
    build_registry,
    trusted_tenant,
)
from backend.mcp.permissions import MCP_TOOL_ALLOWLIST
from backend.mcp.registry import MCPTool, object_schema
from db.schemas import (
    AutomationSettingsWrite,
    BusinessProfileWrite,
    FestivalCampaignCreate,
)
from db.session import create_engine_from_url, init_db, session_factory
from db.uow import Database
from services.clock import FrozenClock

SECRET_PATH = "C:/secret/SECRET_ASSET_PATH_DO_NOT_LEAK.jpg"
SECRET_PROMPT = "SECRET_PROMPT_DO_NOT_LEAK"
OTHER_PATH = "C:/secret/SECRET_OTHER_PATH_DO_NOT_LEAK.jpg"
LOGO_PATH = "C:/secret/SECRET_LOGO_PATH_DO_NOT_LEAK.jpg"
OPENAI_KEY = "sk-proj-mcp-test-openai-key-should-not-log"
DEEPSEEK_KEY = "deepseek-secret-key-value-should-not-log"
META_TOKEN = "EAAGmeta-token-value-should-not-log"
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue"
ENCRYPTED = "gAAAAA-encrypted-credential-blob-should-not-log"


@pytest.fixture
def mcp_world(tmp_path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'mcp.db').as_posix()}")
    init_db(engine)
    factory = session_factory(engine)
    session = factory()
    db = Database(session)
    user_a = db.users.create(email="a@example.com", password_hash="hashed-a")
    user_b = db.users.create(email="b@example.com", password_hash="hashed-b")
    db.business.upsert(
        user_a.id,
        BusinessProfileWrite(
            business_name="Ayan Jewels",
            brand_style="warm gold",
            preferred_language="en",
            target_audience="local buyers",
            location="Kolkata",
            products=["Gold ring", "Silver chain"],
            services=["custom design"],
            description="Family jeweller",
        ),
    )
    db.business.upsert(
        user_b.id,
        BusinessProfileWrite(business_name="Other Shop", products=["Secret bowl"], brand_style="plain"),
    )
    db.automation.upsert(
        user_a.id,
        AutomationSettingsWrite(daily_enabled=True, festival_enabled=True, timezone="Asia/Kolkata"),
    )
    logo = db.generated_images.create(
        user_id=user_a.id,
        original_prompt="company logo mark",
        content_type="BRAND",
        theme="logo",
        filename="logo.png",
        storage_path=LOGO_PATH,
        mime_type="image/png",
        width=64,
        height=64,
        generation_status="SUCCEEDED",
        approval_status="APPROVED",
    )
    product_image = db.generated_images.create(
        user_id=user_a.id,
        original_prompt="gold ring on velvet",
        content_type="PRODUCT",
        theme="Gold ring",
        filename="ring.png",
        storage_path=SECRET_PATH,
        mime_type="image/png",
        width=80,
        height=80,
        generation_status="SUCCEEDED",
        approval_status="APPROVED",
    )
    offer = db.generated_images.create(
        user_id=user_a.id,
        original_prompt="festive making charges waived",
        content_type="PROMOTION",
        theme="Festive making charges",
        filename="offer.png",
        storage_path="C:/secret/offer.jpg",
        mime_type="image/png",
        width=80,
        height=80,
        generation_status="SUCCEEDED",
        approval_status="APPROVED",
    )
    other_image = db.generated_images.create(
        user_id=user_b.id,
        original_prompt=SECRET_PROMPT,
        content_type="PRODUCT",
        theme="Secret bowl",
        filename="bowl.png",
        storage_path=OTHER_PATH,
        mime_type="image/png",
        width=80,
        height=80,
        generation_status="SUCCEEDED",
        approval_status="APPROVED",
    )
    campaign_a = db.festivals.create(
        FestivalCampaignCreate(
            user_id=user_a.id,
            festival_name="Diwali",
            festival_date=date(2026, 11, 8),
            year=2026,
            required_posts=2,
        )
    )
    campaign_b = db.festivals.create(
        FestivalCampaignCreate(
            user_id=user_b.id,
            festival_name="Diwali",
            festival_date=date(2026, 11, 8),
            year=2026,
            required_posts=9,
        )
    )
    db.commit()
    world = SimpleNamespace(
        user_a=user_a.id,
        user_b=user_b.id,
        logo_id=logo.id,
        product_image_id=product_image.id,
        offer_id=offer.id,
        other_image_id=other_image.id,
        campaign_a=campaign_a.id,
        campaign_b=campaign_b.id,
    )
    session.close()
    clock = FrozenClock(datetime(2026, 9, 24, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
    gateway = RepositoryGateway(factory, clock=clock)
    client = MCPClient(build_registry(gateway), timeout_seconds=2.0)
    tenant_a = trusted_tenant(world.user_a)
    tenant_b = trusted_tenant(world.user_b)
    return client, world, tenant_a, tenant_b


def test_tool_discovery(mcp_world) -> None:
    client, _, _, _ = mcp_world
    discovered = client.discover()
    names = {item["name"] for item in discovered}
    servers = {item["server"] for item in discovered}
    assert names == set(MCP_TOOL_ALLOWLIST)
    assert servers == {"business", "brand", "product", "asset", "festival", "content", "account", "trend"}
    assert "publish_instagram_media" not in names
    assert "create_instagram_media" not in names
    assert "verify_publication" not in names
    for item in discovered:
        assert "handler" not in item
        assert item["input_schema"]["type"] == "object"
        assert item["input_schema"]["additionalProperties"] is False

    async def _unused(tenant, arguments):
        del tenant, arguments
        return {}

    with pytest.raises(ToolNotAllowed):
        client.registry.register(
            MCPTool(
                name="publish_instagram_media",
                description="publish",
                input_schema=object_schema(),
                server="instagram",
                handler=_unused,
            )
        )


async def test_tool_invocation(mcp_world) -> None:
    client, world, tenant_a, _tenant_b = mcp_world
    profile = await client.invoke("get_business_profile", {}, tenant_a)
    assert profile.data["tenant_id"] == world.user_a
    assert profile.data["profile"]["business_name"] == "Ayan Jewels"
    assert profile.data["profile"]["products"] == ["Gold ring", "Silver chain"]

    brand = await client.invoke("get_brand_guidelines", {}, tenant_a)
    assert brand.data["guidelines"]["brand_style"] == "warm gold"

    product = await client.invoke("get_product", {"name": "gold ring"}, tenant_a)
    assert product.data["product"]["name"] == "Gold ring"

    image = await client.invoke("get_product_image", {"image_id": world.product_image_id}, tenant_a)
    assert image.data["asset"]["id"] == world.product_image_id
    assert image.data["asset"]["media_url"].endswith(f"/{world.product_image_id}/media")
    rendered = json.dumps(image.data)
    assert SECRET_PATH not in rendered
    assert "storage_path" not in rendered

    logo = await client.invoke("get_company_logo", {}, tenant_a)
    assert logo.data["asset"]["id"] == world.logo_id
    assert LOGO_PATH not in json.dumps(logo.data)

    offers = await client.invoke("get_active_offers", {}, tenant_a)
    assert [item["id"] for item in offers.data["offers"]] == [world.offer_id]

    festivals = await client.invoke("get_upcoming_festivals", {}, tenant_a)
    as_of = festivals.data["as_of"]
    assert all(item["date"] >= as_of for item in festivals.data["festivals"])
    assert not any(item["festival_name"] == "Holi" for item in festivals.data["festivals"])
    diwali = next(item for item in festivals.data["festivals"] if item["festival_name"] == "Diwali")
    assert diwali["date"] == "2026-11-08"
    assert diwali["campaign"]["id"] == world.campaign_a
    assert diwali["campaign"]["required_posts"] == 2

    rules = await client.invoke("get_content_rules", {}, tenant_a)
    assert rules.data["publishing"]["available"] is False
    assert rules.data["publishing"]["user_prompt_auto_publish"] is False
    assert "PRODUCT" in rules.data["content_types"]
    assert rules.data["automation"]["festival_enabled"] is True
    assert "publish_instagram_media" not in json.dumps(rules.data)

    context = await ContextBuilder(client).build(tenant_a, festival_name="Diwali", product_name="Gold ring")
    assert context["tenant_id"] == world.user_a
    assert context["business"]["profile"]["business_name"] == "Ayan Jewels"
    assert context["product"]["product"]["name"] == "Gold ring"
    assert context["festival"]["festival"]["campaign"]["required_posts"] == 2
    blob = json.dumps(context)
    assert SECRET_PATH not in blob
    assert SECRET_PROMPT not in blob
    assert OTHER_PATH not in blob


async def test_tenant_isolation(mcp_world) -> None:
    client, world, tenant_a, _tenant_b = mcp_world
    spoofed = {
        "user_id": world.user_b,
        "tenant_id": world.user_b,
        "tenantId": world.user_b,
        "owner_id": world.user_b,
    }
    profile = await client.invoke("get_business_profile", spoofed, tenant_a)
    assert profile.data["tenant_id"] == world.user_a
    assert profile.data["profile"]["business_name"] == "Ayan Jewels"
    assert "Other Shop" not in json.dumps(profile.data)

    foreign = await client.invoke(
        "get_festival_details",
        {"campaign_id": world.campaign_b, "user_id": world.user_b},
        tenant_a,
    )
    assert foreign.data["found"] is False
    assert foreign.data["festival"] is None
    assert world.campaign_b not in json.dumps(foreign.data)

    own = await client.invoke(
        "get_festival_details",
        {"festival_name": "Diwali", "user_id": world.user_b},
        tenant_a,
    )
    assert own.data["festival"]["campaign"]["id"] == world.campaign_a
    assert own.data["festival"]["campaign"]["required_posts"] == 2

    with pytest.raises(TenantContextRequired):
        await client.invoke("get_business_profile", {"user_id": world.user_b}, None)
    with pytest.raises(TenantContextRequired):
        await client.invoke("get_business_profile", {}, {"tenant_id": world.user_b})  # type: ignore[arg-type]
    with pytest.raises(TenantContextRequired):
        trusted_tenant(world.user_b, source="llm")


async def test_unauthorized_asset_access(mcp_world, caplog: pytest.LogCaptureFixture) -> None:
    client, world, tenant_a, tenant_b = mcp_world
    caplog.set_level(logging.INFO, logger="backend.mcp")
    with pytest.raises(AssetAccessDenied) as denied:
        await client.invoke("get_product_image", {"image_id": world.product_image_id}, tenant_b)
    assert denied.value.code == "ASSET_ACCESS_DENIED"
    assert SECRET_PATH not in str(denied.value)
    assert SECRET_PROMPT not in str(denied.value)

    with pytest.raises(AssetAccessDenied):
        await client.invoke("get_company_logo", {"asset_id": world.logo_id}, tenant_b)
    with pytest.raises(AssetAccessDenied):
        await client.invoke("get_product_image", {"image_id": world.other_image_id}, tenant_a)

    owned = await client.invoke("get_product_image", {"product": "Gold ring"}, tenant_a)
    assert owned.data["asset"]["id"] == world.product_image_id
    leaked = caplog.text + json.dumps(owned.data) + str(denied.value)
    assert SECRET_PATH not in leaked
    assert OTHER_PATH not in leaked
    assert SECRET_PROMPT not in leaked
    assert LOGO_PATH not in leaked


async def test_unknown_tool(mcp_world) -> None:
    client, _, tenant_a, _tenant_b = mcp_world
    with pytest.raises(UnknownTool) as unknown:
        await client.invoke("not_a_real_tool", {}, tenant_a)
    assert unknown.value.code == "UNKNOWN_TOOL"
    with pytest.raises(ToolNotAllowed) as denied:
        await client.invoke("publish_instagram_media", {}, tenant_a)
    assert denied.value.code == "TOOL_NOT_ALLOWED"
    with pytest.raises(ToolNotAllowed):
        await client.invoke("create_instagram_media", {"user_id": "someone"}, tenant_a)


async def test_timeout(mcp_world) -> None:
    client, _, tenant_a, _tenant_b = mcp_world
    tool = client.registry.get("get_content_rules")

    async def slow(tenant, arguments):
        del tenant, arguments
        await asyncio.sleep(2)

    tool.handler = slow
    with pytest.raises(MCPTimeout) as timeout:
        await client.invoke("get_content_rules", {}, tenant_a, timeout=0.05)
    assert timeout.value.code == "TIMEOUT"


async def test_malformed_arguments(mcp_world, caplog: pytest.LogCaptureFixture) -> None:
    client, _, tenant_a, _tenant_b = mcp_world
    caplog.set_level(logging.INFO, logger="backend.mcp")
    poisoned = {
        "name": 5,
        "openai_api_key": OPENAI_KEY,
        "deepseek_api_key": DEEPSEEK_KEY,
        "jwt": JWT,
        "meta_access_token": META_TOKEN,
        "access_token_encrypted": ENCRYPTED,
    }
    with pytest.raises(MalformedArguments):
        await client.invoke("get_product", poisoned, tenant_a)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_product", {}, tenant_a)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_upcoming_festivals", {"within_days": True}, tenant_a)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_upcoming_festivals", {"within_days": 0}, tenant_a)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_festival_details", {}, tenant_a)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_business_profile", ["not-an-object"], tenant_a)  # type: ignore[arg-type]

    logged = caplog.text
    for secret in (OPENAI_KEY, DEEPSEEK_KEY, JWT, META_TOKEN, ENCRYPTED):
        assert secret not in logged
    assert "mcp.tool.execute" in logged
