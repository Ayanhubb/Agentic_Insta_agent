"""Optional live Meta tests. Skipped unless credentials are present."""

from __future__ import annotations

import os

import pytest

from config import Settings

pytestmark = pytest.mark.integration


def _credentials_available() -> bool:
    return bool(os.getenv("META_ACCESS_TOKEN") and os.getenv("INSTAGRAM_ACCOUNT_ID"))


@pytest.mark.skipif(not _credentials_available(), reason="META_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID are not set")
def test_instagram_account_is_reachable() -> None:
    import asyncio

    import httpx

    from tools.instagram_media import InstagramMediaService

    settings = Settings()
    service = InstagramMediaService(settings)

    async def _probe() -> dict:
        account_id = settings.instagram_account_id.strip()
        return await service._request(
            "GET",
            account_id,
            params={"fields": "id"},
            fallback=__import__("models.errors", fromlist=["ErrorCode"]).ErrorCode.INVALID_ACCOUNT,
            retry=False,
        )

    payload = asyncio.run(_probe())
    assert payload.get("id") == settings.instagram_account_id.strip()
    asyncio.run(service.aclose())


@pytest.mark.skipif(
    not _credentials_available() or os.getenv("RUN_INSTAGRAM_PUBLISH_TEST") != "1",
    reason="Set META_ACCESS_TOKEN, INSTAGRAM_ACCOUNT_ID, and RUN_INSTAGRAM_PUBLISH_TEST=1 to publish once",
)
def test_real_publish_is_opt_in_only() -> None:
    pytest.skip("Real publishing is performed as a single controlled manual check, not by default pytest.")
