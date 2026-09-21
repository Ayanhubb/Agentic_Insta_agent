from pathlib import Path

import pytest

from models.errors import AppError, ErrorCode
from tests.helpers import write_jpeg, write_png
from tools.image_preparer import ImagePreparer
from tools.image_storage import ImageStorage
from tools.image_validator import ImageLimits, ImageValidator
from config import Settings
from models.state import AgentState


def test_validate_accepts_jpeg(tmp_settings: Settings, tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "photo.jpg")
    data = ImageValidator(tmp_settings).validate_path(str(path))
    assert data["valid"] is True
    assert data["format"] == "JPEG"
    assert data["width"] == 1080


def test_validate_accepts_png(tmp_settings: Settings, tmp_path: Path) -> None:
    path = write_png(tmp_path / "photo.png")
    data = ImageValidator(tmp_settings).validate_path(str(path))
    assert data["format"] == "PNG"


def test_validate_rejects_missing_file(tmp_settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(AppError) as exc:
        ImageValidator(tmp_settings).validate_path(str(tmp_path / "missing.jpg"))
    assert exc.value.code in {
        ErrorCode.INVALID_FILE,
        ErrorCode.INVALID_IMAGE,
        ErrorCode.IMAGE_VALIDATION_FAILED,
    }


def test_validate_rejects_invalid_extension(tmp_settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not an image", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        ImageValidator(tmp_settings).validate_path(str(path))
    assert exc.value.code in {
        ErrorCode.UNSUPPORTED_FORMAT,
        ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        ErrorCode.CORRUPTED_IMAGE,
        ErrorCode.INVALID_IMAGE,
    }


def test_validate_rejects_corrupted_image(tmp_settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not-a-real-jpeg")
    with pytest.raises(AppError) as exc:
        ImageValidator(tmp_settings).validate_path(str(path))
    assert exc.value.code in {
        ErrorCode.INVALID_IMAGE,
        ErrorCode.CORRUPTED_IMAGE,
        ErrorCode.IMAGE_CORRUPTED,
    }


def test_validate_rejects_oversize_image(tmp_settings: Settings, tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "photo.jpg")
    with pytest.raises(AppError) as exc:
        ImageValidator(max_image_bytes=10).validate(path)
    assert exc.value.code in {ErrorCode.FILE_TOO_LARGE, ErrorCode.IMAGE_TOO_LARGE}


def test_prepare_does_not_modify_original(tmp_settings: Settings, tmp_path: Path) -> None:
    original = write_jpeg(tmp_path / "original.jpg")
    before = original.read_bytes()
    prepared = ImagePreparer(tmp_settings).prepare(str(original), "task-orig")
    assert original.read_bytes() == before
    assert Path(prepared).exists()
    assert Path(prepared) != original


def test_mock_storage_returns_https_url(tmp_settings: Settings, tmp_path: Path) -> None:
    import asyncio

    prepared = write_jpeg(tmp_path / "prepared.jpg")
    storage = ImageStorage(tmp_settings)

    async def _run() -> None:
        observation = await storage.execute(
            AgentState(task_id="t1", image_path=str(prepared), prepared_image_path=str(prepared))
        )
        assert observation.success is True
        assert observation.data is not None
        assert str(observation.data["image_url"]).startswith("https://")
        assert "localhost" not in str(observation.data["image_url"])

    asyncio.run(_run())
