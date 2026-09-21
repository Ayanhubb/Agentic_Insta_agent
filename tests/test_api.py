from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from config import Settings
from tests.helpers import DummyInstagramClient, auth_client_headers, write_jpeg


def test_publish_endpoint_success(tmp_settings: Settings, tmp_path: Path) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
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
    body = response.json()
    assert body["success"] is True
    assert body["platform"] == "instagram"
    assert body["status"] == "completed"
    assert body["instagram_media_id"] == "media-1"
    assert body["message"] == "Image published successfully"
    assert body["task_id"]
    assert [step["tool"] for step in body["steps"]] == [
        "validate_image",
        "prepare_image",
        "upload_image",
        "create_instagram_media",
        "publish_instagram_media",
        "verify_publication",
    ]


def test_publish_endpoint_rejects_invalid_image(tmp_settings: Settings, tmp_path: Path) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    bad_file = tmp_path / "notes.txt"
    bad_file.write_text("nope", encoding="utf-8")
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        with bad_file.open("rb") as handle:
            response = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("notes.txt", handle, "text/plain")},
                headers=headers,
            )
    assert response.status_code in {400, 415}
    body = response.json()
    assert body["success"] is False
    assert body["status"] == "failed"
    assert body["error"]["code"]
    assert "TOKEN" not in body["error"]["message"]
    assert "traceback" not in body["error"]["message"].lower()


def test_publish_without_file_returns_validation_error(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        response = client.post("/api/v1/instagram/publish?wait=true", headers=headers)
    assert response.status_code in {400, 422}


def test_task_status_endpoint(tmp_settings: Settings, tmp_path: Path) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    image_path = write_jpeg(tmp_path / "photo.jpg")
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        with image_path.open("rb") as handle:
            started = client.post(
                "/api/v1/instagram/publish?wait=true",
                files={"image": ("photo.jpg", handle, "image/jpeg")},
                headers=headers,
            )
        task_id = started.json()["task_id"]
        status = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert status.status_code == 200
    body = status.json()
    assert body["task_id"] == task_id
    assert body["status"] == "completed"
    assert body["current_step"] == "completed"
    assert body["instagram_media_id"] == "media-1"
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
