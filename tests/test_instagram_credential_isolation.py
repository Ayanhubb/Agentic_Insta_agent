"""Publishing uses the signed-in user's Instagram token, never a process Meta token."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import api.app  # noqa: F401  — load the app package before the publication gateway
from config import Settings
from db.crypto import encrypt_token
from db.repositories import InstagramAccountRepository
from models.errors import AppError, ErrorCode
from scheduler.daily_scheduler import DailyScheduler
from scheduler.festival_scheduler import FestivalScheduler
from services.clock import FrozenClock
from services.publication import InstagramConnectRequest, InstagramPublicationRequest, PublicationGateway
from services.publication_store import AccountStatus, InMemoryPublicationStore, InstagramAccountRecord
from tests.helpers import DummyInstagramClient, no_sleep, test_settings, write_jpeg
from tests.test_scheduler import FakeContent, FakePublisher, _seed_user, _session


def _recording_gateway(settings: Settings) -> tuple[PublicationGateway, list[tuple[str, str]]]:
    seen: list[tuple[str, str]] = []

    def factory(user_settings: Settings) -> DummyInstagramClient:
        seen.append((user_settings.meta_access_token, user_settings.instagram_account_id))
        return DummyInstagramClient()

    gateway = PublicationGateway(
        settings,
        InMemoryPublicationStore(),
        client_factory=factory,
        sleeper=no_sleep,
    )
    return gateway, seen


@pytest.mark.asyncio
async def test_connected_account_publishes_with_that_users_token(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        app_env="production",
        instagram_legacy_env_fallback=True,
        meta_access_token="process-token",
        instagram_account_id="ig-process",
    )
    gateway, seen = _recording_gateway(settings)
    await gateway.connect_account(
        "user-a",
        InstagramConnectRequest(instagram_account_id="ig-a", access_token="token-a"),
    )
    image = write_jpeg(tmp_path / "photo.jpg")
    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(
            user_id="user-a",
            image_path=str(image),
            allow_environment_fallback=True,
        )
    )
    assert state.instagram_media_id == "media-1"
    assert ("token-a", "ig-a") in seen
    assert ("process-token", "ig-process") not in seen
    status = await gateway.account_status("user-a")
    assert status["connected"] is True
    assert status["environment_configured"] is False
    assert status["source"] == "user"
    assert "token-a" not in str(status)
    assert "process-token" not in str(status)


@pytest.mark.asyncio
async def test_disconnected_account_fails_even_when_environment_token_is_set(tmp_path: Path) -> None:
    settings = test_settings(tmp_path, meta_access_token="process-token", instagram_account_id="ig-process")
    gateway, seen = _recording_gateway(settings)
    await gateway.connect_account(
        "user-a",
        InstagramConnectRequest(instagram_account_id="ig-a", access_token="token-a"),
    )
    await gateway.disconnect_account("user-a")
    before = len(seen)
    image = write_jpeg(tmp_path / "photo.jpg")
    with pytest.raises(AppError) as exc:
        await gateway.enqueue_publication(
            InstagramPublicationRequest(
                user_id="user-a",
                image_path=str(image),
                allow_environment_fallback=True,
            )
        )
    assert exc.value.code == ErrorCode.INSTAGRAM_NOT_CONNECTED
    assert exc.value.http_status == 409
    assert len(seen) == before
    assert "token-a" not in exc.value.message
    assert "process-token" not in exc.value.message
    status = await gateway.account_status("user-a")
    assert status["connected"] is False
    assert status["environment_configured"] is False
    assert "process-token" not in str(status)


@pytest.mark.asyncio
async def test_user_cannot_publish_to_another_users_instagram_account(tmp_path: Path) -> None:
    settings = test_settings(tmp_path, meta_access_token="process-token", instagram_account_id="ig-process")
    gateway, seen = _recording_gateway(settings)
    await gateway.connect_account(
        "user-a",
        InstagramConnectRequest(instagram_account_id="ig-a", access_token="token-a"),
    )
    await gateway.connect_account(
        "user-b",
        InstagramConnectRequest(instagram_account_id="ig-b", access_token="token-b"),
    )
    before = len(seen)
    image = write_jpeg(tmp_path / "photo.jpg")
    with pytest.raises(AppError) as exc:
        await gateway.enqueue_publication(
            InstagramPublicationRequest(
                user_id="user-a",
                image_path=str(image),
                instagram_account_id="ig-b",
                allow_environment_fallback=True,
            )
        )
    assert exc.value.code == ErrorCode.PERMISSION_ERROR
    assert len(seen) == before
    assert await gateway.store.list_posts("user-b") == []
    assert "token-a" not in exc.value.message
    assert "token-b" not in exc.value.message
    assert "process-token" not in exc.value.message

    state = await gateway.enqueue_publication(
        InstagramPublicationRequest(user_id="user-a", image_path=str(image), generated_image_id="only-a")
    )
    assert state.instagram_account_ref == "ig-a"
    assert ("token-a", "ig-a") in seen
    assert await gateway.store.list_posts("user-b") == []
    assert ("token-b", "ig-b") in seen
    publish_calls = [pair for pair in seen[before:] if pair[0] == "token-a"]
    assert publish_calls
    assert all(pair[0] != "token-b" for pair in seen[before:])
    assert all(pair[0] != "process-token" for pair in seen)


@pytest.mark.asyncio
async def test_expired_token_fails_without_leaking_the_token(tmp_path: Path) -> None:
    settings = test_settings(tmp_path, meta_access_token="process-token", instagram_account_id="ig-process")
    gateway, seen = _recording_gateway(settings)
    secret = "expired-user-token"
    await gateway.store.upsert_account(
        InstagramAccountRecord(
            user_id="user-a",
            instagram_account_id="ig-a",
            access_token_encrypted=encrypt_token(settings, secret),
            token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            status=AccountStatus.CONNECTED.value,
        )
    )
    with pytest.raises(AppError) as exc:
        await gateway.resolve_credentials("user-a", allow_environment_fallback=True)
    assert exc.value.code == ErrorCode.AUTHENTICATION_ERROR
    assert "expired" in exc.value.message.lower()
    assert secret not in exc.value.message
    assert "process-token" not in exc.value.message
    assert seen == []
    status = await gateway.account_status("user-a")
    assert status["connected"] is False
    assert status["status"] == AccountStatus.EXPIRED.value
    assert secret not in str(status)


@pytest.mark.asyncio
async def test_missing_token_fails_without_environment_fallback(tmp_path: Path) -> None:
    settings = test_settings(tmp_path, meta_access_token="process-token", instagram_account_id="ig-process")
    gateway, seen = _recording_gateway(settings)
    await gateway.store.upsert_account(
        InstagramAccountRecord(
            user_id="user-a",
            instagram_account_id="ig-a",
            access_token_encrypted="",
            status=AccountStatus.CONNECTED.value,
        )
    )
    with pytest.raises(AppError) as exc:
        await gateway.resolve_credentials("user-a", allow_environment_fallback=True)
    assert exc.value.code == ErrorCode.AUTHENTICATION_ERROR
    assert "missing" in exc.value.message.lower()
    assert seen == []
    assert "process-token" not in exc.value.message


@pytest.mark.asyncio
async def test_production_ignores_legacy_environment_variables(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        app_env="production",
        instagram_legacy_env_fallback=True,
        meta_access_token="process-token",
        instagram_account_id="ig-process",
    )
    assert settings.legacy_environment_credentials_allowed() is False
    gateway, seen = _recording_gateway(settings)
    image = write_jpeg(tmp_path / "photo.jpg")
    with pytest.raises(AppError) as exc:
        await gateway.enqueue_publication(
            InstagramPublicationRequest(
                user_id="user-a",
                image_path=str(image),
                allow_environment_fallback=True,
            )
        )
    assert exc.value.code == ErrorCode.INSTAGRAM_NOT_CONNECTED
    assert seen == []
    assert "process-token" not in exc.value.message
    status = await gateway.account_status("user-a")
    assert status["environment_configured"] is False
    assert status["connected"] is False
    assert "process-token" not in str(status)


@pytest.mark.asyncio
async def test_staging_and_default_env_do_not_enable_legacy_fallback(tmp_path: Path) -> None:
    staging = test_settings(
        tmp_path / "staging",
        app_env="staging",
        instagram_legacy_env_fallback=True,
        meta_access_token="process-token",
        instagram_account_id="ig-process",
    )
    default = test_settings(
        tmp_path / "default",
        meta_access_token="process-token",
        instagram_account_id="ig-process",
    )
    assert staging.legacy_environment_credentials_allowed() is False
    assert default.app_env == "production"
    assert default.legacy_environment_credentials_allowed() is False


@pytest.mark.asyncio
async def test_development_legacy_fallback_is_explicit(tmp_path: Path) -> None:
    disabled = test_settings(
        tmp_path / "off",
        app_env="development",
        instagram_legacy_env_fallback=False,
        meta_access_token="env-token",
        instagram_account_id="ig-env",
        instagram_access_token="env-token",
        instagram_ig_user_id="ig-env",
    )
    gateway, seen = _recording_gateway(disabled)
    image = write_jpeg(tmp_path / "off.jpg")
    with pytest.raises(AppError) as exc:
        await gateway.enqueue_publication(
            InstagramPublicationRequest(
                user_id="dev-user",
                image_path=str(image),
                allow_environment_fallback=True,
            )
        )
    assert exc.value.code == ErrorCode.INSTAGRAM_NOT_CONNECTED
    assert seen == []

    enabled = test_settings(
        tmp_path / "on",
        app_env="development",
        instagram_legacy_env_fallback=True,
        meta_access_token="env-token",
        instagram_account_id="ig-env",
        instagram_access_token="env-token",
        instagram_ig_user_id="ig-env",
    )
    assert enabled.legacy_environment_credentials_allowed() is True
    dev_gateway, dev_seen = _recording_gateway(enabled)
    state = await dev_gateway.enqueue_publication(
        InstagramPublicationRequest(
            user_id="dev-user",
            image_path=str(write_jpeg(tmp_path / "on.jpg")),
            allow_environment_fallback=True,
        )
    )
    assert state.instagram_media_id == "media-1"
    assert ("env-token", "ig-env") in dev_seen
    payload = str(state.model_dump(mode="json"))
    assert "env-token" not in payload

    with pytest.raises(AppError) as foreign:
        await dev_gateway.resolve_credentials(
            "dev-user",
            allow_environment_fallback=True,
            instagram_account_id="ig-someone-else",
        )
    assert foreign.value.code == ErrorCode.PERMISSION_ERROR
    assert "env-token" not in foreign.value.message


@pytest.mark.asyncio
async def test_scheduler_fails_safely_when_no_account_is_connected(tmp_path: Path) -> None:
    settings = test_settings(
        tmp_path,
        app_env="production",
        instagram_legacy_env_fallback=True,
        meta_access_token="process-token",
        instagram_account_id="ig-process",
    )
    session = _session(settings)
    try:
        user, account, automation, image_path = _seed_user(session, settings, tmp_path)
        InstagramAccountRepository(session).disconnect(account)
        session.commit()
        clock = FrozenClock(datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc))
        publisher = FakePublisher(session)
        content = FakeContent(session, image_path=str(image_path))
        daily = DailyScheduler(settings, session, content, publisher, clock)
        result = await daily.run_user(user, automation, force=True)
        assert result["status"] == "failed"
        assert result["reason"] == "INSTAGRAM_NOT_CONNECTED"
        assert publisher.calls == 0

        festival = FestivalScheduler(settings, session, content, publisher, clock)
        festival_result = await festival.run_user(user, automation, force=True)
        assert festival_result
        assert all(item.get("status") != "PUBLISHED" for item in festival_result)
        assert any(item.get("reason") == "INSTAGRAM_NOT_CONNECTED" for item in festival_result)
        assert publisher.calls == 0
        assert "process-token" not in str(result)
        assert "process-token" not in str(festival_result)
    finally:
        session.close()
