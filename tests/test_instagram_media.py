import httpx
import pytest

from models.errors import AppError, ErrorCode
from tests.helpers import test_settings
from tools.instagram_media import InstagramMediaService


def _success_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path.endswith("/media"):
        return httpx.Response(200, json={"id": "container-1"})
    if request.method == "POST" and path.endswith("/media_publish"):
        return httpx.Response(200, json={"id": "media-1"})
    if request.method == "GET" and path.endswith("/container-1"):
        return httpx.Response(200, json={"status_code": "FINISHED"})
    if request.method == "GET" and path.endswith("/media-1"):
        return httpx.Response(200, json={"id": "media-1", "media_type": "IMAGE"})
    return httpx.Response(400, json={"error": {"message": "unexpected"}})


@pytest.mark.asyncio
async def test_create_and_publish_with_mocked_graph_api(tmp_path) -> None:
    settings = test_settings(tmp_path)
    settings.meta_graph_api_base_url = "https://graph.facebook.com"
    settings.meta_api_version = "v21.0"
    client = httpx.AsyncClient(transport=httpx.MockTransport(_success_handler), timeout=5.0)
    service = InstagramMediaService(settings, client=client)
    created = await service.create_image_container("https://cdn.example.test/ig/photo.jpg")
    published = await service.publish_container(created["instagram_container_id"])
    assert created["instagram_container_id"] == "container-1"
    assert published["instagram_media_id"] == "media-1"
    await service.aclose()


@pytest.mark.asyncio
async def test_not_configured_fails_without_http(tmp_path) -> None:
    settings = test_settings(tmp_path, meta_access_token="", instagram_account_id="", instagram_access_token=None, instagram_ig_user_id=None)
    service = InstagramMediaService(settings)
    with pytest.raises(AppError) as exc:
        await service.create_image_container("https://cdn.example.test/ig/photo.jpg")
    assert exc.value.code in {ErrorCode.CONFIGURATION_ERROR, ErrorCode.INSTAGRAM_NOT_CONFIGURED}
    assert "TOKEN" not in exc.value.message or "META_ACCESS_TOKEN" in exc.value.message


@pytest.mark.asyncio
async def test_authentication_failure_is_normalized(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token signature.", "code": 190}})

    settings = test_settings(tmp_path)
    service = InstagramMediaService(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
    )
    with pytest.raises(AppError) as exc:
        await service.create_image_container("https://cdn.example.test/ig/photo.jpg")
    assert exc.value.code == ErrorCode.AUTHENTICATION_ERROR
    assert "token" not in exc.value.message.lower() or "authentication" in exc.value.message.lower()


@pytest.mark.asyncio
async def test_rate_limit_and_timeout_errors(tmp_path) -> None:
    def rate_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, json={"error": {"code": 4, "message": "rate"}}, headers={"Retry-After": "2"})

    settings = test_settings(tmp_path, graph_retry_attempts=1)
    service = InstagramMediaService(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(rate_handler), timeout=5.0),
    )
    with pytest.raises(AppError) as exc:
        await service.create_image_container("https://cdn.example.test/ig/photo.jpg")
    assert exc.value.code == ErrorCode.RATE_LIMITED


@pytest.mark.asyncio
async def test_permission_failure(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(403, json={"error": {"message": "Permission denied", "code": 10}})

    settings = test_settings(tmp_path, graph_retry_attempts=1)
    service = InstagramMediaService(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
    )
    with pytest.raises(AppError) as exc:
        await service.create_image_container("https://cdn.example.test/ig/photo.jpg")
    assert exc.value.code == ErrorCode.PERMISSION_ERROR


@pytest.mark.asyncio
async def test_invalid_image_url_rejected_locally(tmp_path) -> None:
    settings = test_settings(tmp_path)
    service = InstagramMediaService(settings)
    with pytest.raises(AppError) as exc:
        await service.create_image_container("http://127.0.0.1:8000/photo.jpg")
    assert exc.value.code == ErrorCode.INVALID_IMAGE_URL


@pytest.mark.asyncio
async def test_timeout(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise httpx.ReadTimeout("timed out")

    settings = test_settings(tmp_path, graph_retry_attempts=1)
    service = InstagramMediaService(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
    )
    with pytest.raises(AppError) as exc:
        await service.create_image_container("https://cdn.example.test/ig/photo.jpg")
    assert exc.value.code == ErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_server_error(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500, json={"error": {"message": "unknown", "code": 1}})

    settings = test_settings(tmp_path, graph_retry_attempts=1)
    service = InstagramMediaService(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0),
    )
    with pytest.raises(AppError) as exc:
        await service.create_image_container("https://cdn.example.test/ig/photo.jpg")
    assert exc.value.code == ErrorCode.API_ERROR

