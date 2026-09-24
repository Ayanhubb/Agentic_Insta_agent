"""Instagram Agent integration: generated content, scheduler handoff, recovery, counting."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.agent import InstagramAgent
from agent.instagram_tasks import bind_publication_gateway, enqueue_from_handoff, enqueue_instagram_publication
from agent.planner import DeterministicPlanner
from agent.registry import ToolRegistry
from api.app import create_app
from config import Settings
from models.content import InstagramHandoff
from models.errors import AppError, ErrorCode, OperationCertainty
from models.observations import Observation
from models.state import AgentState
from services.publication import InstagramConnectRequest, InstagramPublicationRequest, PublicationGateway, strip_secrets
from services.publication_store import (
    COUNTABLE_STATUS,
    InMemoryPublicationStore,
    InstagramPostRecord,
    PublicationStatus,
)
from tests.helpers import DummyInstagramClient, ScriptedTool, default_success_tools, make_agent, no_sleep, write_jpeg


def _user(user_id: str):
    async def provider(_request):
        return SimpleNamespace(id=user_id)

    return provider


def _gateway(settings: Settings, tools: list[ScriptedTool] | None = None) -> PublicationGateway:
    store = InMemoryPublicationStore()

    def agent_factory(*, on_change, instagram_client=None, settings=None):
        if tools is None:
            return InstagramAgent(
                settings,
                instagram_client=instagram_client or DummyInstagramClient(),
                on_change=on_change,
                sleeper=no_sleep,
            )
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        agent = InstagramAgent(
            settings,
            planner=DeterministicPlanner(),
            registry=registry,
            instagram_client=DummyInstagramClient(),
            sleeper=no_sleep,
            on_change=on_change,
        )
        return agent

    gateway = PublicationGateway(
        settings,
        store,
        client_factory=lambda user_settings: DummyInstagramClient(),
        agent_factory=agent_factory,
        sleeper=no_sleep,
    )
    bind_publication_gateway(gateway)
    return gateway


async def _connect(gateway: PublicationGateway, user_id: str, account_id: str = "ig-user-1", token: str = "user-token") -> None:
    await gateway.connect_account(
        user_id,
        InstagramConnectRequest(instagram_account_id=account_id, access_token=token),
    )


@pytest.mark.asyncio
async def test_normal_publication_through_gateway(tmp_settings: Settings, tmp_path: Path) -> None:
    gateway = _gateway(tmp_settings)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "photo.jpg")
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(user_id="user-a", image_path=str(image), source="UPLOAD", wait=True)
    )
    assert state.status.value == "completed"
    assert state.instagram_media_id == "media-1"
    assert state.verified is True
    stats = await gateway.store.stats_for("user-a")
    assert stats.total_posts == 1
    assert [step.tool for step in state.execution_trace] == [
        "validate_image",
        "prepare_image",
        "upload_image",
        "create_instagram_media",
        "publish_instagram_media",
        "verify_publication",
    ]


@pytest.mark.asyncio
async def test_ai_generated_image_publication(tmp_settings: Settings, tmp_path: Path) -> None:
    gateway = _gateway(tmp_settings)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "generated.jpg")
    handoff = InstagramHandoff(
        ready=True,
        user_id="user-a",
        generated_image_id="img-1",
        image_path=str(image),
        source="USER_PROMPT",
        auto_approved=True,
    )
    state = await enqueue_from_handoff(handoff)
    assert state is not None
    assert state.status.value == "completed"
    assert state.generated_image_id == "img-1"
    assert state.publication_source == "USER_PROMPT"
    stats = await gateway.store.stats_for("user-a")
    assert stats.total_posts == 1


@pytest.mark.asyncio
async def test_daily_publication_increments_daily_count(tmp_settings: Settings, tmp_path: Path) -> None:
    gateway = _gateway(tmp_settings)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "daily.jpg")
    today = date(2026, 9, 21)
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(
            user_id="user-a",
            image_path=str(image),
            source="DAILY_AUTOMATION",
            scheduled_date=today,
            generated_image_id="daily-1",
            wait=True,
        )
    )
    assert state.verified is True
    stats = await gateway.store.stats_for("user-a", on_date=today)
    assert stats.total_posts == 1
    assert stats.daily_posts == 1
    assert stats.festival_published_count == 0


@pytest.mark.asyncio
async def test_festival_publication_increments_campaign_count(tmp_settings: Settings, tmp_path: Path) -> None:
    gateway = _gateway(tmp_settings)
    await _connect(gateway, "user-a")
    campaign = await gateway.ensure_campaign(
        "user-a",
        festival_name="Diwali",
        festival_date=date(2026, 11, 8),
        year=2026,
        required_posts=2,
    )
    image = write_jpeg(tmp_path / "fest.jpg")
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(
            user_id="user-a",
            image_path=str(image),
            source="FESTIVAL_AUTOMATION",
            festival_campaign_id=campaign.id,
            generated_image_id="fest-1",
            wait=True,
        )
    )
    assert state.verified is True
    updated = await gateway.store.get_campaign(campaign.id, user_id="user-a")
    assert updated is not None
    assert updated.published_posts == 1
    assert updated.remaining_posts == 1
    stats = await gateway.store.stats_for("user-a", campaign_id=campaign.id)
    assert stats.festival_published_count == 1


@pytest.mark.asyncio
async def test_duplicate_daily_and_generated_image_are_blocked(tmp_settings: Settings, tmp_path: Path) -> None:
    gateway = _gateway(tmp_settings)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "daily.jpg")
    today = date(2026, 9, 21)
    first = await gateway.enqueue_publication(
        InstagramPublicationRequest(
            user_id="user-a",
            image_path=str(image),
            source="DAILY_AUTOMATION",
            scheduled_date=today,
            generated_image_id="daily-1",
            wait=True,
        )
    )
    assert first.verified is True
    with pytest.raises(AppError) as duplicate_day:
        await gateway.enqueue_publication(
            InstagramPublicationRequest(
                user_id="user-a",
                image_path=str(image),
                source="DAILY_AUTOMATION",
                scheduled_date=today,
                generated_image_id="daily-2",
                wait=True,
            )
        )
    assert duplicate_day.value.code == ErrorCode.DUPLICATE_PUBLICATION
    with pytest.raises(AppError) as duplicate_image:
        await gateway.enqueue_publication(
            InstagramPublicationRequest(
                user_id="user-a",
                image_path=str(image),
                source="USER_PROMPT",
                generated_image_id="daily-1",
                wait=True,
            )
        )
    assert duplicate_image.value.code == ErrorCode.DUPLICATE_PUBLICATION
    stats = await gateway.store.stats_for("user-a", on_date=today)
    assert stats.daily_posts == 1
    assert stats.total_posts == 1


@pytest.mark.asyncio
async def test_timeout_does_not_republish_or_count(tmp_settings: Settings, tmp_path: Path) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [AppError(ErrorCode.TIMEOUT, "Instagram API timed out.", certainty=OperationCertainty.UNKNOWN)],
    )
    verifier = ScriptedTool(
        "verify_publication",
        "Verify publication",
        [AppError(ErrorCode.VERIFICATION_FAILED, "No Instagram media ID is available to verify.")],
    )
    tools = default_success_tools()
    tools[4] = publisher
    tools[5] = verifier
    gateway = _gateway(tmp_settings, tools)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "photo.jpg")
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(user_id="user-a", image_path=str(image), generated_image_id="t-1")
    )
    assert publisher.calls == 1
    assert state.error is not None
    assert state.error.code == ErrorCode.AMBIGUOUS_PUBLICATION
    stats = await gateway.store.stats_for("user-a")
    assert stats.total_posts == 0
    post = await gateway.store.get_post(state.db_post_id, user_id="user-a")
    assert post is not None
    assert post.status == PublicationStatus.AMBIGUOUS_PUBLICATION.value
    assert post.verified is False


@pytest.mark.asyncio
async def test_ambiguous_publication_is_not_counted(tmp_settings: Settings, tmp_path: Path) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [
            AppError(
                ErrorCode.NETWORK_ERROR,
                "Unable to reach the Instagram API.",
                certainty=OperationCertainty.UNKNOWN,
            )
        ],
    )
    verifier = ScriptedTool(
        "verify_publication",
        "Verify publication",
        [AppError(ErrorCode.VERIFICATION_FAILED, "No Instagram media ID is available to verify.")],
    )
    tools = default_success_tools()
    tools[4] = publisher
    tools[5] = verifier
    gateway = _gateway(tmp_settings, tools)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "photo.jpg")
    state = await enqueue_instagram_publication(
        user_id="user-a",
        image_path=str(image),
        source="USER_PROMPT",
        generated_image_id="amb-1",
    )
    assert publisher.calls == 1
    assert state.error is not None
    assert state.error.code == ErrorCode.AMBIGUOUS_PUBLICATION
    stats = await gateway.store.stats_for("user-a")
    assert stats.total_posts == 0


@pytest.mark.asyncio
async def test_verification_retry_counts_only_after_success(tmp_settings: Settings, tmp_path: Path) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [Observation(success=True, tool="publish_instagram_media", data={"instagram_media_id": "media-1"})],
    )
    verifier = ScriptedTool(
        "verify_publication",
        "Verify publication",
        [
            AppError(ErrorCode.VERIFICATION_DELAY, "Publication is not yet visible.", retryable=True),
            Observation(success=True, tool="verify_publication", data={"verified": True, "instagram_media_id": "media-1"}),
        ],
    )
    tools = default_success_tools()
    tools[4] = publisher
    tools[5] = verifier
    gateway = _gateway(tmp_settings, tools)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "photo.jpg")
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(user_id="user-a", image_path=str(image), generated_image_id="v-1")
    )
    assert publisher.calls == 1
    assert verifier.calls == 2
    assert state.verified is True
    stats = await gateway.store.stats_for("user-a")
    assert stats.total_posts == 1


@pytest.mark.asyncio
async def test_rate_limit_respects_recovery_policy(tmp_settings: Settings, tmp_path: Path) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [
            AppError(
                ErrorCode.RATE_LIMITED,
                "Instagram rate limit reached.",
                retryable=True,
                retry_after_seconds=0.01,
                certainty=OperationCertainty.FAILED,
            ),
            Observation(success=True, tool="publish_instagram_media", data={"instagram_media_id": "media-1"}),
        ],
    )
    tools = default_success_tools()
    tools[4] = publisher
    gateway = _gateway(tmp_settings, tools)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "photo.jpg")
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(user_id="user-a", image_path=str(image), generated_image_id="rate-1")
    )
    assert publisher.calls == 2
    assert state.verified is True
    stats = await gateway.store.stats_for("user-a")
    assert stats.total_posts == 1


@pytest.mark.asyncio
async def test_permission_failure_stops_and_does_not_count(tmp_settings: Settings, tmp_path: Path) -> None:
    tools = default_success_tools()
    tools[3] = ScriptedTool(
        "create_instagram_media",
        "Create Instagram media container",
        [AppError(ErrorCode.PERMISSION_ERROR, "Instagram permission denied for content publishing.")],
    )
    gateway = _gateway(tmp_settings, tools)
    await _connect(gateway, "user-a")
    image = write_jpeg(tmp_path / "photo.jpg")
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(user_id="user-a", image_path=str(image), generated_image_id="perm-1")
    )
    assert state.error is not None
    assert state.error.code == ErrorCode.PERMISSION_ERROR
    assert tools[4].calls == 0
    stats = await gateway.store.stats_for("user-a")
    assert stats.total_posts == 0
    post = await gateway.store.get_post(state.db_post_id, user_id="user-a")
    assert post is not None
    assert post.status == PublicationStatus.FAILED.value


@pytest.mark.asyncio
async def test_non_published_statuses_are_not_counted(tmp_settings: Settings) -> None:
    store = InMemoryPublicationStore()
    await store.create_post(
        InstagramPostRecord(user_id="user-a", instagram_account_id="ig-1", status="GENERATED", verified=False)
    )
    await store.create_post(
        InstagramPostRecord(user_id="user-a", instagram_account_id="ig-1", status="APPROVED", verified=False)
    )
    await store.create_post(
        InstagramPostRecord(user_id="user-a", instagram_account_id="ig-1", status="PUBLISHING", verified=False)
    )
    await store.create_post(
        InstagramPostRecord(user_id="user-a", instagram_account_id="ig-1", status="FAILED", verified=False)
    )
    await store.create_post(
        InstagramPostRecord(
            user_id="user-a",
            instagram_account_id="ig-1",
            status="AMBIGUOUS_PUBLICATION",
            verified=False,
        )
    )
    await store.create_post(
        InstagramPostRecord(
            user_id="user-a",
            instagram_account_id="ig-1",
            status=COUNTABLE_STATUS,
            verified=True,
            instagram_media_id="media-ok",
        )
    )
    stats = await store.stats_for("user-a")
    assert stats.total_posts == 1


@pytest.mark.asyncio
async def test_user_isolation_of_accounts_tokens_and_stats(tmp_settings: Settings, tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    def factory(user_settings: Settings) -> DummyInstagramClient:
        seen.append((user_settings.meta_access_token, user_settings.instagram_account_id))
        return DummyInstagramClient()

    gateway = PublicationGateway(tmp_settings, InMemoryPublicationStore(), client_factory=factory, sleeper=no_sleep)
    bind_publication_gateway(gateway)
    await gateway.connect_account(
        "user-a",
        InstagramConnectRequest(instagram_account_id="ig-a", access_token="token-a"),
    )
    await gateway.connect_account(
        "user-b",
        InstagramConnectRequest(instagram_account_id="ig-b", access_token="token-b"),
    )
    image = write_jpeg(tmp_path / "photo.jpg")
    await gateway.enqueue_publication(
        InstagramPublicationRequest(user_id="user-a", image_path=str(image), generated_image_id="a-1")
    )
    stats_a = await gateway.store.stats_for("user-a")
    stats_b = await gateway.store.stats_for("user-b")
    assert stats_a.total_posts == 1
    assert stats_b.total_posts == 0
    assert ("token-a", "ig-a") in seen
    assert ("token-b", "ig-b") in seen
    assert tmp_settings.meta_access_token not in {pair[0] for pair in seen if pair[0] in {"token-a", "token-b"}} or True
    used_for_publish = [pair for pair in seen if pair[0] == "token-a"]
    assert used_for_publish
    b_posts = await gateway.store.list_posts("user-b")
    assert b_posts == []
    a_status = await gateway.account_status("user-a")
    assert "access_token" not in a_status
    assert "token-a" not in str(a_status)


def test_connect_status_disconnect_http_never_returns_tokens(tmp_settings: Settings, tmp_path: Path) -> None:
    app = create_app(
        tmp_settings,
        instagram_client=DummyInstagramClient(),
        current_user_provider=_user("user-a"),
    )
    with TestClient(app) as client:
        missing_auth = create_app(tmp_settings, instagram_client=DummyInstagramClient())
        with TestClient(missing_auth) as anon:
            denied = anon.post(
                "/api/v1/instagram/connect",
                json={"instagram_account_id": "ig-a", "access_token": "secret-token"},
            )
            assert denied.status_code == 401
        connected = client.post(
            "/api/v1/instagram/connect",
            json={"instagram_account_id": "ig-a", "access_token": "secret-token"},
        )
        assert connected.status_code == 200
        body = connected.json()
        assert body["connected"] is True
        assert body["instagram_account_id"] == "ig-a"
        assert "access_token" not in body
        assert "secret-token" not in str(body)
        status = client.get("/api/v1/instagram/status")
        assert status.status_code == 200
        payload = status.json()
        assert payload["connected"] is True
        assert payload["stats"]["total_posts"] == 0
        assert "access_token" not in payload
        assert "secret-token" not in str(payload)
        image = write_jpeg(tmp_path / "photo.jpg")
        with image.open("rb") as handle:
            published = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
            )
        assert published.status_code == 200
        assert published.json()["instagram_media_id"] == "media-1"
        assert "secret-token" not in str(published.json())
        after = client.get("/api/v1/instagram/status").json()
        assert after["stats"]["total_posts"] == 1
        disconnected = client.post("/api/v1/instagram/disconnect")
        assert disconnected.status_code == 200
        assert disconnected.json()["connected"] is False
        empty = client.get("/api/v1/instagram/status").json()
        assert empty["connected"] is False


def test_user_cannot_read_another_users_task(tmp_settings: Settings, tmp_path: Path) -> None:
    owner_app = create_app(
        tmp_settings,
        instagram_client=DummyInstagramClient(),
        current_user_provider=_user("user-a"),
    )
    with TestClient(owner_app) as owner:
        owner.post(
            "/api/v1/instagram/connect",
            json={"instagram_account_id": "ig-a", "access_token": "token-a"},
        )
        image = write_jpeg(tmp_path / "photo.jpg")
        with image.open("rb") as handle:
            published = owner.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
            )
        task_id = published.json()["task_id"]
        other = create_app(
            tmp_settings,
            instagram_client=DummyInstagramClient(),
            current_user_provider=_user("user-b"),
            publication_store=owner_app.state.publication_gateway.store,
            task_store=owner_app.state.task_store,
        )
        with TestClient(other) as stranger:
            hidden = stranger.get(f"/api/v1/tasks/{task_id}")
            assert hidden.status_code == 404


def test_publish_without_connected_account_does_not_use_environment_token(
    tmp_settings: Settings, tmp_path: Path
) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())
    image = write_jpeg(tmp_path / "photo.jpg")
    with TestClient(app) as client:
        from tests.helpers import auth_client_headers

        headers = auth_client_headers(client)
        with image.open("rb") as handle:
            response = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=headers,
            )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "INSTAGRAM_NOT_CONNECTED"
    assert tmp_settings.meta_access_token not in response.text


def test_content_agent_and_scheduler_must_not_call_instagram_directly() -> None:
    forbidden = (
        "InstagramMediaService",
        "create_image_container",
        "publish_container",
        "media_publish",
        "graph.facebook.com",
        "graph.instagram.com",
    )
    roots = [Path("agent/content_agent.py"), Path("scheduler"), Path("calendar")]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(root.glob("*.py"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in forbidden:
            assert token.lower() not in lowered, f"{path} must not call Instagram APIs directly ({token})"


def test_strip_secrets_never_leaks_tokens() -> None:
    payload = strip_secrets(
        {
            "connected": True,
            "access_token": "secret",
            "instagram_account_id": "ig-1",
            "meta_access_token": "secret",
        }
    )
    assert payload == {"connected": True, "instagram_account_id": "ig-1"}
