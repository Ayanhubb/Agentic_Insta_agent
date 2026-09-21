"""Image validation, preparation, and mock storage tests.

These tests use temporary directories and never require cloud or Meta credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from models.errors import AppError, ErrorCode
from models.state import AgentState, TaskStatus
from tests.helpers import write_jpeg, write_png
from tools.image_preparer import ImagePreparer
from tools.image_storage import CloudImageStorage, MockImageStorage
from tools.image_validator import ImageLimits, ImageValidator


def test_valid_jpeg_returns_typed_result(tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "photo.jpg", size=(1080, 1080))
    result = ImageValidator().validate(path)
    assert result.valid is True
    assert result.format == "JPEG"
    assert result.width == 1080
    assert result.height == 1080
    assert result.size_bytes and result.size_bytes > 0
    assert result.mime_type == "image/jpeg"


def test_valid_png_returns_typed_result(tmp_path: Path) -> None:
    path = write_png(tmp_path / "photo.png")
    result = ImageValidator().validate(path)
    assert result.valid is True
    assert result.format == "PNG"
    assert result.mime_type == "image/png"


def test_invalid_extension_gif_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "photo.gif"
    Image.new("RGB", (1080, 1080), (10, 20, 30)).save(path, format="GIF")
    with pytest.raises(AppError) as exc:
        ImageValidator().validate(path)
    assert exc.value.code == ErrorCode.UNSUPPORTED_FORMAT


def test_jpeg_contents_are_accepted_even_with_wrong_extension(tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "photo.gif", size=(1080, 1080))
    result = ImageValidator().validate(path)
    assert result.valid is True
    assert result.format == "JPEG"


def test_corrupted_image_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"\xff\xd8\xff" + b"not-a-real-jpeg")
    with pytest.raises(AppError) as exc:
        ImageValidator().validate(path)
    assert exc.value.code == ErrorCode.CORRUPTED_IMAGE


def test_oversized_image_is_rejected(tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "photo.jpg")
    validator = ImageValidator(max_image_bytes=10)
    with pytest.raises(AppError) as exc:
        validator.validate(path)
    assert exc.value.code == ErrorCode.FILE_TOO_LARGE


def test_invalid_image_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not an image", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        ImageValidator().validate(path)
    assert exc.value.code in {ErrorCode.CORRUPTED_IMAGE, ErrorCode.UNSUPPORTED_FORMAT}


def test_missing_file_is_invalid(tmp_path: Path) -> None:
    with pytest.raises(AppError) as exc:
        ImageValidator().validate(tmp_path / "missing.jpg")
    assert exc.value.code == ErrorCode.INVALID_FILE


def test_tiny_image_has_invalid_dimensions(tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "tiny.jpg", size=(20, 20))
    with pytest.raises(AppError) as exc:
        ImageValidator().validate(path)
    assert exc.value.code == ErrorCode.INVALID_DIMENSIONS


def test_limits_are_configurable(tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "photo.jpg", size=(400, 400))
    validator = ImageValidator(limits=ImageLimits(min_width=1000, min_height=1000, max_bytes=8 * 1024 * 1024))
    with pytest.raises(AppError) as exc:
        validator.validate(path)
    assert exc.value.code == ErrorCode.INVALID_DIMENSIONS


def test_image_preparation_writes_separate_jpeg(tmp_path: Path) -> None:
    original = write_jpeg(tmp_path / "input" / "original.jpg", size=(1600, 1080))
    original_bytes = original.read_bytes()
    preparer = ImagePreparer(output_dir=tmp_path / "output")
    prepared = preparer.prepare(original, "task-prep")
    assert prepared == tmp_path / "output" / "prepared" / "task-prep.jpg"
    assert prepared.exists()
    with Image.open(prepared) as image:
        assert image.format == "JPEG"
        assert not image.info.get("exif")
    assert original.read_bytes() == original_bytes
    assert original.exists()


def test_original_image_remains_unchanged(tmp_path: Path) -> None:
    original = write_png(tmp_path / "input" / "original.png")
    before = original.read_bytes()
    ImagePreparer(output_dir=tmp_path / "output").prepare(original, "task-png")
    assert original.read_bytes() == before


def test_preparation_is_idempotent(tmp_path: Path) -> None:
    original = write_jpeg(tmp_path / "input" / "original.jpg")
    preparer = ImagePreparer(output_dir=tmp_path / "output")
    first = preparer.prepare(original, "task-idemp")
    second = preparer.prepare(original, "task-idemp")
    assert first == second
    assert first.exists()


def test_mock_storage_returns_https_url(tmp_path: Path) -> None:
    source = write_jpeg(tmp_path / "prepared.jpg")
    storage = MockImageStorage(tmp_path / "hosted", "https://cdn.example.test/ig")
    uploaded = storage.upload(source, "task-1")
    assert uploaded.public_url == "https://cdn.example.test/ig/task-1.jpg"
    assert storage.get_public_url(uploaded.object_key) == uploaded.public_url
    assert Path(uploaded.local_path or "").is_file()
    storage.delete(uploaded.object_key)
    assert not Path(uploaded.local_path or "").exists()


def test_mock_storage_rejects_localhost() -> None:
    with pytest.raises(AppError) as exc:
        MockImageStorage(Path("hosted"), "http://localhost:8000/media")
    assert exc.value.code == ErrorCode.STORAGE_NOT_CONFIGURED


def test_storage_failure_when_source_missing(tmp_path: Path) -> None:
    storage = MockImageStorage(tmp_path / "hosted", "https://cdn.example.test/ig")
    with pytest.raises(AppError) as exc:
        storage.upload(tmp_path / "missing.jpg", "task-missing")
    assert exc.value.code in {ErrorCode.STORAGE_FAILED, ErrorCode.STORAGE_FAILURE}


def test_cloud_storage_is_explicitly_unimplemented(tmp_path: Path) -> None:
    cloud = CloudImageStorage()
    with pytest.raises(AppError) as exc:
        cloud.upload(tmp_path / "prepared.jpg", "task-cloud")
    assert exc.value.code == ErrorCode.STORAGE_NOT_CONFIGURED


def test_agent_records_validation_failure_in_history(tmp_path: Path) -> None:
    import asyncio

    from agent.agent import InstagramAgent
    from config import Settings

    bad = tmp_path / "notes.txt"
    bad.write_text("nope", encoding="utf-8")
    settings = Settings(
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        storage_dir=tmp_path / "hosted",
        temp_dir=tmp_path / "tmp",
        image_public_base_url="https://cdn.example.test/ig",
        agent_timeout_seconds=15,
    )
    settings.ensure_directories()
    agent = InstagramAgent(settings)
    state = AgentState(task_id="task-fail", image_path=str(bad))
    result = asyncio.run(agent.run(state))
    assert result.status == TaskStatus.FAILED
    assert result.error is not None
    assert result.error.code in {ErrorCode.UNSUPPORTED_FORMAT, ErrorCode.CORRUPTED_IMAGE}
    assert result.execution_history
    failed = []
    for event in result.execution_history:
        status = getattr(event, "status", None) or getattr(event, "result", None)
        target = getattr(event, "target", None)
        if status == "failed" or target == TaskStatus.FAILED:
            failed.append(event)
    assert failed
    tool_name = getattr(failed[0], "step", None) or getattr(failed[0], "tool", None)
    assert tool_name in {"validate_image", "ImageValidator"}
