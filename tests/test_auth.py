"""Authentication, authorization, isolation, and token lifecycle tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from auth.tokens import create_access_token
from config import Settings
from db.repositories import (
    BusinessRepository,
    FestivalRepository,
    GeneratedImageRepository,
    PostRepository,
    TaskRepository,
    UserRepository,
)
from tests.helpers import DummyInstagramClient, auth_client_headers, write_jpeg


FORBIDDEN_KEYS = {
    "password",
    "password_hash",
    "openai_api_key",
    "OPENAI_API_KEY",
    "meta_access_token",
    "META_ACCESS_TOKEN",
    "access_token_encrypted",
}


def _app(settings: Settings):
    return create_app(settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]


def _assert_safe(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in FORBIDDEN_KEYS
            _assert_safe(value)
        return
    if isinstance(payload, list):
        for item in payload:
            _assert_safe(item)


def test_registration(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": "password12"},
        )
    assert response.status_code == 200
    body = response.json()
    user = body["user"]
    assert user["email"] == "owner@example.com"
    assert user["is_admin"] is False
    assert user["must_change_password"] is False
    assert "password" not in user
    assert "password_hash" not in user
    _assert_safe(body)


def test_login(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "password12"})
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "password12"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert "access_token" in response.cookies
    _assert_safe(body)


def test_invalid_credentials(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "password12"})
        missing = client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": "password12"})
        wrong = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong-pass"})
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["error"]["message"] == wrong.json()["error"]["message"]
    assert "password" not in missing.json()["error"]["message"].lower() or "Invalid" in missing.json()["error"]["message"]
    _assert_safe(missing.json())


def test_protected_route_requires_auth(tmp_settings: Settings, tmp_path: Path) -> None:
    app = _app(tmp_settings)
    image_path = write_jpeg(tmp_path / "photo.jpg")
    with TestClient(app) as client:
        with image_path.open("rb") as handle:
            anonymous = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
            )
        assert anonymous.status_code == 401
        headers = auth_client_headers(client)
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        with image_path.open("rb") as handle:
            published = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=headers,
            )
    assert published.status_code == 200
    assert published.json()["success"] is True


def test_logout(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
        logged_out = client.post("/api/v1/auth/logout", headers=headers)
        assert logged_out.status_code == 200
        assert logged_out.json()["success"] is True
        after = client.get("/api/v1/auth/me", headers=headers)
    assert after.status_code == 401


def test_password_change(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        headers = auth_client_headers(client, email="owner@example.com", password="password12")
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "password12", "new_password": "newpass99"},
            headers=headers,
        )
        assert changed.status_code == 200
        assert changed.json()["user"]["must_change_password"] is False
        old_login = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "password12"})
        new_login = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "newpass99"})
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    _assert_safe(changed.json())


def test_forced_password_change(tmp_settings: Settings, tmp_path: Path) -> None:
    settings = tmp_settings.model_copy(
        update={
            "default_admin_email": "admin@yottolabs.com",
            "default_admin_password": "12345",
        }
    )
    app = _app(settings)
    image_path = write_jpeg(tmp_path / "photo.jpg")
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": settings.default_admin_email, "password": settings.default_admin_password},
        )
        assert login.status_code == 200
        user = login.json()["user"]
        assert user["is_admin"] is True
        assert user["must_change_password"] is True
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["user"]["must_change_password"] is True
        with image_path.open("rb") as handle:
            blocked = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=headers,
            )
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "MUST_CHANGE_PASSWORD"
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": settings.default_admin_password, "new_password": "changed99"},
            headers=headers,
        )
        assert changed.status_code == 200
        assert changed.json()["user"]["must_change_password"] is False
        fresh = {"Authorization": f"Bearer {changed.json()['access_token']}"}
        with image_path.open("rb") as handle:
            allowed = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=fresh,
            )
    assert allowed.status_code == 200


def test_admin_authorization(tmp_settings: Settings) -> None:
    settings = tmp_settings.model_copy(
        update={
            "default_admin_email": "admin@yottolabs.com",
            "default_admin_password": "12345",
        }
    )
    app = _app(settings)
    with TestClient(app) as client:
        member = auth_client_headers(client, email="member@example.com", password="password12")
        forbidden = client.get("/api/v1/admin/users", headers=member)
        assert forbidden.status_code == 403
        login = client.post(
            "/api/v1/auth/login",
            json={"email": settings.default_admin_email, "password": settings.default_admin_password},
        )
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        client.post(
            "/api/v1/auth/change-password",
            json={"current_password": settings.default_admin_password, "new_password": "changed99"},
            headers=admin_headers,
        )
        changed = client.post(
            "/api/v1/auth/login",
            json={"email": settings.default_admin_email, "password": "changed99"},
        )
        ready = {"Authorization": f"Bearer {changed.json()['access_token']}"}
        allowed = client.get("/api/v1/admin/users", headers=ready)
    assert allowed.status_code == 200
    emails = {item["email"] for item in allowed.json()["users"]}
    assert settings.default_admin_email in emails
    assert "member@example.com" in emails
    _assert_safe(allowed.json())


def test_user_isolation(tmp_settings: Settings, tmp_path: Path) -> None:
    app = _app(tmp_settings)
    image_path = write_jpeg(tmp_path / "photo.jpg")
    with TestClient(app) as client:
        headers_a = auth_client_headers(client, email="a@example.com", password="password12")
        with image_path.open("rb") as handle:
            published = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=headers_a,
            )
        task_id = published.json()["task_id"]
        headers_b = auth_client_headers(client, email="b@example.com", password="password12")
        stolen_task = client.get(f"/api/v1/tasks/{task_id}", headers=headers_b)
        stolen_events = client.get(f"/api/v1/tasks/{task_id}/events", headers=headers_b)
        own_task = client.get(f"/api/v1/tasks/{task_id}", headers=headers_a)
    assert stolen_task.status_code == 404
    assert stolen_events.status_code == 404
    assert own_task.status_code == 200

    factory = app.state.session_factory
    session = factory()
    try:
        users = UserRepository(session)
        user_a = users.get_by_email("a@example.com")
        user_b = users.get_by_email("b@example.com")
        assert user_a is not None and user_b is not None
        BusinessRepository(session).upsert(user_a.id, business_name="Shop A")
        image = GeneratedImageRepository(session).create(
            user_id=user_a.id,
            original_prompt="red shoes for the window display",
            enhanced_prompt="red shoes for the window display",
            filename="a.png",
            storage_path="storage/a.png",
        )
        post = PostRepository(session).create(user_id=user_a.id, status="GENERATED", post_type="USER_PROMPT")
        task = TaskRepository(session).create(id=str(uuid4()), user_id=user_a.id, status="completed")
        campaign = FestivalRepository(session).get_or_create_campaign(
            user_a.id,
            festival_name="Diwali",
            festival_date=image.created_at.date(),
            year=image.created_at.year,
        )
        session.commit()
        assert BusinessRepository(session).get_for_user(user_b.id) is None
        assert GeneratedImageRepository(session).get_owned(user_b.id, image.id) is None
        assert PostRepository(session).get_owned(user_b.id, post.id) is None
        assert TaskRepository(session).get_owned(user_b.id, task.id) is None
        assert FestivalRepository(session).get_owned(user_b.id, campaign.id) is None
        assert GeneratedImageRepository(session).get_owned(user_a.id, image.id) is not None
        assert PostRepository(session).get_owned(user_a.id, post.id) is not None
        assert TaskRepository(session).get_owned(user_a.id, task.id) is not None
        assert FestivalRepository(session).get_owned(user_a.id, campaign.id) is not None
    finally:
        session.close()


def test_token_expiration(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        user_id = client.get("/api/v1/auth/me", headers=headers).json()["user"]["id"]
        expired = create_access_token(
            tmp_settings,
            user_id=user_id,
            email="user@example.com",
            expires_delta=timedelta(seconds=-5),
        )
        response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"].lower()


def test_inactive_user(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        headers = auth_client_headers(client, email="sleep@example.com", password="password12")
        session = app.state.session_factory()
        try:
            user = UserRepository(session).get_by_email("sleep@example.com")
            assert user is not None
            user.is_active = False
            session.commit()
        finally:
            session.close()
        me = client.get("/api/v1/auth/me", headers=headers)
        login = client.post("/api/v1/auth/login", json={"email": "sleep@example.com", "password": "password12"})
    assert me.status_code == 401
    assert login.status_code == 401


def test_health_and_media_remain_public(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        missing_media = client.get("/api/v1/media/does-not-exist.jpg")
    assert health.status_code == 200
    assert missing_media.status_code == 404


def test_duplicate_registration_is_conflict(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        first = client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "password12"})
        second = client.post("/api/v1/auth/register", json={"email": "owner@example.com", "password": "password12"})
    assert first.status_code == 200
    assert second.status_code == 409
