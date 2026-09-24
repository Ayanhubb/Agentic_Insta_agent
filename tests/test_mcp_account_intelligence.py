"""Live MCP account tools use the authorized reader for the authenticated tenant."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from backend.mcp import (
    MCPClient,
    RepositoryGateway,
    ToolNotAllowed,
    build_registry,
    trusted_tenant,
)
from backend.mcp.servers.account import insights_for_analyst
from backend.trends.analyst import DeepSeekTrendAnalyst
from backend.trends.schemas import AccountInsight, TrendAnalysisRequest
from db.crypto import encrypt_token
from db.enums import AccountStatus, PostStatus, PostType
from db.models import InstagramIntelligenceRecord
from db.repositories import InstagramAccountRepository
from db.session import create_engine_from_url, init_db, session_factory
from db.uow import Database
from models.errors import AppError, ErrorCode
from tests.helpers import test_settings
from tests.test_trend_analysis import ScriptedChat

NOW = datetime(2026, 9, 20, 8, tzinfo=timezone.utc)
TOKEN_A = "ig-token-live-secret-a"
TOKEN_B = "ig-token-live-secret-b"
CAPTION_A = "SECRET_CAPTION_ALPHA"
CAPTION_B = "SECRET_CAPTION_BETA"


class RoutingReader:
    def __init__(self) -> None:
        self.bound: str | None = None
        self.calls: list[tuple[str, str | None]] = []
        self.profiles = {
            "ig-a": {
                "id": "ig-a",
                "username": "shop-a",
                "name": "Shop A",
                "followers_count": 100,
                "media_count": 2,
            },
            "ig-b": {
                "id": "ig-b",
                "username": "shop-b",
                "name": "Shop B",
                "followers_count": 50,
                "media_count": 1,
            },
        }
        earlier = (NOW - timedelta(days=14)).isoformat()
        self.media = {
            "ig-a": [
                _post("media-a-old", earlier, likes=4, comments=1, caption=CAPTION_A),
                _post("media-a-new", NOW.isoformat(), likes=20, comments=5, caption=CAPTION_A),
            ],
            "ig-b": [_post("media-b", NOW.isoformat(), likes=3, comments=1, caption=CAPTION_B)],
        }
        self.insights = {
            "ig-a": {"reach": 40, "profile_views": "permission"},
            "ig-b": {"reach": 9},
        }
        self.error: AppError | None = None
        self.transport_error: Exception | None = None

    def bind_account(self, account_id: str) -> None:
        self.bound = account_id
        self.calls.append(("bind", account_id))

    async def read_profile(self) -> dict:
        self.calls.append(("profile", self.bound))
        if self.transport_error is not None:
            raise self.transport_error
        if self.error is not None:
            raise self.error
        return dict(self.profiles[self.bound or ""])

    async def read_media(self, limit: int = 25) -> list[dict]:
        self.calls.append(("media", self.bound))
        if self.error is not None:
            raise self.error
        return list(self.media.get(self.bound or "", []))[:limit]

    async def read_insight(self, object_id: str, metric: str, period: str) -> dict:
        del object_id, period
        self.calls.append(("insight", self.bound))
        spec = self.insights.get(self.bound or "", {}).get(metric, "unsupported")
        if spec == "permission":
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "Instagram permission denied for this metric.",
                http_status=403,
                details={"graph_code": 10},
            )
        if spec == "rate":
            raise AppError(
                ErrorCode.RATE_LIMITED,
                "Instagram rate limit reached.",
                http_status=429,
                details={"graph_code": 4},
            )
        if spec == "timeout":
            raise AppError(ErrorCode.TIMEOUT, "Instagram API request timed out.", http_status=504)
        if spec == "unsupported":
            raise AppError(
                ErrorCode.API_ERROR,
                "Instagram does not provide that metric.",
                http_status=400,
                details={"graph_code": 100},
            )
        return {"data": [{"name": metric, "values": [{"value": spec}]}]}


def _post(media_id: str, when: str, *, likes: int, comments: int, caption: str) -> dict:
    return {
        "id": media_id,
        "caption": caption,
        "media_type": "IMAGE",
        "timestamp": when,
        "permalink": f"https://instagram.test/p/{media_id}",
        "like_count": likes,
        "comments_count": comments,
    }


def _status(factory, user_id: str, status: str, expires_at: datetime | None = None) -> None:
    session = factory()
    account = InstagramAccountRepository(session).get_primary(user_id)
    assert account is not None
    account.status = status
    account.token_expires_at = expires_at
    session.commit()
    session.close()


@pytest.fixture
def account_world(tmp_path: Path):
    settings = test_settings(tmp_path, deepseek_api_key="ds-test-key", llm_max_attempts=1)
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'mcp-account.db').as_posix()}")
    init_db(engine)
    factory = session_factory(engine)
    session = factory()
    db = Database(session)
    user_a = db.users.create(email="mcp-account-a@example.com", password_hash="hashed-a")
    user_b = db.users.create(email="mcp-account-b@example.com", password_hash="hashed-b")
    db.instagram_accounts.upsert(user_a.id, "ig-a", encrypt_token(settings, TOKEN_A))
    db.instagram_accounts.upsert(user_b.id, "ig-b", encrypt_token(settings, TOKEN_B))
    account_b = db.instagram_accounts.get_primary(user_b.id)
    assert account_b is not None
    db.posts.create(
        user_id=user_a.id,
        status=PostStatus.PUBLISHED,
        post_type=PostType.FESTIVAL,
        instagram_media_id="media-a",
        verified=True,
        published_at=NOW,
        permalink="https://www.instagram.com/p/ayana/",
        error=TOKEN_A,
    )
    db.posts.create(
        user_id=user_a.id,
        status=PostStatus.PUBLISHED,
        post_type=PostType.USER_PROMPT,
        instagram_account_id=account_b.id,
        instagram_media_id="foreign-link",
        verified=True,
        published_at=NOW,
        permalink="https://www.instagram.com/p/foreign/",
    )
    db.posts.create(
        user_id=user_b.id,
        status=PostStatus.PUBLISHED,
        post_type=PostType.USER_PROMPT,
        instagram_media_id="media-b",
        verified=True,
        published_at=NOW,
        permalink="https://www.instagram.com/p/other/",
        error=TOKEN_B,
    )
    db.commit()
    session.close()
    reader = RoutingReader()
    client = MCPClient(
        build_registry(RepositoryGateway(factory), settings=settings, reader=reader),
        timeout_seconds=2.0,
    )
    return client, reader, factory, user_a.id, user_b.id, settings


def test_live_registry_account_tools_reject_tenant_arguments(account_world) -> None:
    client, _reader, _factory, _user_a, _user_b, _settings = account_world
    discovered = {item["name"]: item for item in client.discover()}
    for name in (
        "get_account_summary",
        "get_recent_media",
        "get_account_insights",
        "get_top_content",
        "get_content_performance",
        "get_publishing_history",
    ):
        tool = discovered[name]
        assert tool["server"] == "account"
        properties = tool["input_schema"]["properties"]
        assert "user_id" not in properties
        assert "account_id" not in properties
        assert "instagram_account_id" not in properties
    assert "publish_instagram_media" not in discovered
    assert "create_instagram_media" not in discovered


async def test_authenticated_tenant_reads_its_own_account(account_world) -> None:
    client, reader, factory, user_a, user_b, _settings = account_world
    summary = await client.invoke(
        "get_account_summary",
        {"user_id": user_b, "account_id": "ig-b", "instagram_account_id": "ig-b"},
        trusted_tenant(user_a, source="scheduler"),
    )
    rendered = json.dumps(summary.data)
    assert summary.data["found"] is True
    assert summary.data["source"] == "instagram_account_intelligence"
    assert summary.data["tenant_id"] == user_a
    assert summary.data["trend_context"]["account_ref"] == "ig-a"
    assert summary.data["trend_context"]["source"] == "instagram_account_intelligence"
    assert CAPTION_A not in rendered
    assert CAPTION_B not in rendered
    assert TOKEN_A not in rendered
    assert TOKEN_B not in rendered
    assert "ig-b" not in rendered
    assert reader.calls[0] == ("bind", "ig-a")
    assert ("profile", "ig-a") in reader.calls
    assert ("media", "ig-b") not in reader.calls

    other = await client.invoke(
        "get_recent_media",
        {"user_id": user_a, "instagram_account_id": "ig-a", "limit": 5},
        trusted_tenant(user_b),
    )
    other_rendered = json.dumps(other.data)
    assert other.data["tenant_id"] == user_b
    assert other.data["trend_context"]["account_ref"] == "ig-b"
    assert "username" not in other.data["trend_context"]
    assert CAPTION_A not in other_rendered
    assert CAPTION_B not in other_rendered
    assert ("bind", "ig-b") in reader.calls
    assert "media-a-old" not in other_rendered
    assert "media-a-new" not in other_rendered

    session = factory()
    rows_b = session.query(InstagramIntelligenceRecord).filter_by(user_id=user_b).all()
    session.close()
    assert rows_b
    assert all(row.user_id == user_b for row in rows_b)
    assert CAPTION_A not in json.dumps([row.payload for row in rows_b])
    assert TOKEN_B not in json.dumps([row.payload for row in rows_b])


async def test_publishing_history_is_owned_and_does_not_call_meta(account_world) -> None:
    client, reader, _factory, user_a, user_b, _settings = account_world
    history = await client.invoke(
        "get_publishing_history",
        {"limit": 10, "instagram_account_id": "ig-b", "user_id": user_b},
        trusted_tenant(user_a),
    )
    ids = [item["instagram_media_id"] for item in history.data["posts"]]
    rendered = json.dumps(history.data)
    assert history.data["found"] is True
    assert history.data["live_graph"] is False
    assert history.data["instagram_account_id"] == "ig-a"
    assert ids == ["media-a"]
    assert "foreign-link" not in ids
    assert "media-b" not in ids
    assert TOKEN_A not in rendered
    assert reader.calls == []

    other = await client.invoke("get_publishing_history", {"account_id": "ig-a"}, trusted_tenant(user_b))
    assert other.data["instagram_account_id"] == "ig-b"
    assert [item["instagram_media_id"] for item in other.data["posts"]] == ["media-b"]
    assert TOKEN_B not in json.dumps(other.data)


async def test_disconnected_expired_empty_metric_and_meta_failures(account_world) -> None:
    client, reader, factory, user_a, _user_b, _settings = account_world
    tenant = trusted_tenant(user_a)

    _status(factory, user_a, AccountStatus.DISCONNECTED.value)
    disconnected = await client.invoke("get_account_summary", {}, tenant)
    assert disconnected.data["found"] is False
    assert disconnected.data["reason"] == "INSTAGRAM_NOT_CONNECTED"
    assert reader.calls == []

    _status(factory, user_a, AccountStatus.CONNECTED.value, NOW - timedelta(days=1))
    expired = await client.invoke("get_recent_media", {}, tenant)
    assert expired.data["reason"] == "AUTHENTICATION_ERROR"
    assert reader.calls == []

    _status(factory, user_a, AccountStatus.CONNECTED.value, None)
    reader.media["ig-a"] = []
    empty = await client.invoke("get_top_content", {}, tenant)
    frequency = next(item for item in empty.data["trend_context"]["metrics"] if item["metric"] == "posting_frequency")
    assert empty.data["found"] is True
    assert frequency["status"] == "unavailable"
    assert frequency["reason"] == "no_posts"
    assert CAPTION_A not in json.dumps(empty.data)

    reader.media["ig-a"] = RoutingReader().media["ig-a"]
    reader.calls.clear()
    insights = await client.invoke("get_account_insights", {}, tenant)
    metrics = insights.data["trend_context"]["metrics"]
    impressions = next(item for item in metrics if item["metric"] == "impressions")
    views = next(item for item in metrics if item["metric"] == "profile_views")
    reach = next(item for item in metrics if item["metric"] == "reach" and item["status"] == "available")
    assert impressions["status"] == "unavailable"
    assert impressions["reason"] == "unsupported_metric"
    assert impressions["value"] is None
    assert views["reason"] == "missing_permission"
    assert reach["value"] == 40

    reader.insights["ig-a"] = {"reach": "rate"}
    limited = await client.invoke("get_account_insights", {}, tenant)
    reasons = {item["reason"] for item in limited.data["trend_context"]["metrics"] if item["status"] == "unavailable"}
    assert "rate_limited" in reasons
    assert sum(1 for kind, _account in reader.calls if kind == "insight") >= 1

    before = len(reader.calls)
    reader.insights["ig-a"] = {"reach": "timeout"}
    timed = await client.invoke("get_account_insights", {}, tenant)
    timeout_reasons = [item["reason"] for item in timed.data["trend_context"]["metrics"]]
    assert timeout_reasons[0] == "timeout"
    assert all(reason == "timeout" for reason in timeout_reasons)
    insight_calls = [call for call in reader.calls[before:] if call[0] == "insight"]
    assert len(insight_calls) == 1

    reader.error = AppError(
        ErrorCode.API_ERROR,
        f"meta failed token={TOKEN_A}",
        http_status=502,
    )
    failed = await client.invoke("get_content_performance", {}, tenant)
    assert failed.data["found"] is False
    assert failed.data["reason"] == "API_ERROR"
    assert TOKEN_A not in json.dumps(failed.data)
    assert CAPTION_A not in json.dumps(failed.data)

    reader.error = None
    reader.transport_error = httpx.ReadTimeout("timed out")
    transport_timeout = await client.invoke("get_account_summary", {}, tenant)
    assert transport_timeout.data["reason"] == "TIMEOUT"
    assert "timed out" not in json.dumps(transport_timeout.data)

    reader.transport_error = None
    reader.profiles["ig-a"] = {"id": "ig-attacker", "username": "nope", "followers_count": 1}
    invalid = await client.invoke(
        "get_account_summary",
        {"instagram_account_id": "ig-attacker"},
        tenant,
    )
    assert invalid.data["found"] is False
    assert invalid.data["reason"] == "INVALID_ACCOUNT"
    assert "ig-attacker" not in json.dumps(invalid.data)
    last_bind = max(index for index, call in enumerate(reader.calls) if call == ("bind", "ig-a"))
    assert ("media", "ig-a") not in reader.calls[last_bind:]


async def test_publishing_tools_are_not_on_the_live_registry(account_world) -> None:
    client, _reader, _factory, user_a, _user_b, _settings = account_world
    tenant = trusted_tenant(user_a)
    for name in ("publish_instagram_media", "create_instagram_media", "media_publish", "publish"):
        with pytest.raises(ToolNotAllowed):
            await client.invoke(name, {"instagram_account_id": "ig-a"}, tenant)
    source = Path("backend/mcp/servers/account.py").read_text(encoding="utf-8")
    assert "media_publish" not in source
    assert "publish_instagram_media" not in source


async def test_mcp_account_context_is_what_reaches_the_trend_analyst(account_world) -> None:
    client, _reader, _factory, user_a, _user_b, settings = account_world
    summary = await client.invoke("get_account_summary", {"user_id": "someone-else"}, trusted_tenant(user_a))
    readings = insights_for_analyst(summary.data["trend_context"])
    assert readings
    assert all("caption" not in item for item in readings)
    chat = ScriptedChat(["not-json"])
    analyst = DeepSeekTrendAnalyst(settings, transport=chat)
    request = TrendAnalysisRequest(
        user_id=user_a,
        analyzed_at=NOW,
        account_insights=[AccountInsight(**item) for item in readings],
    )
    with pytest.raises(AppError):
        await analyst.analyze(request)
    prompt = json.dumps(chat.payloads)
    assert "The authorized Instagram sample recorded" in prompt
    assert str(readings[0]["value"]) in prompt
    assert CAPTION_A not in prompt
    assert TOKEN_A not in prompt
    assert "ig-b" not in prompt
    assert "do not publish" in prompt.lower() or "You do not publish" in prompt
