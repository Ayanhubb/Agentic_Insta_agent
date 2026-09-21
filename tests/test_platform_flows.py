"""Platform flows: auth, isolation, daily/festival automation, ambiguous counting."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from services.clock import FrozenClock
from tests.helpers import DummyInstagramClient, auth_client_headers, write_jpeg


def _app(tmp_settings: Settings, clock: FrozenClock | None = None):
    return create_app(
        tmp_settings,
        instagram_client=DummyInstagramClient(),  # type: ignore[arg-type]
        clock=clock or FrozenClock(datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))),
    )


def _auth(client: TestClient, email: str, password: str = "password12") -> dict:
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _prepare_publisher(client: TestClient, headers: dict) -> None:
    profile = client.put(
        "/api/v1/business",
        headers=headers,
        json={
            "business_name": "Pal Jewels",
            "business_type": "retail",
            "products": "kundan necklace, gold jhumkas",
            "location": "Kolkata",
            "brand_style": "heritage luxury",
        },
    )
    assert profile.status_code == 200
    connected = client.post(
        "/api/v1/instagram/connect",
        headers=headers,
        json={"instagram_account_id": "ig-user-1", "access_token": "user-token"},
    )
    assert connected.status_code == 200


def test_register_login_and_me(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": "password12"},
        )
        assert registered.status_code == 200
        assert registered.json()["user"]["email"] == "owner@example.com"
        assert "password" not in str(registered.json()).lower() or "password_hash" not in str(registered.json())
        bad = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrongpass"})
        assert bad.status_code == 401
        headers = _auth(client, "owner2@example.com")
        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["user"]["email"] == "owner2@example.com"


def test_protected_route_requires_auth(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/dashboard")
    assert response.status_code == 401


def test_user_isolation_across_resources(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        headers_a = _auth(client, "a@example.com")
        headers_b = _auth(client, "b@example.com")
        client.put(
            "/api/v1/business",
            headers=headers_a,
            json={"business_name": "Studio A", "products": "tea"},
        )
        _prepare_publisher(client, headers_a)
        generated = client.post(
            "/api/v1/generation",
            headers=headers_a,
            json={"prompt": "Show our evening tea tray in warm light for Instagram."},
        )
        assert generated.status_code == 200
        image_id = (generated.json().get("image") or generated.json().get("generated_image") or {}).get("id")
        client.put(
            "/api/v1/automation",
            headers=headers_a,
            json={"daily_enabled": True, "festival_enabled": True, "timezone": "Asia/Kolkata"},
        )
        campaigns_a = client.get("/api/v1/festivals/campaigns", headers=headers_a)
        assert campaigns_a.status_code == 200
        a_campaign_ids = {item["id"] for item in campaigns_a.json().get("campaigns") or []}

        other_business = client.get("/api/v1/business", headers=headers_b)
        assert (other_business.json().get("profile") or {}).get("business_name") != "Studio A"
        if image_id:
            stolen = client.get(f"/api/v1/generation/{image_id}", headers=headers_b)
            assert stolen.status_code in {401, 403, 404}
            stolen_media = client.get(f"/api/v1/media/generated/{image_id}", headers=headers_b)
            assert stolen_media.status_code in {401, 403, 404}
        images_b = client.get("/api/v1/generation", headers=headers_b)
        assert images_b.status_code == 200
        assert (images_b.json().get("images") or images_b.json().get("generated_images") or []) == []
        posts_b = client.get("/api/v1/posts", headers=headers_b)
        assert posts_b.status_code == 200
        assert (posts_b.json().get("posts") or []) == []
        auto_b = client.get("/api/v1/automation", headers=headers_b)
        assert auto_b.status_code == 200
        assert auto_b.json().get("daily_enabled") is not True
        ig_b = client.get("/api/v1/instagram/status", headers=headers_b)
        assert ig_b.status_code == 200
        assert ig_b.json().get("connected") is not True
        campaigns_b = client.get("/api/v1/festivals/campaigns", headers=headers_b)
        assert campaigns_b.status_code == 200
        b_ids = {item["id"] for item in campaigns_b.json().get("campaigns") or []}
        assert a_campaign_ids.isdisjoint(b_ids)
        dash_b = client.get("/api/v1/dashboard", headers=headers_b)
        assert dash_b.status_code == 200
        assert dash_b.json()["generated_images"] == 0
        assert dash_b.json()["published_posts"] == 0


def test_manual_generate_does_not_publish_until_approve(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        headers = _auth(client, "manual@example.com")
        _prepare_publisher(client, headers)
        generated = client.post(
            "/api/v1/generation",
            headers=headers,
            json={"prompt": "Create a jewellery tray photo with warm Kolkata lighting for the boutique."},
        )
        assert generated.status_code == 200
        body = generated.json()
        assert body["published"] is False
        image_id = (body.get("image") or body.get("generated_image") or {}).get("id")
        assert image_id
        approved = client.post(f"/api/v1/generation/{image_id}/approve", headers=headers)
        assert approved.status_code == 200
        post = approved.json()["post"]
        assert post["status"] == "PUBLISHED"
        dash = client.get("/api/v1/dashboard", headers=headers)
        assert dash.json()["published_posts"] == 1


def test_daily_scheduler_twice_publishes_once(tmp_settings: Settings) -> None:
    app = _app(tmp_settings)
    with TestClient(app) as client:
        headers = _auth(client, "daily@example.com")
        _prepare_publisher(client, headers)
        updated = client.put(
            "/api/v1/automation",
            headers=headers,
            json={"daily_enabled": True, "auto_daily_publish": True, "timezone": "Asia/Kolkata"},
        )
        assert updated.status_code == 200
        first = client.post("/api/v1/automation/run-now", headers=headers)
        second = client.post("/api/v1/automation/run-now", headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        dash = client.get("/api/v1/dashboard", headers=headers)
        assert dash.json()["todays_posts"] == 1
        assert dash.json()["published_posts"] == 1


def test_festival_two_required_posts_and_partial_failure(tmp_settings: Settings) -> None:
    clock = FrozenClock(datetime(2026, 11, 7, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
    app = _app(tmp_settings, clock=clock)
    with TestClient(app) as client:
        headers = _auth(client, "fest@example.com")
        _prepare_publisher(client, headers)
        client.put(
            "/api/v1/automation",
            headers=headers,
            json={
                "festival_enabled": True,
                "auto_festival_publish": True,
                "festival_posts_per_festival": 2,
                "timezone": "Asia/Kolkata",
            },
        )
        first = client.post("/api/v1/automation/run-now", headers=headers)
        assert first.status_code == 200
        clock.set(datetime(2026, 11, 8, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
        second = client.post("/api/v1/automation/run-now", headers=headers)
        assert second.status_code == 200
        campaigns = client.get("/api/v1/festivals/campaigns", headers=headers)
        diwali = next(item for item in campaigns.json()["campaigns"] if "Diwali" in item["festival_name"] and "Govardhan" not in item["festival_name"])
        assert diwali["required_posts"] == 2
        assert diwali["published_posts"] == 2
        assert diwali["remaining_posts"] == 0


def test_v1_publish_still_works(tmp_settings: Settings, tmp_path) -> None:
    app = _app(tmp_settings)
    image_path = write_jpeg(tmp_path / "photo.jpg")
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        with image_path.open("rb") as handle:
            response = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=headers,
            )
    assert response.status_code == 200
    assert response.json()["instagram_media_id"] == "media-1"
