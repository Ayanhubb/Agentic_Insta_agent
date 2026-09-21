"""Shared test fixtures and scripted tools."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel

from agent.agent import InstagramAgent
from agent.planner import DeterministicPlanner
from agent.registry import ToolRegistry
from config import Settings
from models.errors import AppError
from models.observations import Observation
from models.state import AgentState
from tools.base import Tool


def write_jpeg(
    path: Path,
    size: tuple[int, int] = (1080, 1350),
    color: tuple[int, int, int] = (200, 30, 30),
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    image.save(path, format="JPEG", quality=90)
    return path


def write_png(path: Path, size: tuple[int, int] = (1080, 1080)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", size, (0, 120, 255, 128))
    image.save(path, format="PNG")
    return path


def test_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = dict(
        meta_access_token="test-token",
        instagram_account_id="ig-user-1",
        instagram_access_token="test-token",
        instagram_ig_user_id="ig-user-1",
        image_public_base_url="https://cdn.example.test/api/v1",
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        storage_dir=tmp_path / "output" / "hosted",
        media_root=tmp_path / "storage",
        temp_dir=tmp_path / "tmp",
        static_dir=tmp_path / "static",
        storage_retry_attempts=3,
        verification_retry_attempts=3,
        verification_retry_delay_seconds=0.0,
        container_ready_attempts=2,
        container_ready_delay_seconds=0.0,
        max_retry_after_seconds=5.0,
        agent_timeout_seconds=30.0,
        graph_retry_attempts=1,
        media_poll_interval_seconds=0.0,
        media_ready_timeout_seconds=1.0,
        request_timeout_seconds=5.0,
        openai_retry_delay_seconds=0.0,
        llm_max_attempts=3,
        image_max_attempts=3,
        database_url=f"sqlite:///{(tmp_path / 'agentic.db').as_posix()}",
        token_encryption_key="test-token-encryption-key",
        jwt_secret="test-jwt-secret-key-32-bytes-min",
        bcrypt_rounds=4,
        jwt_expire_minutes=60,
        default_admin_email="",
        default_admin_password="",
        scheduler_enabled=False,
    )
    values.update(overrides)
    settings = Settings(**values)
    settings.ensure_directories()
    return settings


test_settings.__test__ = False


def auth_client_headers(client, *, email: str = "user@example.com", password: str = "password12") -> dict[str, str]:
    """Register and login, returning a Bearer header. Cookie is also stored on the client."""
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = (response.json().get("access_token") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


auth_client_headers.__test__ = False


class ScriptedTool(Tool):
    """Deterministic fake tool that yields scripted observations."""

    def __init__(
        self,
        name: str,
        purpose: str,
        script: Sequence[Observation | AppError | Callable[[AgentState], Observation | AppError]],
        *,
        data_on_success: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.purpose = purpose
        self.description = purpose
        self._script = list(script)
        self.calls = 0
        self._data_on_success = data_on_success or {}

    async def execute(self, state: AgentState) -> Observation:
        self.calls += 1
        if not self._script:
            return Observation(success=True, tool=self.name, data=dict(self._data_on_success))
        item = self._script[min(self.calls - 1, len(self._script) - 1)]
        if callable(item) and not isinstance(item, BaseModel):
            item = item(state)
        if isinstance(item, AppError):
            raise item
        if isinstance(item, Observation):
            return item
        raise TypeError(f"Unsupported script item: {item!r}")


class CountingPublisher(ScriptedTool):
    def __init__(self) -> None:
        super().__init__(
            "publish_instagram_media",
            "Publish image",
            [
                Observation(
                    success=True,
                    tool="publish_instagram_media",
                    data={"instagram_media_id": "media-1"},
                )
            ],
        )


def default_success_tools() -> list[ScriptedTool]:
    return [
        ScriptedTool(
            "validate_image",
            "Validate uploaded image",
            [Observation(success=True, tool="validate_image", data={"valid": True, "format": "JPEG", "width": 1080, "height": 1080})],
        ),
        ScriptedTool(
            "prepare_image",
            "Prepare image for publishing",
            [Observation(success=True, tool="prepare_image", data={"prepared_image_path": "/tmp/prepared.jpg"})],
        ),
        ScriptedTool(
            "upload_image",
            "Create publicly accessible image URL",
            [
                Observation(
                    success=True,
                    tool="upload_image",
                    data={"image_url": "https://cdn.example.test/photo.jpg", "storage_key": "abc.jpg"},
                )
            ],
        ),
        ScriptedTool(
            "create_instagram_media",
            "Create Instagram media container",
            [
                Observation(
                    success=True,
                    tool="create_instagram_media",
                    data={"instagram_container_id": "container-1"},
                )
            ],
        ),
        ScriptedTool(
            "publish_instagram_media",
            "Publish image",
            [Observation(success=True, tool="publish_instagram_media", data={"instagram_media_id": "media-1"})],
        ),
        ScriptedTool(
            "verify_publication",
            "Verify publication",
            [
                Observation(
                    success=True,
                    tool="verify_publication",
                    data={"verified": True, "instagram_media_id": "media-1"},
                )
            ],
        ),
    ]


class DummyInstagramClient:
    def __init__(self) -> None:
        self._published = 0

    async def create_image_container(self, image_url: str, *, task_id: str | None = None) -> dict[str, Any]:
        return {"id": "container-1", "instagram_container_id": "container-1"}

    async def get_container_status(self, container_id: str) -> dict[str, Any]:
        return {"id": container_id, "status_code": "FINISHED"}

    async def wait_until_ready(self, container_id: str) -> str:
        return "FINISHED"

    async def publish_container(self, container_id: str, *, task_id: str | None = None) -> dict[str, Any]:
        self._published += 1
        media_id = f"media-{self._published}"
        return {"id": media_id, "instagram_media_id": media_id}

    async def get_media(self, media_id: str) -> dict[str, Any]:
        return {"id": media_id, "media_type": "IMAGE", "permalink": "https://instagram.test/p/1"}

    async def list_recent_media(self, limit: int = 5) -> list[dict[str, Any]]:
        return [{"id": "media-1", "media_type": "IMAGE"}]

    async def get_account(self, account_id: str | None = None) -> dict[str, Any]:
        return {"id": account_id or "ig-user-1"}

    async def aclose(self) -> None:
        return None


async def no_sleep(_: float) -> None:
    return None


def make_agent(settings: Settings, tools: list[Tool]) -> InstagramAgent:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return InstagramAgent(
        settings,
        planner=DeterministicPlanner(),
        registry=registry,
        instagram_client=DummyInstagramClient(),  # type: ignore[arg-type]
        sleeper=no_sleep,
    )
