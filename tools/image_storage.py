"""Public image storage for Instagram publishing.

The Agent depends on the ImageStorage tool, which delegates to a provider
interface. Instagram requires a publicly reachable HTTPS URL.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

from config import Settings
from models.errors import AppError, ErrorCode, OperationCertainty
from models.observations import Observation
from models.state import AgentState
from tools.base import Tool

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
DEFAULT_MOCK_PUBLIC_BASE_URL = "https://cdn.example.test/instagram-agent"


class StorageObject(BaseModel):
    object_key: str
    public_url: str
    local_path: str | None = None
    size_bytes: int = 0


def assert_public_https_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise AppError(
            ErrorCode.STORAGE_NOT_CONFIGURED,
            "Public image hosting must use HTTPS.",
            http_status=503,
        )
    hostname = (parsed.hostname or "").lower()
    if hostname in LOCAL_HOSTS:
        raise AppError(
            ErrorCode.STORAGE_NOT_CONFIGURED,
            "Public image hosting cannot use localhost. Instagram must fetch "
            "the image over a publicly reachable HTTPS URL.",
            http_status=503,
        )
    return url.rstrip("/")


class ImageStorageProvider(ABC):
    """Storage interface. The Agent depends on this contract, not a cloud vendor."""
    @abstractmethod
    def upload(self, local_path: Path, task_id: str) -> StorageObject: ...

    @abstractmethod
    def get_public_url(self, object_key: str) -> str: ...

    @abstractmethod
    def delete(self, object_key: str) -> None: ...


class MockImageStorage(ImageStorageProvider):
    def __init__(self, storage_dir: Path, public_base_url: str | None = None) -> None:
        self._storage_dir = Path(storage_dir)
        self._public_base_url = assert_public_https_url(
            public_base_url or DEFAULT_MOCK_PUBLIC_BASE_URL
        )

    def upload(self, local_path: Path, task_id: str) -> StorageObject:
        source = Path(local_path)
        if not source.exists() or not source.is_file():
            raise AppError(
                ErrorCode.STORAGE_FAILURE,
                "The prepared image could not be found for upload.",
                http_status=500,
            )
        object_key = f"{task_id}.jpg"
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        destination = self._storage_dir / object_key
        try:
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except OSError as exc:
            raise AppError(
                ErrorCode.STORAGE_TEMPORARY_FAILURE,
                "Temporary storage is unavailable.",
                http_status=503,
                retryable=True,
                certainty=OperationCertainty.FAILED,
            ) from exc
        return StorageObject(
            object_key=object_key,
            public_url=self.get_public_url(object_key),
            local_path=str(destination),
            size_bytes=destination.stat().st_size,
        )

    def get_public_url(self, object_key: str) -> str:
        safe_key = Path(object_key).name
        return f"{self._public_base_url}/{safe_key}"

    def delete(self, object_key: str) -> None:
        path = self._storage_dir / Path(object_key).name
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise AppError(
                ErrorCode.STORAGE_FAILURE,
                "The stored image could not be deleted.",
                http_status=500,
            ) from exc

    def resolve_public_file(self, filename: str) -> Path:
        safe = Path(filename).name
        if safe != filename or ".." in filename:
            raise AppError(ErrorCode.STORAGE_FAILURE, "Invalid storage key.")
        root = self._storage_dir.resolve()
        path = (root / safe).resolve()
        if path.parent != root:
            raise AppError(ErrorCode.STORAGE_FAILURE, "Invalid storage path.")
        return path


class CloudImageStorage(ImageStorageProvider):
    def upload(self, local_path: Path, task_id: str) -> StorageObject:
        raise AppError(
            ErrorCode.STORAGE_NOT_CONFIGURED,
            "Cloud image storage is not implemented yet.",
            http_status=503,
        )

    def get_public_url(self, object_key: str) -> str:
        raise AppError(
            ErrorCode.STORAGE_NOT_CONFIGURED,
            "Cloud image storage is not implemented yet.",
            http_status=503,
        )

    def delete(self, object_key: str) -> None:
        raise AppError(
            ErrorCode.STORAGE_NOT_CONFIGURED,
            "Cloud image storage is not implemented yet.",
            http_status=503,
        )


class ImageStorage(Tool):
    name = "upload_image"
    purpose = "Create publicly accessible image URL"
    description = (
        "Host the prepared JPEG at a publicly reachable HTTPS URL required by "
        "the Instagram Graph API."
    )

    def __init__(
        self,
        settings: Settings | ImageStorageProvider,
        *,
        storage: ImageStorageProvider | None = None,
    ) -> None:
        if isinstance(settings, ImageStorageProvider):
            self._backend = settings
            self._settings = None
            return
        self._settings = settings
        if storage is not None:
            self._backend = storage
            return
        hosted_dir = settings.storage_dir
        public_url = settings.public_image_base_url
        if settings.credentials_configured:
            public_url = assert_public_https_url(public_url)
        else:
            try:
                public_url = assert_public_https_url(public_url)
            except AppError:
                public_url = DEFAULT_MOCK_PUBLIC_BASE_URL
        self._backend = MockImageStorage(storage_dir=hosted_dir, public_base_url=public_url)

    def upload(self, local_path: Path, task_id: str) -> StorageObject:
        return self._backend.upload(local_path, task_id)

    def get_public_url(self, object_key: str) -> str:
        return self._backend.get_public_url(object_key)

    def delete(self, object_key: str) -> None:
        self._backend.delete(object_key)

    def resolve_public_file(self, filename: str) -> Path:
        resolve = getattr(self._backend, "resolve_public_file", None)
        if resolve is None:
            raise AppError(ErrorCode.STORAGE_FAILURE, "Storage cannot resolve local files.")
        return resolve(filename)

    async def execute(self, state: AgentState) -> Observation:
        source = state.prepared_image_path or state.image_path
        if not source:
            raise AppError(ErrorCode.STORAGE_FAILURE, "No prepared image is available to store.")
        uploaded = self._backend.upload(Path(source), state.task_id)
        return Observation(
            success=True,
            tool=self.name,
            data={
                "image_url": uploaded.public_url,
                "storage_key": uploaded.object_key,
                "stored_path": uploaded.local_path,
            },
        )


ImageStorageTool = ImageStorage


def build_image_storage(
    *,
    output_dir: Path,
    public_base_url: str | None,
    mock_public_base_url: str = DEFAULT_MOCK_PUBLIC_BASE_URL,
) -> ImageStorageProvider:
    hosted_dir = Path(output_dir) / "hosted"
    return MockImageStorage(
        storage_dir=hosted_dir,
        public_base_url=public_base_url or mock_public_base_url,
    )
