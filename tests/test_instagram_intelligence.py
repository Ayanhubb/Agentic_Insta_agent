"""Account intelligence: authorized reads, unavailable metrics, and tenant isolation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from db.crypto import encrypt_token
from db.enums import AccountStatus
from db.models import InstagramIntelligenceRecord
from db.repositories import InstagramAccountRepository
from models.errors import AppError, ErrorCode
from services.instagram_reader import InstagramReadClient
from backend.mcp.servers.account import account_intelligence_tools
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import trusted_tenant
from tests.helpers import DummyInstagramClient, auth_client_headers, test_settings


class FakeReader:
    def __init__(
        self,
        *,
        profile: dict | None = None,
        media: list[dict] | None = None,
        insights: dict | None = None,
        error: AppError | None = None,
    ) -> None:
        self.profile = profile or {
            "id": "ig-user-1",
            "username": "shop",
            "name": "Demo Shop",
            "followers_count": 100,
            "media_count": 0,
        }
        self.media = list(media or [])
        self.insights = insights or {}
        self.error = error
        self.calls = 0

    async def read_profile(self) -> dict:
        self.calls += 1
        if self.error:
            raise self.error
        return self.profile

    async def read_media(self, limit: int = 25) -> list[dict]:
        self.calls += 1
        if self.error:
            raise self.error
        return self.media[:limit]

    async def read_insight(self, object_id: str, metric: str, period: str) -> dict:
        del object_id, period
        self.calls += 1
        spec = self.insights.get(metric, "unsupported")
        if spec == "permission":
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "Instagram permission denied for this metric.",
                http_status=403,
                details={"graph_code": 10},
            )
        if spec == "unsupported":
            raise AppError(
                ErrorCode.API_ERROR,
                "Instagram does not provide that metric.",
                http_status=400,
                details={"graph_code": 100},
            )
        return {"data": [{"name": metric, "values": [{"value": spec}]}]}


def _app(tmp_path, reader: FakeReader) -> tuple[Settings, TestClient]:
    settings = test_settings(tmp_path, scheduler_enabled=False)
    app = create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    app.state.intelligence_reader = reader
    return settings, TestClient(app)


def _user_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    return response.json()["user"]["id"]


def _connect(
    client: TestClient,
    settings: Settings,
    user_id: str,
    *,
    status: str = AccountStatus.CONNECTED.value,
    expires_at: datetime | None = None,
    ig_id: str = "ig-user-1",
) -> None:
    session = client.app.state.session_factory()
    InstagramAccountRepository(session).upsert(user_id, ig_id, encrypt_token(settings, "ig-token-not-for-logs"))
    account = InstagramAccountRepository(session).get_primary(user_id)
    assert account is not None
    account.status = status
    account.token_expires_at = expires_at
    session.commit()
    session.close()


def _post(media_id: str, when: str, *, likes: int | None = 10, comments: int | None = 2, caption: str = "sarees") -> dict:
    payload = {
        "id": media_id,
        "caption": caption,
        "media_type": "IMAGE",
        "timestamp": when,
        "permalink": f"https://instagram.test/p/{media_id}",
    }
    if likes is not None:
        payload["like_count"] = likes
    if comments is not None:
        payload["comments_count"] = comments
    return payload


def test_connected_account_reports_available_metrics_without_secrets(tmp_path) -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    earlier = now - timedelta(days=14)
    reader = FakeReader(
        media=[
            _post("media-1", earlier.isoformat(), likes=10, comments=2, caption="silk sarees for Diwali"),
            _post("media-2", now.isoformat(), likes=30, comments=6, caption="20% off sarees today"),
        ],
        insights={"reach": 40, "profile_views": "permission"},
    )
    settings, client = _app(tmp_path, reader)
    with client:
        headers = auth_client_headers(client, email="intel-a@example.com")
        user_id = _user_id(client, headers)
        _connect(client, settings, user_id)
        account = client.get("/api/v1/intelligence/account", headers=headers)
        posts = client.get("/api/v1/intelligence/account/posts", headers=headers)
        insights = client.get("/api/v1/intelligence/account/insights", headers=headers)
        top = client.get("/api/v1/intelligence/account/top-content", headers=headers)
    assert account.status_code == 200
    body = account.json()
    assert body["account"]["instagram_account_id"] == "ig-user-1"
    assert body["account"]["captured_at"]
    rate = next(item for item in body["performance"]["metrics"] if item["metric"] == "engagement_rate")
    assert rate["status"] == "available"
    assert rate["value"] is not None
    blob = json.dumps(body["trend_context"])
    assert "ig-token-not-for-logs" not in blob
    assert "access_token" not in blob
    assert "silk sarees" not in blob
    assert "caused" not in blob.lower()
    assert posts.status_code == 200
    assert len(posts.json()["posts"]) == 2
    assert posts.json()["posts"][0]["caption"]
    assert "silk sarees" not in json.dumps(posts.json()["trend_context"])
    assert insights.status_code == 200
    reach = next(item for item in insights.json()["insights"] if item["metric"] == "reach" and item["object_type"] == "account")
    assert reach["status"] == "available"
    assert reach["value"] == 40
    impressions = next(item for item in insights.json()["insights"] if item["metric"] == "impressions")
    assert impressions["status"] == "unavailable"
    assert impressions["reason"] == "unsupported_metric"
    assert impressions["value"] is None
    views = next(item for item in insights.json()["insights"] if item["metric"] == "profile_views")
    assert views["status"] == "unavailable"
    assert views["reason"] == "missing_permission"
    assert top.status_code == 200
    assert top.json()["top_content"][0]["instagram_media_id"] == "media-2"
    assert any("higher average engagement" in note for note in top.json()["observations"])
    session = client.app.state.session_factory()
    stored = session.query(InstagramIntelligenceRecord).filter_by(user_id=user_id).all()
    session.close()
    assert stored
    stored_blob = json.dumps([row.payload for row in stored])
    assert "ig-token-not-for-logs" not in stored_blob
    assert "silk sarees" not in stored_blob
    assert "instagram.test" not in stored_blob


def test_disconnected_expired_permission_and_empty_history(tmp_path) -> None:
    reader = FakeReader()
    settings, client = _app(tmp_path, reader)
    with client:
        headers = auth_client_headers(client, email="intel-empty@example.com")
        user_id = _user_id(client, headers)
        missing = client.get("/api/v1/intelligence/account", headers=headers)
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "INSTAGRAM_NOT_CONNECTED"
        assert reader.calls == 0

        _connect(client, settings, user_id, status=AccountStatus.DISCONNECTED.value)
        disconnected = client.get("/api/v1/intelligence/account/posts", headers=headers)
        assert disconnected.status_code == 409
        assert reader.calls == 0

        _connect(
            client,
            settings,
            user_id,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        expired = client.get("/api/v1/intelligence/account", headers=headers)
        assert expired.status_code == 401
        assert expired.json()["error"]["code"] == "AUTHENTICATION_ERROR"
        assert reader.calls == 0

        _connect(client, settings, user_id)
        reader.error = AppError(ErrorCode.PERMISSION_ERROR, "Instagram permission denied.", http_status=403)
        denied = client.get("/api/v1/intelligence/account", headers=headers)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "PERMISSION_ERROR"

        reader.error = None
        reader.media = []
        empty = client.get("/api/v1/intelligence/account/top-content", headers=headers)
        assert empty.status_code == 200
        frequency = next(item for item in empty.json()["metrics"] if item["metric"] == "posting_frequency")
        assert frequency["status"] == "unavailable"
        assert frequency["reason"] == "no_posts"

        reader.media = [_post("only-1", datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat())]
        thin = client.get("/api/v1/intelligence/account/top-content", headers=headers)
        assert thin.status_code == 200
        consistency = next(item for item in thin.json()["metrics"] if item["metric"] == "posting_consistency")
        assert consistency["status"] == "unavailable"
        assert consistency["reason"] == "insufficient_historical_data"
        low = next(item for item in thin.json()["metrics"] if item["metric"] == "low_content")
        assert low["status"] == "unavailable"
        assert low["reason"] == "insufficient_historical_data"


def test_missing_like_count_is_not_treated_as_zero(tmp_path) -> None:
    reader = FakeReader(
        media=[
            {
                "id": "media-hidden",
                "caption": "hello",
                "media_type": "IMAGE",
                "timestamp": "2026-09-01T00:00:00+00:00",
                "comments_count": 1,
            }
        ]
    )
    settings, client = _app(tmp_path, reader)
    with client:
        headers = auth_client_headers(client, email="intel-hidden@example.com")
        user_id = _user_id(client, headers)
        _connect(client, settings, user_id)
        response = client.get("/api/v1/intelligence/account/posts", headers=headers)
    assert response.status_code == 200
    post = response.json()["posts"][0]
    likes = next(item for item in post["metrics"] if item["metric"] == "like_count")
    assert likes == {"metric": "like_count", "status": "unavailable", "value": None, "reason": "not_provided_by_meta", "unit": None}
    assert post["like_count"] is None
    assert post["engagement"] is None


def test_tenant_isolation(tmp_path) -> None:
    reader = FakeReader(media=[_post("media-owner", "2026-08-01T00:00:00+00:00", caption="private caption")])
    settings, client = _app(tmp_path, reader)
    with client:
        owner_headers = auth_client_headers(client, email="intel-owner@example.com")
        other_headers = auth_client_headers(client, email="intel-other@example.com")
        owner_id = _user_id(client, owner_headers)
        other_id = _user_id(client, other_headers)
        _connect(client, settings, owner_id)
        owned = client.get("/api/v1/intelligence/account/posts", headers=owner_headers)
        foreign = client.get("/api/v1/intelligence/account/posts", headers=other_headers)
        assert owned.status_code == 200
        assert "media-owner" in json.dumps(owned.json())
        assert foreign.status_code == 409
        assert "media-owner" not in json.dumps(foreign.json())
        assert "private caption" not in json.dumps(foreign.json())
        session = client.app.state.session_factory()
        other_rows = session.query(InstagramIntelligenceRecord).filter_by(user_id=other_id).all()
        owner_rows = session.query(InstagramIntelligenceRecord).filter_by(user_id=owner_id).all()
        session.close()
    assert other_rows == []
    assert owner_rows
    assert all(row.user_id == owner_id for row in owner_rows)


@pytest.mark.asyncio
async def test_reader_is_get_only_and_marks_unsupported_metrics(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "test-token" not in str(request.url)
        if request.url.path.endswith("/insights"):
            return httpx.Response(400, json={"error": {"message": "metric is not available", "code": 100}})
        return httpx.Response(200, json={"id": "ig-user-1", "username": "shop"})

    settings = test_settings(tmp_path)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = InstagramReadClient(settings, http=http)
    profile = await client.get_json("ig-user-1", {"fields": "id,username"})
    assert profile["username"] == "shop"
    with pytest.raises(AppError) as caught:
        await client.get_json("ig-user-1/insights", {"metric": "impressions", "period": "day"})
    assert caught.value.details["graph_code"] == 100
    await http.aclose()


@pytest.mark.asyncio
async def test_reader_maps_timeout_and_rate_limit(tmp_path) -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        raise httpx.ReadTimeout("timed out")

    settings = test_settings(tmp_path)
    timeout_http = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    timeout_client = InstagramReadClient(settings, http=timeout_http)
    with pytest.raises(AppError) as timed_out:
        await timeout_client.get_json("ig-user-1", {"fields": "id"})
    assert timed_out.value.code == ErrorCode.TIMEOUT
    assert "test-token" not in str(timed_out.value)
    await timeout_http.aclose()

    def rate_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "test-token" not in str(request.url)
        return httpx.Response(429, json={"error": {"code": 4, "message": "Application request limit reached"}})

    rate_http = httpx.AsyncClient(transport=httpx.MockTransport(rate_handler))
    rate_client = InstagramReadClient(settings, http=rate_http)
    with pytest.raises(AppError) as limited:
        await rate_client.get_json("ig-user-1", {"fields": "id"})
    assert limited.value.code == ErrorCode.RATE_LIMITED
    assert "test-token" not in str(limited.value)
    await rate_http.aclose()


def test_read_client_source_does_not_publish() -> None:
    from pathlib import Path

    source = Path("services/instagram_reader.py").read_text(encoding="utf-8")
    assert "media_publish" not in source
    assert "create_image_container" not in source
    assert '"POST"' not in source
    assert "'POST'" not in source


@pytest.mark.asyncio
async def test_trend_mcp_receives_normalized_context_only(tmp_path) -> None:
    reader = FakeReader(
        media=[_post("media-mcp", "2026-09-01T00:00:00+00:00", caption="secret caption for deepseek")]
    )
    settings, client = _app(tmp_path, reader)
    with client:
        headers = auth_client_headers(client, email="intel-mcp@example.com")
        user_id = _user_id(client, headers)
        _connect(client, settings, user_id)
        gateway = RepositoryGateway(client.app.state.session_factory)
        tools = {tool.name: tool for tool in account_intelligence_tools(gateway, settings=settings, reader=reader)}
        summary = await tools["get_account_summary"].handler(trusted_tenant(user_id), {"user_id": "someone-else"})
        media = await tools["get_recent_media"].handler(trusted_tenant(user_id), {})
        other = await tools["get_account_summary"].handler(trusted_tenant("missing-user"), {})
    rendered = json.dumps({"summary": summary, "media": media})
    assert summary["found"] is True
    assert summary["trend_context"]["source"] == "instagram_account_intelligence"
    assert summary["trend_context"]["account_ref"] == "ig-user-1"
    assert "secret caption" not in rendered
    assert "posts" not in summary
    assert "username" not in media["trend_context"]
    assert "access_token" not in rendered
    assert other["found"] is False
    assert other["status"] == "unavailable"
    assert other["reason"] == "INSTAGRAM_NOT_CONNECTED"
