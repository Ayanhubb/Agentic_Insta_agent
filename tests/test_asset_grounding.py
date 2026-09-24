"""Owner-scoped logo and product grounding. Fixtures live under pytest tmp_path only."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent.content_orchestrator import ContentOrchestrator
from backend.mcp.errors import AssetAccessDenied
from backend.mcp.tenant_isolation import trusted_tenant
from db.models import BusinessAsset
from db.repositories import BusinessProfileRepository
from db.schemas import BrandWrite, BusinessProfileWrite, ProductWrite
from models.content import ContentMode, ContentStrategyRequest
from models.creative import ContentOrchestrationRequest, CreativePlan, ImageQAVerdict
from services.asset_catalog import AssetCatalog
from services.asset_resolution import (
    AssetState,
    resolve_brand_assets,
    resolve_brand_logo,
    resolve_owned_asset,
    resolve_product_asset,
    resolve_product_assets,
)
from services.clock import FrozenClock
from tests.test_content_orchestrator import FakeStore, ScriptedCreative
from tests.test_image_references import (
    CREATIVE,
    _agent,
    _attached,
    _mcp,
    _plan,
    _png,
    _provider,
    _world,
)

pytestmark = pytest.mark.asyncio


def _catalog(world) -> AssetCatalog:
    return AssetCatalog(world.session, world.settings)


def _business_id(world, user_id: str) -> str:
    profile = BusinessProfileRepository(world.session).get_for_user(user_id)
    assert profile is not None
    return profile.id


async def _generate(world, plan: dict):
    provider, client = _provider(world)
    agent = _agent(world, provider, [plan])
    await agent.run(
        ContentStrategyRequest(
            user_id=world.user_a,
            mode=ContentMode.USER_PROMPT,
            user_prompt="Show the Gold ring on linen.",
        )
    )
    return client


def _prompt(client) -> str:
    calls = client.images.edit_calls or client.images.generate_calls
    assert calls
    return calls[0]["prompt"]


async def test_product_image_exists_and_primary_is_selected(tmp_path: Path) -> None:
    world = _world(tmp_path)
    business_id = _business_id(world, world.user_a)
    primary = resolve_product_asset(world.session, world.settings, world.user_a, world.product_id)
    assets = resolve_product_assets(world.session, world.settings, world.user_a, world.product_id)

    assert primary.state is AssetState.AVAILABLE
    assert primary.asset_id == world.ring_id
    assert primary.link_role == "primary"
    assert primary.image is not None
    assert primary.image.data == world.ring_bytes
    assert [item.asset_id for item in assets] == [world.ring_id, world.side_id]
    assert assets[0].state is AssetState.AVAILABLE
    assert business_id

    client = await _generate(world, _plan(product_ids=[world.product_id]))
    blobs = _attached(client)
    assert blobs[0] == world.ring_bytes
    assert world.side_bytes in blobs
    assert world.other_bytes not in blobs
    assert world.logo_bytes not in blobs
    prompt = _prompt(client)
    assert CREATIVE in prompt
    assert "Use ring.png" not in prompt
    assert "Use the product" not in prompt


async def test_logo_exists(tmp_path: Path) -> None:
    world = _world(tmp_path)
    business_id = _business_id(world, world.user_a)
    logo = resolve_brand_logo(world.session, world.settings, world.user_a, business_id=business_id)
    brand = resolve_brand_assets(world.session, world.settings, world.user_a, business_id=business_id)

    assert logo.state is AssetState.AVAILABLE
    assert logo.asset_id == world.logo_id
    assert logo.business_id == business_id
    assert logo.image is not None
    assert logo.image.data == world.logo_bytes
    assert brand[0].state is AssetState.AVAILABLE
    assert brand[0].asset_id == world.logo_id

    client = await _generate(
        world,
        _plan(
            content_type="BRAND",
            featured_product_or_service=None,
            product_ids=[],
            logo_required=True,
            logo_asset_id=world.logo_id,
            reason="brand mark",
        ),
    )
    assert _attached(client) == [world.logo_bytes]
    prompt = _prompt(client)
    assert CREATIVE in prompt
    assert "company logo" in prompt
    assert "Use the company logo" not in prompt
    assert "Use logo.png" not in prompt


async def test_logo_and_product_image_both_exist(tmp_path: Path) -> None:
    world = _world(tmp_path)
    business_id = _business_id(world, world.user_a)
    logo = resolve_brand_logo(world.session, world.settings, world.user_a, business_id=business_id)
    product = resolve_product_asset(world.session, world.settings, world.user_a, world.product_id)
    assert logo.state is AssetState.AVAILABLE
    assert product.state is AssetState.AVAILABLE

    client = await _generate(
        world,
        _plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id]),
    )
    blobs = _attached(client)
    assert blobs[0] == world.logo_bytes
    assert world.ring_bytes in blobs
    assert world.side_bytes in blobs
    assert world.foreign_bytes not in blobs
    prompt = _prompt(client)
    assert CREATIVE in prompt
    assert "Use the company logo" not in prompt
    assert "Use the XYZ logo" not in prompt
    assert prompt != "Use the company logo"


async def test_logo_missing_continues_without_inventing_one(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _catalog(world).delete_asset(world.user_a, world.logo_id)
    world.session.commit()
    business_id = _business_id(world, world.user_a)
    logo = resolve_brand_logo(world.session, world.settings, world.user_a, business_id=business_id)
    product = resolve_product_asset(world.session, world.settings, world.user_a, world.product_id)

    assert logo.state is AssetState.MISSING
    assert logo.image is None
    assert product.state is AssetState.AVAILABLE

    client = await _generate(
        world,
        _plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id]),
    )
    blobs = _attached(client)
    assert world.logo_bytes not in blobs
    assert world.ring_bytes in blobs
    prompt = _prompt(client)
    assert "Use the company logo" not in prompt
    assert "company logo" not in prompt


async def test_product_image_missing_continues_from_metadata(tmp_path: Path) -> None:
    world = _world(tmp_path, files=False)
    catalog = _catalog(world)
    logo = catalog.upload(
        world.user_a,
        filename="logo.png",
        data=_png((12, 34, 56)),
        claimed_mime="image/png",
        role="logo_png",
    )
    catalog.upsert_brand(
        world.user_a,
        BrandWrite(company_name="Yotto Atelier", logo_png_asset_id=logo["id"]),
    )
    world.session.commit()
    product = resolve_product_asset(world.session, world.settings, world.user_a, world.product_id)
    resolved_logo = resolve_brand_logo(
        world.session,
        world.settings,
        world.user_a,
        business_id=_business_id(world, world.user_a),
    )

    assert product.state is AssetState.MISSING
    assert product.image is None
    assert resolved_logo.state is AssetState.AVAILABLE

    client = await _generate(
        world,
        _plan(logo_required=True, logo_asset_id=logo["id"], product_ids=[world.product_id]),
    )
    blobs = _attached(client)
    assert blobs == [resolved_logo.image.data]
    assert world.ring_bytes not in blobs
    prompt = _prompt(client)
    assert "product photograph" not in prompt
    assert "Use the product" not in prompt


async def test_invalid_asset_is_not_sent(tmp_path: Path) -> None:
    world = _world(tmp_path)
    logo = world.session.get(BusinessAsset, world.logo_id)
    path = world.settings.media_root / logo.storage_key
    path.write_bytes(b"not-a-real-image")
    world.session.commit()

    resolved = resolve_brand_logo(
        world.session,
        world.settings,
        world.user_a,
        business_id=_business_id(world, world.user_a),
        asset_id=world.logo_id,
    )
    assert resolved.state is AssetState.INVALID
    assert resolved.image is None

    client = await _generate(
        world,
        _plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id]),
    )
    blobs = _attached(client)
    assert b"not-a-real-image" not in blobs
    assert world.logo_bytes not in blobs
    assert world.ring_bytes in blobs
    assert "company logo" not in _prompt(client)


async def test_deleted_asset_is_not_sent(tmp_path: Path) -> None:
    world = _world(tmp_path)
    logo = world.session.get(BusinessAsset, world.logo_id)
    path = world.settings.media_root / logo.storage_key
    path.unlink()
    world.session.commit()

    resolved = resolve_owned_asset(world.session, world.settings, world.user_a, world.logo_id)
    by_business = resolve_brand_logo(
        world.session,
        world.settings,
        world.user_a,
        business_id=_business_id(world, world.user_a),
    )
    assert resolved.state is AssetState.DELETED
    assert resolved.image is None
    assert by_business.state is AssetState.DELETED

    client = await _generate(
        world,
        _plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id]),
    )
    blobs = _attached(client)
    assert world.logo_bytes not in blobs
    assert world.ring_bytes in blobs


async def test_foreign_tenant_asset_is_unauthorized(tmp_path: Path) -> None:
    world = _world(tmp_path)
    catalog = _catalog(world)
    foreign_product = catalog.create_product(world.user_b, ProductWrite(name="Other ring", sku="OTHER-1"))
    foreign_image = catalog.upload(
        world.user_b,
        filename="other-ring.png",
        data=world.foreign_bytes,
        claimed_mime="image/png",
        role="product_image",
        product_id=foreign_product["id"],
    )
    foreign_business = BusinessProfileRepository(world.session).upsert(
        world.user_b,
        BusinessProfileWrite(business_name="Other Shop"),
    )
    world.session.commit()

    product = resolve_product_asset(world.session, world.settings, world.user_a, foreign_product["id"])
    logo = resolve_brand_logo(
        world.session,
        world.settings,
        world.user_a,
        business_id=foreign_business.id,
    )
    brand = resolve_brand_assets(
        world.session,
        world.settings,
        world.user_a,
        business_id=foreign_business.id,
    )
    owned_file = resolve_owned_asset(world.session, world.settings, world.user_a, foreign_image["id"])

    assert product.state is AssetState.UNAUTHORIZED
    assert logo.state is AssetState.UNAUTHORIZED
    assert brand[0].state is AssetState.UNAUTHORIZED
    assert owned_file.state is AssetState.UNAUTHORIZED
    assert product.image is None
    assert logo.image is None
    assert owned_file.image is None
    assert owned_file.role is None

    client = _mcp(world)
    with pytest.raises(AssetAccessDenied):
        await client.invoke("get_product_image", {"image_id": foreign_image["id"]}, trusted_tenant(world.user_a))
    with pytest.raises(AssetAccessDenied):
        await client.invoke("get_company_logo", {"asset_id": world.foreign_logo_id}, trusted_tenant(world.user_a))
    hidden = await client.invoke(
        "get_product_image",
        {"product_id": foreign_product["id"]},
        trusted_tenant(world.user_a),
    )
    assert hidden.data["found"] is False

    generated = await _generate(
        world,
        _plan(
            featured_product_or_service=None,
            product_ids=[foreign_product["id"]],
            logo_required=True,
            logo_asset_id=world.foreign_logo_id,
            asset_ids=[foreign_image["id"]],
            reason="brand mark",
        ),
    )
    blobs = _attached(generated)
    assert world.foreign_bytes not in blobs
    assert world.logo_bytes in blobs


async def test_manual_generation_sends_resolved_files(tmp_path: Path) -> None:
    world = _world(tmp_path)
    business_id = _business_id(world, world.user_a)
    logo = resolve_brand_logo(world.session, world.settings, world.user_a, business_id=business_id)
    product = resolve_product_asset(world.session, world.settings, world.user_a, world.product_id)
    assert logo.state is AssetState.AVAILABLE
    assert product.state is AssetState.AVAILABLE

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
    assert logo.image is not None and product.image is not None
    assert blobs[0] == logo.image.data
    assert product.image.data in blobs
    assert world.side_bytes in blobs
    assert world.foreign_bytes not in blobs
    prompt = _prompt(client)
    assert CREATIVE in prompt
    assert "Use logo.png" not in prompt
    assert result.published is False


async def test_scheduler_generation_sends_resolved_files(tmp_path: Path) -> None:
    world = _world(tmp_path)
    business_id = _business_id(world, world.user_a)
    logo = resolve_brand_logo(world.session, world.settings, world.user_a, business_id=business_id)
    product = resolve_product_asset(world.session, world.settings, world.user_a, world.product_id)
    assert logo.image is not None and product.image is not None

    provider, client = _provider(world)
    agent = _agent(
        world,
        provider,
        [_plan(logo_required=True, logo_asset_id=world.logo_id, product_ids=[world.product_id])],
    )

    class _Publisher:
        def __init__(self) -> None:
            self.calls = 0

        async def publish_generated_image(self, **kwargs):
            del kwargs
            self.calls += 1
            raise AssertionError("Instagram publishing must not run")

    import api.app  # noqa: F401
    from db.models import User
    from db.repositories import AutomationRepository
    from scheduler.daily_scheduler import DailyScheduler

    publisher = _Publisher()
    clock = FrozenClock(datetime(2026, 9, 24, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
    scheduler = DailyScheduler(world.settings, world.session, agent, publisher, clock)
    user = world.session.get(User, world.user_a)
    automation = AutomationRepository(world.session).get_for_user(world.user_a)
    outcome = await scheduler.run_user(user, automation, force=True)

    assert outcome["status"] == "generated_pending_approval"
    assert publisher.calls == 0
    blobs = _attached(client)
    assert blobs[0] == logo.image.data
    assert product.image.data in blobs
    assert world.side_bytes in blobs
    assert world.foreign_bytes not in blobs
    prompt = _prompt(client)
    assert CREATIVE in prompt
    assert "Use the company logo" not in prompt
    assert "Use ring.png" not in prompt
