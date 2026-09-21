"""User-scoped local media storage and generated-image lifecycle."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable
from uuid import uuid4

from config import Settings
from models.errors import AppError, ErrorCode
from models.media import ContentSource, GeneratedImageRecord, MediaLifecycleStatus, MediaStage
from services.media_paths import (
    MediaPaths,
    assert_safe_user_id,
    atomic_write,
    generate_internal_filename,
)
from services.media_repository import JsonFileMediaRepository, MediaRecordRepository
from tools.image_preparer import ImagePreparer
from tools.image_validator import ImageLimits, ImageValidator

WriteFile = Callable[[Path, bytes], Path]


def _default_write(path: Path, data: bytes) -> Path:
    return atomic_write(path, data)


class MediaStorage:
    """Minimal user-scoped copy/save helper used by automation."""

    def __init__(self, settings: Settings) -> None:
        self._paths = MediaPaths(settings.media_root)

    def save_bytes(self, user_id: str, kind: str, data: bytes, mime_type: str) -> Path:
        ext = "jpg" if mime_type.lower() in {"image/jpeg", "image/jpg"} else "png"
        filename = generate_internal_filename(ext)
        destination = self._paths.image_file(kind, user_id, filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return atomic_write(destination, data)

    def copy_into(self, user_id: str, kind: str, source: Path) -> Path:
        ext = source.suffix.lower().lstrip(".") or "jpg"
        filename = generate_internal_filename(ext)
        destination = self._paths.image_file(kind, user_id, filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return destination

    def resolve(self, user_id: str, kind: str, filename: str) -> Path:
        return self._paths.image_file(kind, user_id, filename)


class MediaStorageService(MediaStorage):
    """Lifecycle service: generate → validate → prepare → approve → publish copy."""

    def __init__(
        self,
        settings: Settings,
        *,
        repository: MediaRecordRepository | None = None,
        validator: ImageValidator | None = None,
        preparer: ImagePreparer | None = None,
        write_file: WriteFile | None = None,
    ) -> None:
        super().__init__(settings)
        self._settings = settings
        self._repo = repository or JsonFileMediaRepository(settings.media_root)
        self._validator = validator or ImageValidator(
            ImageLimits(
                max_bytes=int(settings.max_image_bytes or 8 * 1024 * 1024),
                min_width=1,
                min_height=1,
                max_width=max(int(settings.max_image_side or 8192), 8192),
                max_height=max(int(settings.max_image_side or 8192), 8192),
            )
        )
        self._preparer = preparer or ImagePreparer(settings)
        self._write = write_file or _default_write

    def save(self, *, user_id: str, image_bytes: bytes, mime_type: str):
        from ai.schemas import StoredGeneratedImage
        from models.media import FAILURE_STATUSES

        record = self.ingest_generated(user_id, image_bytes, claimed_mime=mime_type)
        if record.status in FAILURE_STATUSES or not record.generated_relpath:
            code = record.error_code or ErrorCode.STORAGE_FAILURE.value
            try:
                error_code = ErrorCode(code)
            except ValueError:
                error_code = ErrorCode.STORAGE_FAILURE
            raise AppError(
                error_code,
                record.error_message or "The generated image could not be stored.",
            )
        return StoredGeneratedImage(
            filename=record.filename or "",
            storage_path=str(self._paths.from_relative(record.generated_relpath)),
            mime_type=record.mime_type or mime_type,
            size_bytes=record.size_bytes or len(image_bytes),
            public_url=record.public_dict()["media_url"],
        )

    def ingest_generated(
        self,
        user_id: str,
        image_bytes: bytes,
        *,
        claimed_mime: str,
        source: ContentSource = ContentSource.USER_PROMPT,
        original_prompt: str | None = None,
        enhanced_prompt: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> GeneratedImageRecord:
        owner = assert_safe_user_id(user_id)
        record = GeneratedImageRecord(
            id=str(uuid4()),
            user_id=owner,
            source=source,
            original_prompt=original_prompt,
            enhanced_prompt=enhanced_prompt,
            model=model,
            provider=provider,
        )
        claimed = (claimed_mime or "").lower()
        if claimed not in {"image/jpeg", "image/jpg", "image/png"}:
            record.error_code = ErrorCode.UNSUPPORTED_FORMAT.value
            record.error_message = "Only JPEG and PNG images are supported."
            record.status = MediaLifecycleStatus.VALIDATION_FAILED
            self._repo.save(record)
            return record

        tmp_dir = Path(self._settings.temp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".png" if claimed == "image/png" else ".jpg"
        scratch = tmp_dir / f"{record.id}{suffix}"
        generated_path: Path | None = None
        try:
            try:
                self._write(scratch, image_bytes)
            except OSError:
                record.error_code = ErrorCode.STORAGE_FAILURE.value
                record.error_message = "The image could not be stored."
                record.status = MediaLifecycleStatus.GENERATION_FAILED
                self._repo.save(record)
                return record

            inspected = self._validator.inspect(scratch, claimed_mime=claimed_mime)
            if not inspected.valid:
                record.error_code = inspected.error_code
                record.error_message = inspected.error_message
                record.status = MediaLifecycleStatus.VALIDATION_FAILED
                self._repo.save(record)
                return record

            actual_mime = (inspected.mime_type or "").lower()
            expected = "image/png" if claimed == "image/png" else "image/jpeg"
            if actual_mime not in {expected, "image/jpg" if expected == "image/jpeg" else expected}:
                record.error_code = ErrorCode.UNSUPPORTED_FORMAT.value
                record.error_message = "Only JPEG and PNG images are supported."
                record.status = MediaLifecycleStatus.VALIDATION_FAILED
                self._repo.save(record)
                return record

            ext = "png" if actual_mime == "image/png" else "jpg"
            filename = generate_internal_filename(ext)
            generated_path = self._paths.image_file("generated", owner, filename)
            generated_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._write(generated_path, scratch.read_bytes())
            except OSError:
                record.error_code = ErrorCode.STORAGE_FAILURE.value
                record.error_message = "The image could not be stored."
                record.status = MediaLifecycleStatus.GENERATION_FAILED
                self._repo.save(record)
                return record

            record.filename = filename
            record.generated_relpath = self._paths.relative_to_root(generated_path)
            record.mime_type = inspected.mime_type
            record.width = inspected.width
            record.height = inspected.height
            record.size_bytes = inspected.size_bytes
            record.record_transition(MediaLifecycleStatus.VALIDATED)

            prepared_name = generate_internal_filename("jpg")
            prepared_path = self._paths.image_file("prepared", owner, prepared_name)
            prepared_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._preparer.prepare_to(generated_path, prepared_path)
            except AppError as exc:
                record.error_code = exc.code.value
                record.error_message = exc.message
                record.record_transition(MediaLifecycleStatus.PREPARATION_FAILED)
                self._repo.save(record)
                return record

            record.prepared_filename = prepared_name
            record.prepared_relpath = self._paths.relative_to_root(prepared_path)
            record.record_transition(MediaLifecycleStatus.PREPARED)
            record.record_transition(MediaLifecycleStatus.READY_FOR_APPROVAL)
            self._repo.save(record)
            return record
        finally:
            scratch.unlink(missing_ok=True)
            if record.status in {
                MediaLifecycleStatus.VALIDATION_FAILED,
                MediaLifecycleStatus.GENERATION_FAILED,
            } and generated_path is not None:
                generated_path.unlink(missing_ok=True)

    def list_for_user(self, user_id: str) -> list[GeneratedImageRecord]:
        return self._repo.list_for_user(assert_safe_user_id(user_id))

    def get_for_user(self, user_id: str, image_id: str) -> GeneratedImageRecord:
        owner = assert_safe_user_id(user_id)
        record = self._repo.get_for_user(owner, image_id)
        if record is None or record.user_id != owner:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "Generated image was not found.", http_status=404)
        return record

    def resolve_owned_media(self, user_id: str, image_id: str, stage: str | None = None) -> tuple[Path, str]:
        record = self.get_for_user(user_id, image_id)
        chosen = (stage or MediaStage.PREPARED.value).lower()
        if chosen not in {MediaStage.GENERATED.value, MediaStage.PREPARED.value, MediaStage.PUBLISHED.value}:
            raise AppError(ErrorCode.INVALID_REQUEST, "Invalid media stage.", http_status=400)
        rel = {
            MediaStage.GENERATED.value: record.generated_relpath,
            MediaStage.PREPARED.value: record.prepared_relpath or record.generated_relpath,
            MediaStage.PUBLISHED.value: record.published_relpath or record.prepared_relpath,
        }[chosen]
        if not rel:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "Generated image was not found.", http_status=404)
        path = self._paths.from_relative(rel)
        if not path.is_file():
            raise AppError(ErrorCode.TASK_NOT_FOUND, "Media file was not found.", http_status=404)
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        return path, mime

    def approve(self, user_id: str, image_id: str) -> GeneratedImageRecord:
        record = self.get_for_user(user_id, image_id)
        record.record_transition(MediaLifecycleStatus.APPROVED)
        self._repo.save(record)
        return record

    def mark_publishing(self, user_id: str, image_id: str) -> GeneratedImageRecord:
        record = self.get_for_user(user_id, image_id)
        record.record_transition(MediaLifecycleStatus.PUBLISHING)
        self._repo.save(record)
        return record

    def mark_published(self, user_id: str, image_id: str) -> GeneratedImageRecord:
        record = self.get_for_user(user_id, image_id)
        record.record_transition(MediaLifecycleStatus.PUBLISHED)
        source_rel = record.prepared_relpath or record.generated_relpath
        if source_rel:
            source = self._paths.from_relative(source_rel)
            name = generate_internal_filename("jpg")
            dest = self._paths.image_file("published", user_id, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            record.published_filename = name
            record.published_relpath = self._paths.relative_to_root(dest)
        self._repo.save(record)
        return record

    def cleanup(self, user_id: str, image_id: str) -> None:
        record = self._repo.get_for_user(user_id, image_id)
        if record is None:
            raise AppError(ErrorCode.TASK_NOT_FOUND, "Generated image was not found.", http_status=404)
        for rel in (record.generated_relpath, record.prepared_relpath, record.published_relpath):
            if not rel:
                continue
            path = self._paths.from_relative(rel)
            path.unlink(missing_ok=True)
        self._repo.delete(user_id, image_id)
