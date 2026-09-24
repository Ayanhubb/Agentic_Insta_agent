"""Exactly one optional live Instagram publish.

This script is intentionally separate from pytest. It publishes only when
META_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID are configured, and only once.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.agent import InstagramAgent
from config import Settings
from models.state import AgentState, TaskStatus


def _sample_image(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1080, 1080), (32, 96, 160)).save(path, format="JPEG", quality=90)
    return path


async def main() -> int:
    settings = Settings.from_env()
    settings.ensure_directories()
    if not settings.legacy_environment_credentials_allowed():
        print(
            "REAL TEST NOT RUN — set APP_ENV=development and INSTAGRAM_LEGACY_ENV_FALLBACK=true "
            "with META_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID. Production does not use those variables."
        )
        return 0
    image = _sample_image(settings.temp_dir / "real-publish-once.jpg")
    agent = InstagramAgent(settings)
    state = await agent.run(AgentState(image_path=str(image)))
    if state.status == TaskStatus.COMPLETED and state.instagram_media_id:
        print("REAL TEST SUCCESSFUL")
        print(f"instagram_media_id={state.instagram_media_id}")
        return 0
    print("REAL TEST FAILED")
    if state.error:
        print(state.error.code.value)
        print(state.error.message)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
