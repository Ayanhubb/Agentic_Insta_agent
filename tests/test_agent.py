from pathlib import Path

import pytest

from agent.agent import InstagramAgent
from models.state import AgentState, TaskStatus
from tests.helpers import DummyInstagramClient, no_sleep, write_jpeg
from config import Settings


@pytest.mark.asyncio
async def test_agent_completes_with_real_tools_and_fake_instagram(
    tmp_settings: Settings,
    tmp_path: Path,
) -> None:
    image_path = write_jpeg(tmp_path / "input" / "photo.jpg")
    agent = InstagramAgent(
        tmp_settings,
        instagram_client=DummyInstagramClient(),  # type: ignore[arg-type]
        sleeper=no_sleep,
    )
    state = await agent.run(AgentState(task_id="task-success", image_path=str(image_path)))
    assert state.status == TaskStatus.COMPLETED
    assert state.instagram_container_id == "container-1"
    assert state.instagram_media_id == "media-1"
    assert state.image_url is not None
    assert state.image_url.startswith("https://")
    assert [item.tool for item in state.execution_trace] == [
        "validate_image",
        "prepare_image",
        "upload_image",
        "create_instagram_media",
        "publish_instagram_media",
        "verify_publication",
    ]
    assert all(item.status == "success" for item in state.execution_trace)
