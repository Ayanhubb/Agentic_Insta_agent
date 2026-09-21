"""Persistence for generated-image metadata. Filesystem paths stay off the public API."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol

from models.errors import AppError, ErrorCode
from models.media import GeneratedImageRecord
from services.media_paths import assert_safe_image_id, assert_safe_user_id, resolve_contained


class MediaRecordRepository(Protocol):
    def save(self, record: GeneratedImageRecord) -> None: ...

    def get_for_user(self, user_id: str, image_id: str) -> GeneratedImageRecord | None: ...

    def list_for_user(self, user_id: str) -> list[GeneratedImageRecord]: ...

    def delete(self, user_id: str, image_id: str) -> None: ...


class InMemoryMediaRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], GeneratedImageRecord] = {}

    def save(self, record: GeneratedImageRecord) -> None:
        user_id = assert_safe_user_id(record.user_id)
        image_id = assert_safe_image_id(record.id)
        with self._lock:
            self._records[(user_id, image_id)] = record.model_copy(deep=True)

    def get_for_user(self, user_id: str, image_id: str) -> GeneratedImageRecord | None:
        key = (assert_safe_user_id(user_id), assert_safe_image_id(image_id))
        with self._lock:
            record = self._records.get(key)
            return record.model_copy(deep=True) if record else None

    def list_for_user(self, user_id: str) -> list[GeneratedImageRecord]:
        owner = assert_safe_user_id(user_id)
        with self._lock:
            items = [
                record.model_copy(deep=True)
                for (stored_user, _), record in self._records.items()
                if stored_user == owner
            ]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    def delete(self, user_id: str, image_id: str) -> None:
        key = (assert_safe_user_id(user_id), assert_safe_image_id(image_id))
        with self._lock:
            self._records.pop(key, None)


class JsonFileMediaRepository:
    def __init__(self, media_root: Path) -> None:
        self._root = Path(media_root).resolve()
        self._index = self._root / ".index"
        self._lock = threading.Lock()
        self._index.mkdir(parents=True, exist_ok=True)

    def _record_path(self, user_id: str, image_id: str) -> Path:
        return resolve_contained(self._index, assert_safe_user_id(user_id), f"{assert_safe_image_id(image_id)}.json")

    def save(self, record: GeneratedImageRecord) -> None:
        path = self._record_path(record.user_id, record.id)
        payload = record.model_dump(mode="json")
        tmp_path = path.with_name(path.name + ".tmp")
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.write_text(json.dumps(payload), encoding="utf-8")
                tmp_path.replace(path)
            except OSError as exc:
                tmp_path.unlink(missing_ok=True)
                raise AppError(
                    ErrorCode.STORAGE_FAILURE,
                    "The image record could not be stored.",
                    http_status=500,
                ) from exc

    def get_for_user(self, user_id: str, image_id: str) -> GeneratedImageRecord | None:
        path = self._record_path(user_id, image_id)
        with self._lock:
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        record = GeneratedImageRecord.model_validate(payload)
        if record.user_id != assert_safe_user_id(user_id):
            return None
        return record

    def list_for_user(self, user_id: str) -> list[GeneratedImageRecord]:
        directory = resolve_contained(self._index, assert_safe_user_id(user_id))
        records: list[GeneratedImageRecord] = []
        with self._lock:
            if not directory.is_dir():
                return []
            files = list(directory.glob("*.json"))
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = GeneratedImageRecord.model_validate(payload)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if record.user_id == assert_safe_user_id(user_id):
                records.append(record)
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records

    def delete(self, user_id: str, image_id: str) -> None:
        path = self._record_path(user_id, image_id)
        with self._lock:
            path.unlink(missing_ok=True)
