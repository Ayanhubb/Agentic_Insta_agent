"""Mocked OpenAI image provider tests. Default pytest never calls the real API."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, APITimeoutError, AuthenticationError
from PIL import Image

from agent.planner import ALLOWED_TOOLS
from backend.ai.image.base import CONTENT_AGENT_CALLER, ControlledImageStore, ImageInput, ImageRequest
from backend.ai.image.factory import get_openai_image_provider
from backend.ai.image.openai import OpenAIImageProvider
from models.errors import AppError, ErrorCode
from tests.helpers import no_sleep, test_settings

PROMPT = "A boutique window display of silk sarees with warm diya lighting for Instagram."


@pytest.fixture(autouse=True)
def _clear_image_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_IMAGE_MODEL",
        "OPENAI_IMAGE_SIZE",
        "OPENAI_IMAGE_QUALITY",
        "OPENAI_IMAGE_OUTPUT_FORMAT",
    ):
        monkeypatch.delenv(name, raising=False)


def _png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (180, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (48, 48), (20, 40, 80)).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _settings(tmp_path: Path, **overrides):
    values = dict(
        openai_api_key="sk-test-not-a-real-openai-key-xxxxx",
        image_provider="openai",
        image_model="configured-image-model",
        image_size="",
        image_max_attempts=2,
        openai_retry_delay_seconds=0.0,
    )
    values.update(overrides)
    return test_settings(tmp_path, **values)


class FakeImages:
    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.generate_calls: list[dict] = []
        self.edit_calls: list[dict] = []

    async def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return self._next()

    async def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        return self._next()

    def _next(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeImageClient:
    def __init__(self, script: list[object]) -> None:
        self.images = FakeImages(script)


def _response(
    image_bytes: bytes | None = None,
    *,
    request_id: str = "req_test_123",
    size: str = "1024x1024",
    quality: str = "high",
    output_format: str = "png",
    background: str | None = None,
) -> SimpleNamespace:
    payload = _png_bytes() if image_bytes is None else image_bytes
    return SimpleNamespace(
        created=1_700_000_000,
        data=[
            SimpleNamespace(
                b64_json=base64.b64encode(payload).decode("ascii"),
                url=None,
                revised_prompt="Enhanced boutique display.",
            )
        ],
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=80,
            total_tokens=92,
            input_tokens_details=SimpleNamespace(text_tokens=12, image_tokens=0),
        ),
        request_id=request_id,
        size=size,
        quality=quality,
        output_format=output_format,
        background=background,
    )


def _request(**overrides) -> ImageRequest:
    values = dict(prompt=PROMPT, user_id="user-1")
    values.update(overrides)
    return ImageRequest(**values)


def _provider(tmp_path: Path, script: list[object], **settings_overrides) -> tuple[OpenAIImageProvider, FakeImageClient]:
    client = FakeImageClient(script)
    provider = OpenAIImageProvider(
        _settings(tmp_path, **settings_overrides),
        client=client,
        sleeper=no_sleep,
    )
    return provider, client


def _status_error(status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    response = httpx.Response(status_code, request=request)
    if status_code == 401:
        return AuthenticationError("rejected", response=response, body=None)
    return APIStatusError("down", response=response, body=None)


@pytest.mark.asyncio
async def test_mocked_generation_returns_metadata_and_default_square_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
    provider, client = _provider(tmp_path, [_response()])
    result = await provider.generate(_request(quality="high"), caller=CONTENT_AGENT_CALLER)

    assert result.provider == "openai"
    assert result.model == "gpt-image-2.5-sunburst"
    assert result.size == "1024x1024"
    assert result.quality == "high"
    assert result.output_format == "png"
    assert result.request_id == "req_test_123"
    assert result.usage is not None
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 80
    assert result.usage.total_tokens == 92
    assert result.usage.raw["input_tokens_details"]["text_tokens"] == 12
    assert result.image_bytes.startswith(b"\x89PNG")
    assert "price" not in result.model_dump()
    assert "cost" not in result.model_dump()
    assert "openai_api_key" not in result.public_dict()
    assert "storage_path" not in result.public_dict()
    call = client.images.generate_calls[0]
    assert call["model"] == "gpt-image-2.5-sunburst"
    assert call["size"] == "1024x1024"
    assert call["quality"] == "high"
    assert call["output_format"] == "png"
    assert "response_format" not in call
    assert client.images.edit_calls == []


@pytest.mark.asyncio
async def test_mocked_editing(tmp_path: Path) -> None:
    provider, client = _provider(
        tmp_path,
        [_response(_jpeg_bytes(), output_format="jpeg", size="1536x1024", quality="medium")],
        openai_image_quality="medium",
    )
    result = await provider.edit(
        _request(source_image=ImageInput(data=_png_bytes(), filename="source.png"), size="1536x1024", output_format="jpeg"),
        caller=CONTENT_AGENT_CALLER,
    )

    assert result.output_format == "jpeg"
    assert result.mime_type == "image/jpeg"
    assert result.size == "1536x1024"
    assert result.quality == "medium"
    assert result.filename.endswith(".jpg")
    assert client.images.generate_calls == []
    call = client.images.edit_calls[0]
    assert call["size"] == "1536x1024"
    assert call["quality"] == "medium"
    assert call["output_format"] == "jpeg"
    assert call["image"][0][0] == "source.png"
    assert "input_fidelity" not in call


@pytest.mark.asyncio
async def test_reference_logo_and_product_images(tmp_path: Path) -> None:
    provider, client = _provider(tmp_path, [_response()])
    request = _request(
        company_logo=ImageInput(data=_png_bytes(), mime_type="image/png", filename="logo.png"),
        product_image=ImageInput(data=_jpeg_bytes(), mime_type="image/jpeg", filename="product.jpg"),
        reference_images=[ImageInput(data=_png_bytes(), filename="mood.png")],
    )
    result = await provider.generate(request, caller=CONTENT_AGENT_CALLER)

    assert result.provider == "openai"
    assert client.images.generate_calls == []
    call = client.images.edit_calls[0]
    names = [item[0] for item in call["image"]]
    assert names == ["logo.png", "product.jpg", "mood.png"]
    assert call["input_fidelity"] == "high"
    assert "company logo" in call["prompt"]
    assert "product photograph" in call["prompt"]
    assert "additional visual reference" in call["prompt"]
    assert Path(result.storage_path).is_file()


@pytest.mark.asyncio
async def test_generation_failure_retries_then_raises(tmp_path: Path) -> None:
    provider, client = _provider(tmp_path, [_status_error(500), _status_error(500)])
    with pytest.raises(AppError) as exc_info:
        await provider.generate(_request(), caller=CONTENT_AGENT_CALLER)
    assert exc_info.value.code == ErrorCode.OPENAI_API_ERROR
    assert len(client.images.generate_calls) == 2
    assert "sk-test" not in exc_info.value.message


@pytest.mark.asyncio
async def test_timeout_is_retried(tmp_path: Path) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/images/generations")
    provider, client = _provider(tmp_path, [APITimeoutError(request), APITimeoutError(request)])
    with pytest.raises(AppError) as exc_info:
        await provider.generate(_request(), caller=CONTENT_AGENT_CALLER)
    assert exc_info.value.code == ErrorCode.TIMEOUT
    assert len(client.images.generate_calls) == 2


@pytest.mark.asyncio
async def test_invalid_key(tmp_path: Path) -> None:
    missing, missing_client = _provider(tmp_path, [_response()], openai_api_key="")
    with pytest.raises(AppError) as missing_exc:
        await missing.generate(_request(), caller=CONTENT_AGENT_CALLER)
    assert missing_exc.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR
    assert missing_client.images.generate_calls == []

    rejected, rejected_client = _provider(tmp_path, [_status_error(401)])
    with pytest.raises(AppError) as rejected_exc:
        await rejected.generate(_request(), caller=CONTENT_AGENT_CALLER)
    assert rejected_exc.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR
    assert len(rejected_client.images.generate_calls) == 1
    assert "sk-test" not in rejected_exc.value.message


@pytest.mark.asyncio
async def test_unsupported_image_is_rejected_before_the_api(tmp_path: Path) -> None:
    provider, client = _provider(tmp_path, [_response()])
    request = _request(source_image=ImageInput(data=b"GIF89a\x01\x00\x01\x00", mime_type="image/gif"))
    with pytest.raises(AppError) as exc_info:
        await provider.edit(request, caller=CONTENT_AGENT_CALLER)
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_FORMAT
    assert client.images.edit_calls == []

    corrupt = _request(product_image=ImageInput(data=b"not-an-image", mime_type="image/png"))
    with pytest.raises(AppError) as corrupt_exc:
        await provider.edit(corrupt, caller=CONTENT_AGENT_CALLER)
    assert corrupt_exc.value.code == ErrorCode.UNSUPPORTED_FORMAT
    assert client.images.edit_calls == []


@pytest.mark.asyncio
async def test_output_storage_is_owner_scoped(tmp_path: Path) -> None:
    provider, _client = _provider(tmp_path, [_response()])
    result = await provider.generate(_request(), caller=CONTENT_AGENT_CALLER)
    path = Path(result.storage_path).resolve()
    root = (_settings(tmp_path).media_root / "generated").resolve()
    assert path.is_file()
    assert path.read_bytes() == result.image_bytes
    assert path.is_relative_to(root)
    assert result.filename.startswith("img_")
    assert "user-1" in path.as_posix()

    store = ControlledImageStore(_settings(tmp_path))
    stored = store.save(user_id="../../etc/passwd", image_bytes=_png_bytes(), mime_type="image/png")
    stored_path = Path(stored.storage_path).resolve()
    assert stored_path.is_relative_to(root)
    assert ".." not in stored_path.parts
    assert "passwd" not in stored.filename


@pytest.mark.asyncio
async def test_transparent_background_and_content_agent_gate(tmp_path: Path) -> None:
    provider, client = _provider(tmp_path, [_response(background="transparent")])
    result = await provider.generate(
        _request(transparent_background=True),
        caller=CONTENT_AGENT_CALLER,
    )
    assert result.background == "transparent"
    assert client.images.generate_calls[0]["background"] == "transparent"
    assert client.images.generate_calls[0]["output_format"] == "png"

    with pytest.raises(AppError) as opaque_jpeg:
        await provider.generate(
            _request(output_format="jpeg", transparent_background=True),
            caller=CONTENT_AGENT_CALLER,
        )
    assert opaque_jpeg.value.code == ErrorCode.UNSUPPORTED_FORMAT
    assert len(client.images.generate_calls) == 1

    with pytest.raises(AppError) as denied:
        await provider.generate(_request(), caller="llm")
    assert denied.value.code == ErrorCode.TOOL_NOT_ALLOWED
    assert len(client.images.generate_calls) == 1


def test_provider_cannot_publish_or_be_selected_by_the_llm(tmp_path: Path) -> None:
    provider = get_openai_image_provider(_settings(tmp_path))
    assert isinstance(provider, OpenAIImageProvider)
    assert provider._client is None
    for name in ("publish", "publish_instagram_media", "create_instagram_media", "media_publish"):
        assert not hasattr(provider, name)
    assert "openai_image" not in ALLOWED_TOOLS
    assert "generate_image" not in ALLOWED_TOOLS

    source_root = Path(__file__).resolve().parents[1] / "backend" / "ai" / "image"
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py")).lower()
    for token in (
        "instagramgraphclient",
        "media_publish",
        "graph.facebook.com",
        "meta_access_token",
        "publish_instagram_media",
        "usd",
        "cost_usd",
    ):
        assert token not in source


def test_factory_rejects_unknown_provider(tmp_path: Path) -> None:
    with pytest.raises(AppError) as exc_info:
        get_openai_image_provider(_settings(tmp_path, image_provider="canva"))
    assert exc_info.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR
