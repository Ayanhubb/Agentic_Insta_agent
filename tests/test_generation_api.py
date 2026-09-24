"""Owner-only generation APIs and controlled media endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.security import CurrentUser, get_current_user
from config import Settings
from tests.helpers import DummyInstagramClient, auth_client_headers, connect_instagram, write_jpeg, write_png

_PROFILE = {
    "business_name": "Pal Jewels",
    "business_type": "retail",
    "products": "kundan necklace, gold jhumkas",
    "location": "Kolkata",
}


def test_generation_requires_authentication(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.get("/api/v1/generation")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_ERROR"


def test_owner_can_list_and_fetch_generation_without_filesystem_paths(
    tmp_settings: Settings, tmp_path: Path
) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        profile = client.put("/api/v1/business", headers=headers, json=_PROFILE)
        assert profile.status_code == 200
        created = client.post(
            "/api/v1/generation",
            headers=headers,
            json={"prompt": "Create a jewellery tray photo with warm Kolkata lighting for the boutique."},
        )
        assert created.status_code == 200
        image = created.json().get("image") or created.json().get("generated_image") or {}
        image_id = image.get("id")
        assert image_id
        listed = client.get("/api/v1/generation", headers=headers)
        detail = client.get(f"/api/v1/generation/{image_id}", headers=headers)
        media = client.get(f"/api/v1/media/generated/{image_id}", headers=headers)

    assert listed.status_code == 200
    body = listed.json()
    assert body["images"][0]["id"] == image_id
    assert "storage_path" not in body["images"][0]
    assert str(tmp_settings.media_root) not in str(body)
    assert str(tmp_path) not in str(body)
    assert detail.status_code == 200
    detail_body = detail.json().get("image") or detail.json()
    assert detail_body["preview_url"] == f"/api/v1/media/generated/{image_id}"
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("image/")

    payload = write_jpeg(tmp_path / "photo.jpg", size=(1080, 1080)).read_bytes()
    record = app.state.media_service.ingest_generated("user-a", payload, claimed_mime="image/jpeg")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-a")
    with TestClient(app) as media_client:
        ingested = media_client.get(f"/api/v1/generation/{record.id}/media")
    assert ingested.status_code == 200
    assert ingested.headers["content-type"].startswith("image/")


def test_user_cannot_access_another_users_generation(
    tmp_settings: Settings, tmp_path: Path
) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    with TestClient(app) as client:
        headers_a = auth_client_headers(client, email="owner-a@example.com")
        assert client.put("/api/v1/business", headers=headers_a, json=_PROFILE).status_code == 200
        created = client.post(
            "/api/v1/generation",
            headers=headers_a,
            json={"prompt": "Create a jewellery tray photo with warm Kolkata lighting for the boutique."},
        )
        assert created.status_code == 200
        image_id = (created.json().get("image") or {}).get("id")
        assert image_id
        headers_b = auth_client_headers(client, email="owner-b@example.com")
        listed = client.get("/api/v1/generation", headers=headers_b)
        detail = client.get(f"/api/v1/generation/{image_id}", headers=headers_b)
        media = client.get(f"/api/v1/media/generated/{image_id}", headers=headers_b)
    assert listed.status_code == 200
    assert listed.json().get("images") == []
    assert detail.status_code == 404
    assert media.status_code == 404

    payload = write_png(tmp_path / "photo.png").read_bytes()
    record = app.state.media_service.ingest_generated("user-a", payload, claimed_mime="image/png")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="user-b")
    with TestClient(app) as stranger:
        owned_media = stranger.get(f"/api/v1/generation/{record.id}/media")
        traversal = stranger.get("/api/v1/generation/../user-a")
        stage = stranger.get(
            f"/api/v1/generation/{record.id}/media",
            params={"stage": "../../secret"},
        )
    assert owned_media.status_code == 404
    assert traversal.status_code in {400, 404, 422}
    assert stage.status_code == 400
    payload_text = str(owned_media.json())
    assert "Generated image was not found" in payload_text or "storage_path" not in payload_text


def test_v1_publish_endpoint_still_accepts_upload(tmp_settings: Settings, tmp_path: Path) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    image_path = write_jpeg(tmp_path / "photo.jpg")
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        connect_instagram(client, headers)
        with image_path.open("rb") as handle:
            response = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=headers,
            )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["instagram_media_id"] == "media-1"
