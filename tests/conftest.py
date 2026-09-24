"""Shared pytest fixtures."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import httpx
import pytest

from config import Settings
from tests.helpers import test_settings
from tools.instagram_media import InstagramMediaService


@pytest.fixture(autouse=True)
def block_real_openai_clients(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Default pytest must never construct a real OpenAI client."""
    if request.node.get_closest_marker("real_openai"):
        yield
        return

    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Default pytest must never make real OpenAI requests")

    monkeypatch.setattr("ai.openai_llm.AsyncOpenAI", _blocked)
    monkeypatch.setattr("ai.openai_image_generator.AsyncOpenAI", _blocked)
    monkeypatch.setattr("backend.ai.image.openai.AsyncOpenAI", _blocked)

    async def _blocked_deepseek_chat(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
        del self, payload
        raise RuntimeError("Default pytest must never make real DeepSeek requests")

    if importlib.util.find_spec("ai.llm.deepseek") is not None:
        try:
            monkeypatch.setattr("ai.llm.deepseek.DeepSeekTransport.chat", _blocked_deepseek_chat)
        except (ImportError, AttributeError):
            pass
    yield


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    return test_settings(tmp_path)


def make_graph_client(settings: Settings, handler) -> InstagramMediaService:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return InstagramMediaService(settings, client=http)


def instagram_mock_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path.endswith("/media"):
        return httpx.Response(200, json={"id": "container-1"})
    if request.method == "POST" and path.endswith("/media_publish"):
        return httpx.Response(200, json={"id": "media-1"})
    if request.method == "GET" and path.endswith("/container-1"):
        return httpx.Response(200, json={"id": "container-1", "status_code": "FINISHED"})
    if request.method == "GET" and path.endswith("/media-1"):
        return httpx.Response(200, json={"id": "media-1", "media_type": "IMAGE"})
    return httpx.Response(400, json={"error": {"message": "unexpected request", "code": 100}})

