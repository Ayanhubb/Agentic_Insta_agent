"""Mocked OpenAI image generation tests. Default pytest never calls the real API."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from ai.image_generator import GeneratedImageStore, LocalGeneratedImageStore, get_image_generation_provider
from ai.openai_image_generator import OpenAIImageGenerationProvider
from ai.schemas import ContentSource, GeneratedImage, ImageGenerationRequest, StoredGeneratedImage
from models.errors import AppError, ErrorCode
from tests.helpers import no_sleep, test_settings


PROMPT = "A boutique window display of silk sarees with warm diya lighting for Instagram."


def _png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (180, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def _ai_settings(tmp_path: Path, **overrides):
    values = dict(
        openai_api_key="sk-test-not-a-real-openai-key-xxxxx",
        llm_model="configured-llm-model",
        image_provider="openai",
        image_model="configured-image-model",
        image_max_attempts=2,
        openai_retry_delay_seconds=0.0,
    )
    values.update(overrides)
    return test_settings(tmp_path, **values)


class FakeImages:
    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls = 0
        self.kwargs: list[dict] = []

    async def generate(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        item = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


class FakeImageClient:
    def __init__(self, script: list[object]) -> None:
        self.images = FakeImages(script)


class FailingStore(GeneratedImageStore):
    def save(self, *, user_id: str, image_bytes: bytes, mime_type: str) -> StoredGeneratedImage:
        raise AppError(ErrorCode.STORAGE_FAILURE, "The generated image could not be stored.")


def _image_response(image_bytes: bytes | None = None, revised: str | None = None) -> SimpleNamespace:
    payload = image_bytes if image_bytes is not None else _png_bytes()
    return SimpleNamespace(
        data=[
            SimpleNamespace(
                b64_json=base64.b64encode(payload).decode("ascii"),
                url=None,
                revised_prompt=revised,
            )
        ]
    )


def _request() -> ImageGenerationRequest:
    return ImageGenerationRequest(
        prompt=PROMPT,
        user_id="user-1",
        original_prompt="Diwali silk collection",
        source=ContentSource.FESTIVAL_AUTOMATION,
    )


@pytest.mark.asyncio
async def test_successful_image_generation(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path)
    client = FakeImageClient([_image_response(revised="Enhanced boutique Diwali display, silk sarees, warm lamps.")])
    provider = OpenAIImageGenerationProvider(settings, client=client, sleeper=no_sleep)
    record = await provider.generate(_request())
    assert isinstance(record, GeneratedImage)
    assert record.user_id == "user-1"
    assert record.provider == "openai"
    assert record.model == "configured-image-model"
    assert record.width == 64
    assert record.height == 64
    assert record.generation_status.value == "GENERATED"
    assert record.source is ContentSource.FESTIVAL_AUTOMATION
    assert Path(record.storage_path).is_file()
    assert record.filename.startswith("img_")
    assert "user-1" in record.storage_path.replace("\\", "/")
    assert "openai_api_key" not in record.public_dict()
    assert client.images.kwargs[0]["model"] == "configured-image-model"
    assert "instagram" not in dir(provider)


@pytest.mark.asyncio
async def test_image_generation_failure(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path, image_max_attempts=2)
    client = FakeImageClient([RuntimeError("image api down")])
    provider = OpenAIImageGenerationProvider(settings, client=client, sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.generate(_request())
    assert exc_info.value.code == ErrorCode.OPENAI_API_ERROR
    assert client.images.calls == 2
    assert "sk-test" not in exc_info.value.message


@pytest.mark.asyncio
async def test_image_storage_failure(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path)
    client = FakeImageClient([_image_response()])
    provider = OpenAIImageGenerationProvider(
        settings,
        client=client,
        storage=FailingStore(),
        sleeper=no_sleep,
    )
    with pytest.raises(AppError) as exc_info:
        await provider.generate(_request())
    assert exc_info.value.code == ErrorCode.STORAGE_FAILURE
    assert client.images.calls == 1


@pytest.mark.asyncio
async def test_missing_api_key_blocks_image_generation(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path, openai_api_key="")
    provider = OpenAIImageGenerationProvider(settings, sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.generate(_request())
    assert exc_info.value.code == ErrorCode.OPENAI_CONFIGURATION_ERROR


@pytest.mark.asyncio
async def test_invalid_image_bytes(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path)
    bad = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(b"not-an-image").decode("ascii"), url=None, revised_prompt=None)]
    )
    provider = OpenAIImageGenerationProvider(settings, client=FakeImageClient([bad]), sleeper=no_sleep)
    with pytest.raises(AppError) as exc_info:
        await provider.generate(_request())
    assert exc_info.value.code == ErrorCode.OPENAI_INVALID_RESPONSE


def test_generated_store_blocks_path_traversal(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path)
    store = LocalGeneratedImageStore(settings)
    stored = store.save(user_id="../../etc/passwd", image_bytes=_png_bytes(), mime_type="image/png")
    path = Path(stored.storage_path).resolve()
    assert path.is_relative_to((settings.media_root / "generated").resolve())
    assert "passwd" not in stored.filename


def test_image_factory(tmp_path: Path) -> None:
    settings = _ai_settings(tmp_path)
    provider = get_image_generation_provider(settings)
    assert isinstance(provider, OpenAIImageGenerationProvider)
    assert not hasattr(provider, "publish")
    assert not hasattr(provider, "publish_instagram_media")
