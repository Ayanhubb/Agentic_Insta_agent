"""Image lifecycle, validation, storage isolation, and cleanup tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image

from models.errors import AppError, ErrorCode
from models.media import MediaLifecycleStatus, can_transition
from services.media_paths import MediaPaths, generate_internal_filename
from services.media_repository import InMemoryMediaRepository
from services.media_storage import MediaStorageService
from tests.helpers import write_jpeg, write_png
from tools.image_preparer import ImagePreparer
from tools.image_storage import MockImageStorage
from tools.image_validator import ImageValidator

INTERNAL_NAME = re.compile(r"^img_[0-9a-f]{32}\.(png|jpg)$")


def _service(settings, **kwargs) -> MediaStorageService:
    return MediaStorageService(settings, repository=InMemoryMediaRepository(), **kwargs)


def _bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_valid_jpeg_is_ingested_and_prepared(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg", size=(1080, 1080)))
    service = _service(tmp_settings)
    record = service.ingest_generated("user-a", payload, claimed_mime="image/jpeg")
    assert record.status == MediaLifecycleStatus.READY_FOR_APPROVAL
    assert record.mime_type == "image/jpeg"
    assert record.width == 1080
    assert INTERNAL_NAME.match(record.filename or "")
    generated = tmp_settings.media_root / "generated" / "user-a" / record.filename
    prepared = tmp_settings.media_root / "prepared" / "user-a" / (record.prepared_filename or "")
    assert generated.is_file()
    assert prepared.is_file()
    with Image.open(prepared) as image:
        assert image.format == "JPEG"
    public = record.public_dict()
    assert "storage_path" not in public
    assert "generated_relpath" not in public
    assert public["media_url"] == f"/api/v1/generation/{record.id}/media"
    assert str(tmp_settings.media_root) not in str(public)


def test_valid_png_is_ingested_and_normalized_to_jpeg(tmp_settings, tmp_path: Path) -> None:
    original = write_png(tmp_path / "photo.png")
    before = original.read_bytes()
    service = _service(tmp_settings)
    record = service.ingest_generated("user-a", before, claimed_mime="image/png")
    assert record.status == MediaLifecycleStatus.READY_FOR_APPROVAL
    assert record.filename and record.filename.endswith(".png")
    assert record.prepared_filename and record.prepared_filename.endswith(".jpg")
    assert original.read_bytes() == before
    prepared = tmp_settings.media_root / "prepared" / "user-a" / record.prepared_filename
    with Image.open(prepared) as image:
        assert image.format == "JPEG"


def test_corrupt_image_is_rejected(tmp_settings) -> None:
    service = _service(tmp_settings)
    record = service.ingest_generated(
        "user-a",
        b"\xff\xd8\xff" + b"not-a-real-jpeg",
        claimed_mime="image/jpeg",
    )
    assert record.status == MediaLifecycleStatus.VALIDATION_FAILED
    assert record.error_code == ErrorCode.CORRUPTED_IMAGE.value
    generated_dir = tmp_settings.media_root / "generated" / "user-a"
    assert not generated_dir.exists() or not any(generated_dir.glob("img_*"))


def test_oversized_image_is_rejected(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg"))
    service = _service(tmp_settings, validator=ImageValidator(max_image_bytes=50))
    record = service.ingest_generated("user-a", payload, claimed_mime="image/jpeg")
    assert record.status == MediaLifecycleStatus.VALIDATION_FAILED
    assert record.error_code in {ErrorCode.FILE_TOO_LARGE.value, ErrorCode.IMAGE_TOO_LARGE.value}


def test_invalid_mime_is_rejected(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg"))
    service = _service(tmp_settings)
    record = service.ingest_generated("user-a", payload, claimed_mime="image/gif")
    assert record.status == MediaLifecycleStatus.VALIDATION_FAILED
    assert record.error_code in {ErrorCode.UNSUPPORTED_FORMAT.value, ErrorCode.INVALID_MIME.value}

    mismatched = service.ingest_generated("user-a", payload, claimed_mime="image/png")
    assert mismatched.status == MediaLifecycleStatus.VALIDATION_FAILED
    assert mismatched.error_code == ErrorCode.UNSUPPORTED_FORMAT.value


def test_validator_does_not_trust_extension(tmp_path: Path) -> None:
    path = write_jpeg(tmp_path / "photo.gif", size=(1080, 1080))
    result = ImageValidator().validate(path)
    assert result.valid is True
    assert result.format == "JPEG"
    assert result.mime_type == "image/jpeg"


def test_path_traversal_is_rejected(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg"))
    service = _service(tmp_settings)
    paths = MediaPaths(tmp_settings.media_root)
    record = service.ingest_generated("../etc/passwd", payload, claimed_mime="image/jpeg")
    generated = (tmp_settings.media_root / "generated" / record.user_id / record.filename).resolve()
    assert generated.is_file()
    assert generated.is_relative_to(tmp_settings.media_root.resolve())
    assert "passwd" not in (record.filename or "")
    assert ".." not in record.user_id

    with pytest.raises(AppError):
        paths.image_file("generated", "user-a", "../../secret.png")
    with pytest.raises(AppError):
        paths.from_relative("generated/../../secret.png")

    outside = (tmp_path / "outside.txt").resolve()
    assert not any(path.resolve() == outside for path in tmp_settings.media_root.rglob("*"))


def test_user_isolation_on_records(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg", size=(1080, 1080)))
    service = _service(tmp_settings)
    owned = service.ingest_generated("user-a", payload, claimed_mime="image/jpeg")
    listed = service.list_for_user("user-b")
    assert listed == []
    with pytest.raises(AppError) as exc:
        service.get_for_user("user-b", owned.id)
    assert exc.value.code == ErrorCode.TASK_NOT_FOUND
    assert service.get_for_user("user-a", owned.id).id == owned.id


def test_storage_failure_cleans_partial_files(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg", size=(1080, 1080)))

    def boom(_path: Path, _data: bytes) -> Path:
        raise OSError("disk full")

    service = _service(tmp_settings, write_file=boom)
    record = service.ingest_generated("user-a", payload, claimed_mime="image/jpeg")
    assert record.status == MediaLifecycleStatus.GENERATION_FAILED
    assert record.error_code == ErrorCode.STORAGE_FAILURE.value
    generated_dir = tmp_settings.media_root / "generated" / "user-a"
    leftovers = list(generated_dir.glob("img_*")) if generated_dir.exists() else []
    assert leftovers == []


def test_cleanup_removes_files_and_metadata(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg", size=(1080, 1080)))
    service = _service(tmp_settings)
    record = service.ingest_generated("user-a", payload, claimed_mime="image/jpeg")
    generated = tmp_settings.media_root / "generated" / "user-a" / record.filename
    prepared = tmp_settings.media_root / "prepared" / "user-a" / record.prepared_filename
    assert generated.is_file()
    assert prepared.is_file()
    service.cleanup("user-a", record.id)
    assert not generated.exists()
    assert not prepared.exists()
    with pytest.raises(AppError) as exc:
        service.get_for_user("user-a", record.id)
    assert exc.value.code == ErrorCode.TASK_NOT_FOUND


def test_lifecycle_happy_path_and_illegal_transition(tmp_settings, tmp_path: Path) -> None:
    payload = _bytes(write_jpeg(tmp_path / "photo.jpg", size=(1080, 1080)))
    service = _service(tmp_settings)
    record = service.ingest_generated("user-a", payload, claimed_mime="image/jpeg")
    assert [item.target for item in record.transitions] == [
        MediaLifecycleStatus.VALIDATED,
        MediaLifecycleStatus.PREPARED,
        MediaLifecycleStatus.READY_FOR_APPROVAL,
    ]
    approved = service.approve("user-a", record.id)
    assert approved.status == MediaLifecycleStatus.APPROVED
    publishing = service.mark_publishing("user-a", record.id)
    assert publishing.status == MediaLifecycleStatus.PUBLISHING
    published = service.mark_published("user-a", record.id)
    assert published.status == MediaLifecycleStatus.PUBLISHED
    published_file = tmp_settings.media_root / "published" / "user-a" / published.published_filename
    assert published_file.is_file()
    assert not can_transition(MediaLifecycleStatus.READY_FOR_APPROVAL, MediaLifecycleStatus.PUBLISHED)
    with pytest.raises(AppError):
        published.record_transition(MediaLifecycleStatus.APPROVED)


def test_internal_filename_generation() -> None:
    name = generate_internal_filename("png")
    assert INTERNAL_NAME.match(name)
    assert generate_internal_filename("jpeg").endswith(".jpg")


def test_v1_preparer_and_upload_image_workflow_unchanged(tmp_settings, tmp_path: Path) -> None:
    original = write_jpeg(tmp_path / "input" / "original.jpg", size=(1600, 1080))
    before = original.read_bytes()
    prepared = ImagePreparer(tmp_settings).prepare(original, "task-v1")
    assert prepared == tmp_settings.output_dir / "prepared" / "task-v1.jpg"
    assert original.read_bytes() == before
    hosted = MockImageStorage(tmp_settings.storage_dir, "https://cdn.example.test/ig")
    uploaded = hosted.upload(prepared, "task-v1")
    assert uploaded.public_url.endswith("/task-v1.jpg")
    assert Path(uploaded.local_path or "").is_file()
    with pytest.raises(AppError):
        hosted.resolve_public_file("../secret.jpg")
