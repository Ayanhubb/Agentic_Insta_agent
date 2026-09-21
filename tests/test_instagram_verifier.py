"""Verifier tests. HTTP 200 is not treated as proof of publication."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from models.errors import AppError, ErrorCode
from models.state import AgentState
from tests.conftest import make_graph_client
from tools.instagram_verifier import InstagramVerifier


def _run(coro):
    return asyncio.run(coro)


def test_verification_requires_matching_media_id(tmp_settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"id": "someone-else", "media_type": "IMAGE"})

    client = make_graph_client(tmp_settings, handler)
    verifier = InstagramVerifier(tmp_settings, client)
    state = AgentState(image_path="photo.jpg", instagram_media_id="media-1")
    with pytest.raises(AppError) as exc:
        _run(verifier.execute(state))
    assert exc.value.code == ErrorCode.VERIFICATION_FAILED


def test_verification_success(tmp_settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"id": "media-1", "media_type": "IMAGE"})

    client = make_graph_client(tmp_settings, handler)
    verifier = InstagramVerifier(tmp_settings, client)
    state = AgentState(image_path="photo.jpg", instagram_media_id="media-1")
    observation = _run(verifier.execute(state))
    assert observation.success is True
    assert observation.data["verified"] is True
    assert observation.data["instagram_media_id"] == "media-1"
