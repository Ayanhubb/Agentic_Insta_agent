"""Real Instagram publishing is never part of the default pytest run."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.real_instagram


@pytest.mark.skipif(
    not (os.getenv("META_ACCESS_TOKEN") and os.getenv("INSTAGRAM_ACCOUNT_ID")),
    reason="Meta credentials are not configured",
)
def test_real_instagram_is_opt_in() -> None:
    pytest.skip("Run scripts/real_publish_once.py for the single controlled live test.")
