"""Trend MCP: tenant isolation, compact results, and read-only access."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend.mcp import (
    MCPClient,
    MCPTimeout,
    MalformedArguments,
    RepositoryGateway,
    TenantContextRequired,
    ToolNotAllowed,
    UnknownTool,
    build_registry,
    trusted_tenant,
)
from db.enums import PostStatus, PostType
from db.schemas import BusinessProfileWrite
from db.session import create_engine_from_url, init_db, session_factory
from db.uow import Database
from services.clock import FrozenClock

META_TOKEN = "EAAGmeta-token-value-should-not-log"
OPENAI_KEY = "sk-proj-mcp-test-openai-key-should-not-log"
DEEPSEEK_KEY = "DEEPSEEK_API_KEY=deepseek-secret-key-value"
CANVA_TOKEN = "CANVA_TOKEN=canva-secret-token-value"
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue"
OTHER_TREND = "SECRET_OTHER_TREND"


@pytest.fixture
def trend_world(tmp_path):
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'trend.db').as_posix()}")
    init_db(engine)
    factory = session_factory(engine)
    session = factory()
    db = Database(session)
    user_a = db.users.create(email="trend-a@example.com", password_hash="hashed-a")
    user_b = db.users.create(email="trend-b@example.com", password_hash="hashed-b")
    empty = db.users.create(email="trend-empty@example.com", password_hash="hashed-e")
    db.business.upsert(
        user_a.id,
        BusinessProfileWrite(
            business_name="Ayan Kitchen",
            business_category="fnb",
            location="Kolkata",
            brand_style="warm",
            products=["Masala chai"],
        ),
    )
    db.instagram_accounts.upsert(user_a.id, "ig-a", META_TOKEN)
    db.instagram_accounts.upsert(user_b.id, "ig-b", "EAAGother-tenant-token")
    now = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    db.posts.create(
        user_id=user_a.id,
        status=PostStatus.PUBLISHED,
        post_type=PostType.FESTIVAL,
        instagram_media_id="media-a",
        verified=True,
        published_at=now,
        permalink="https://www.instagram.com/p/ayan/",
        error=META_TOKEN,
    )
    db.posts.create(
        user_id=user_b.id,
        status=PostStatus.PUBLISHED,
        post_type=PostType.USER_PROMPT,
        instagram_media_id="media-b",
        verified=True,
        published_at=now,
        permalink="https://www.instagram.com/p/other/",
    )
    fresh = db.trend_observations.create(
        user_a.id,
        industry="fnb",
        region="IN",
        festival="Diwali",
        content_type="FESTIVAL",
        title="Chai service sets",
        summary=OPENAI_KEY + " short festive drink note",
        evidence="Menu boards in Kolkata cafes mentioned chai sets.",
        source="research",
        observed_at=now,
    )
    stale = db.trend_observations.create(
        user_a.id,
        industry="fnb",
        region="IN",
        title="Old monsoon special",
        summary="Stale monsoon item",
        evidence="January menu audit.",
        source="research",
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    db.trend_observations.create(
        user_b.id,
        industry="fnb",
        region="IN",
        title=OTHER_TREND,
        summary="Other tenant research",
        evidence="Do not return this evidence.",
        source="research",
        observed_at=now,
    )
    for index in range(30):
        db.trend_observations.create(
            user_a.id,
            industry="fnb",
            region="IN-WB",
            title=f"batch-{index:02d}",
            summary=("detail " * 400) + OPENAI_KEY,
            evidence="x" * 2000,
            source="research",
            observed_at=now - timedelta(minutes=index),
        )
    db.trend_reports.create(
        user_a.id,
        title="September fnb",
        summary="Fresh report " + DEEPSEEK_KEY,
        industry="fnb",
        region="IN",
        observation_count=2,
        observed_at=now,
    )
    db.products.create(user_id=user_a.id, name="Masala chai", category="beverage", is_active=True)
    db.commit()
    session.close()
    clock = FrozenClock(datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc))
    client = MCPClient(build_registry(RepositoryGateway(factory, clock=clock)), timeout_seconds=2.0)
    return client, user_a.id, user_b.id, empty.id, fresh.id, stale.id


async def test_current_trends_are_compact_and_tenant_scoped(trend_world) -> None:
    client, user_a, user_b, _empty, fresh_id, stale_id = trend_world
    tenant = trusted_tenant(user_a)
    result = await client.invoke(
        "get_current_trends",
        {
            "industry": "fnb",
            "region": "IN",
            "limit": 10,
            "user_id": user_b,
            "business_id": "biz-other",
            "account_id": "ig-b",
        },
        tenant,
    )
    titles = [item["title"] for item in result.data["observations"]]
    assert result.data["tenant_id"] == user_a
    assert result.data["source"] == "trend_observations"
    assert "Chai service sets" in titles
    assert OTHER_TREND not in titles
    assert "Old monsoon special" not in titles
    assert result.data["stale_excluded"] >= 1
    assert all("evidence" not in item for item in result.data["observations"])
    rendered = json.dumps(result.data)
    assert OPENAI_KEY not in rendered
    assert user_b not in rendered

    evidence = await client.invoke("get_trend_evidence", {"observation_id": fresh_id}, tenant)
    assert evidence.data["found"] is True
    assert evidence.data["observation"]["id"] == fresh_id
    assert evidence.data["observation"]["stale"] is False
    assert "Kolkata" in evidence.data["observation"]["evidence"]

    stale = await client.invoke("get_trend_evidence", {"observation_id": stale_id}, tenant)
    assert stale.data["observation"]["stale"] is True

    foreign = await client.invoke(
        "get_trend_evidence",
        {"observation_id": fresh_id, "user_id": user_a},
        trusted_tenant(user_b),
    )
    assert foreign.data["found"] is False
    assert "Chai service sets" not in json.dumps(foreign.data)
    assert "Kolkata" not in json.dumps(foreign.data)


async def test_large_result_is_capped(trend_world) -> None:
    client, user_a, _user_b, _empty, _fresh, _stale = trend_world
    result = await client.invoke(
        "get_current_trends",
        {"industry": "fnb", "region": "IN-WB", "limit": 10},
        trusted_tenant(user_a),
    )
    observations = result.data["observations"]
    assert result.data["truncated"] is True
    assert len(observations) == 10
    assert all(len(item["summary"]) <= 240 for item in observations)
    assert "batch-29" not in json.dumps(result.data)
    blob = json.dumps(result.data)
    assert len(blob) < 8000
    assert OPENAI_KEY not in blob


async def test_missing_stale_and_meta_reads(trend_world) -> None:
    client, user_a, user_b, empty_id, _fresh, _stale = trend_world
    empty = trusted_tenant(empty_id)
    missing = await client.invoke("get_current_trends", {"limit": 5}, empty)
    assert missing.data["observations"] == []
    assert missing.data["found"] is False
    report = await client.invoke("get_latest_trend_report", {}, empty)
    assert report.data["found"] is False
    insights = await client.invoke("get_account_insights", {}, empty)
    assert insights.data["found"] is False
    assert insights.data["reason"] == "INSTAGRAM_NOT_CONNECTED"
    media = await client.invoke("get_recent_media", {}, empty)
    assert media.data["found"] is False
    assert media.data["reason"] == "INSTAGRAM_NOT_CONNECTED"
    empty_history = await client.invoke("get_publishing_history", {}, empty)
    assert empty_history.data["found"] is False
    assert empty_history.data["reason"] == "INSTAGRAM_NOT_CONNECTED"

    owner = trusted_tenant(user_a)
    summary = await client.invoke("get_account_summary", {"instagram_account_id": "ig-b"}, owner)
    rendered = json.dumps(summary.data)
    assert summary.data["found"] is False
    assert summary.data["reason"] == "AUTHENTICATION_ERROR"
    assert summary.data["source"] == "instagram_account_intelligence"
    assert META_TOKEN not in rendered
    assert "access_token" not in rendered
    assert "ig-b" not in rendered

    history = await client.invoke(
        "get_publishing_history",
        {"limit": 5, "instagram_account_id": "ig-b", "user_id": user_b},
        owner,
    )
    assert history.data["found"] is True
    assert history.data["live_graph"] is False
    assert history.data["instagram_account_id"] == "ig-a"
    assert [item["instagram_media_id"] for item in history.data["posts"]] == ["media-a"]
    assert META_TOKEN not in json.dumps(history.data)
    other_history = await client.invoke(
        "get_publishing_history",
        {"account_id": "ig-a"},
        trusted_tenant(user_b),
    )
    assert other_history.data["instagram_account_id"] == "ig-b"
    assert all(item["instagram_media_id"] != "media-a" for item in other_history.data["posts"])

    latest = await client.invoke("get_latest_trend_report", {}, owner)
    assert latest.data["found"] is True
    assert latest.data["stale"] is False
    assert DEEPSEEK_KEY not in json.dumps(latest.data)

    regional = await client.invoke("get_regional_trends", {"region": "in", "industry": "fnb", "limit": 5}, owner)
    assert any(item["title"] == "Chai service sets" for item in regional.data["observations"])
    assert all(item["region"] == "IN" for item in regional.data["observations"])

    festivals = await client.invoke(
        "get_festival_opportunities",
        {"limit": 5, "date_range": "2026-11-01/2026-11-15"},
        owner,
    )
    assert festivals.data["source"] == "festival_service"
    assert len(festivals.data["opportunities"]) <= 5
    assert any(item["title"] == "Diwali" for item in festivals.data["opportunities"])

    business = await client.invoke("get_business_opportunities", {"limit": 5}, owner)
    assert any(item["kind"] == "product" and item["title"] == "Masala chai" for item in business.data["opportunities"])
    content = await client.invoke("get_content_opportunities", {"limit": 5}, owner)
    assert any(item["content_type"] == "DAILY_RETAIL_POST" for item in content.data["opportunities"])
    assert user_b not in json.dumps(content.data)


async def test_invalid_arguments_unknown_tool_and_timeout(trend_world, caplog: pytest.LogCaptureFixture) -> None:
    client, user_a, _user_b, _empty, _fresh, _stale = trend_world
    tenant = trusted_tenant(user_a)
    caplog.set_level(logging.INFO, logger="backend.mcp")
    with pytest.raises(MalformedArguments):
        await client.invoke("get_current_trends", {"limit": 0}, tenant)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_current_trends", {"limit": 100}, tenant)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_current_trends", {"date_range": "yesterday"}, tenant)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_current_trends", {"date_range": "2026-09-24/2026-01-01"}, tenant)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_current_trends", {"industry": "F&B"}, tenant)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_current_trends", {"region": "india"}, tenant)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_regional_trends", {"industry": "fnb"}, tenant)
    with pytest.raises(MalformedArguments):
        await client.invoke("get_trend_evidence", {}, tenant)
    with pytest.raises(UnknownTool):
        await client.invoke("get_secret_trends", {}, tenant)
    with pytest.raises(ToolNotAllowed):
        await client.invoke("publish_instagram_media", {}, tenant)
    with pytest.raises(ToolNotAllowed):
        await client.invoke("create_instagram_media", {}, tenant)
    with pytest.raises(TenantContextRequired):
        await client.invoke("get_current_trends", {"user_id": user_a}, None)

    tool = client.registry.get("get_current_trends")

    async def slow(bound, arguments):
        del bound, arguments
        await asyncio.sleep(1)

    tool.handler = slow
    with pytest.raises(MCPTimeout):
        await client.invoke("get_current_trends", {"limit": 1}, tenant, timeout=0.05)

    logged = caplog.text
    for secret in (META_TOKEN, OPENAI_KEY, DEEPSEEK_KEY, CANVA_TOKEN, JWT):
        assert secret not in logged
