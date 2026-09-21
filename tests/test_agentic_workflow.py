"""Agentic workflow tests: plan → tools → observations → decisions → result."""

from __future__ import annotations

import pytest

from agent.planner import DeterministicPlanner, Plan, PlannedAction
from config import Settings
from models.errors import AppError, ErrorCode, OperationCertainty
from models.observations import Observation
from models.state import AgentState, TaskStatus
from tests.helpers import ScriptedTool, default_success_tools, make_agent


def _replace(tools: list[ScriptedTool], name: str, replacement: ScriptedTool) -> list[ScriptedTool]:
    return [replacement if tool.name == name else tool for tool in tools]


@pytest.mark.asyncio
async def test_successful_complete_workflow(tmp_settings: Settings) -> None:
    tools = default_success_tools()
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-success", image_path="photo.jpg"))

    assert state.status == TaskStatus.COMPLETED
    assert state.instagram_media_id == "media-1"
    assert [item.tool for item in state.execution_trace] == [
        "validate_image",
        "prepare_image",
        "upload_image",
        "create_instagram_media",
        "publish_instagram_media",
        "verify_publication",
    ]
    assert all(item.status == "success" for item in state.execution_trace)
    assert [event.model_dump(by_alias=True)["to"] for event in state.execution_history] == [
        "PLANNING",
        "VALIDATING",
        "PREPARING",
        "UPLOADING",
        "CREATING_MEDIA",
        "PUBLISHING",
        "VERIFYING",
        "COMPLETED",
    ]
    assert state.public_trace()["task_id"] == "task-success"
    assert [step["status"] for step in state.public_trace()["steps"]] == ["success"] * 6
    assert all(tool.calls == 1 for tool in tools)


@pytest.mark.asyncio
async def test_validation_failure_stops_immediately(tmp_settings: Settings) -> None:
    tools = _replace(
        default_success_tools(),
        "validate_image",
        ScriptedTool(
            "validate_image",
            "Validate uploaded image",
            [AppError(ErrorCode.IMAGE_VALIDATION_FAILED, "Image is not valid.")],
        ),
    )
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-invalid", image_path="bad.jpg"))

    assert state.status == TaskStatus.FAILED
    assert state.error is not None
    assert state.error.code == ErrorCode.IMAGE_VALIDATION_FAILED
    assert tools[0].calls == 1
    assert tools[1].calls == 0
    assert tools[4].calls == 0
    assert state.instagram_media_id is None


@pytest.mark.asyncio
async def test_preparation_failure(tmp_settings: Settings) -> None:
    tools = _replace(
        default_success_tools(),
        "prepare_image",
        ScriptedTool(
            "prepare_image",
            "Prepare image for publishing",
            [AppError(ErrorCode.PREPARATION_FAILED, "Could not normalize the image.")],
        ),
    )
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-prep", image_path="photo.jpg"))
    assert state.status == TaskStatus.FAILED
    assert state.error is not None
    assert state.error.code == ErrorCode.PREPARATION_FAILED
    assert tools[1].calls == 1
    assert tools[2].calls == 0


@pytest.mark.asyncio
async def test_storage_temporary_failure_retries_then_succeeds(tmp_settings: Settings) -> None:
    storage = ScriptedTool(
        "upload_image",
        "Create publicly accessible image URL",
        [
            AppError(
                ErrorCode.STORAGE_TEMPORARY_FAILURE,
                "Temporary storage is unavailable.",
                retryable=True,
            ),
            Observation(
                success=True,
                tool="upload_image",
                data={"image_url": "https://cdn.example.test/photo.jpg"},
            ),
        ],
    )
    tools = _replace(default_success_tools(), "upload_image", storage)
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-storage", image_path="photo.jpg"))
    assert state.status == TaskStatus.COMPLETED
    assert storage.calls == 2
    assert state.image_url == "https://cdn.example.test/photo.jpg"


@pytest.mark.asyncio
async def test_storage_failure_exhausts_retries(tmp_settings: Settings) -> None:
    storage = ScriptedTool(
        "upload_image",
        "Create publicly accessible image URL",
        [
            AppError(
                ErrorCode.STORAGE_TEMPORARY_FAILURE,
                "Temporary storage is unavailable.",
                retryable=True,
            )
        ],
    )
    tools = _replace(default_success_tools(), "upload_image", storage)
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-storage-fail", image_path="photo.jpg"))
    assert state.status == TaskStatus.FAILED
    assert storage.calls == tmp_settings.storage_retry_attempts
    assert tools[3].calls == 0


@pytest.mark.asyncio
async def test_instagram_api_failure(tmp_settings: Settings) -> None:
    tools = _replace(
        default_success_tools(),
        "create_instagram_media",
        ScriptedTool(
            "create_instagram_media",
            "Create Instagram media container",
            [AppError(ErrorCode.MEDIA_CREATION_FAILED, "Instagram rejected the container.")],
        ),
    )
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-api", image_path="photo.jpg"))
    assert state.status == TaskStatus.FAILED
    assert state.error is not None
    assert state.error.code == ErrorCode.MEDIA_CREATION_FAILED
    assert tools[4].calls == 0


@pytest.mark.asyncio
async def test_permission_error_stops(tmp_settings: Settings) -> None:
    tools = _replace(
        default_success_tools(),
        "create_instagram_media",
        ScriptedTool(
            "create_instagram_media",
            "Create Instagram media container",
            [AppError(ErrorCode.PERMISSION_ERROR, "Instagram permission denied for content publishing.")],
        ),
    )
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-perm", image_path="photo.jpg"))
    assert state.status == TaskStatus.FAILED
    assert state.error is not None
    assert state.error.code == ErrorCode.PERMISSION_ERROR
    assert tools[4].calls == 0
    tools = _replace(
        default_success_tools(),
        "create_instagram_media",
        ScriptedTool(
            "create_instagram_media",
            "Create Instagram media container",
            [AppError(ErrorCode.AUTHENTICATION_ERROR, "Instagram authentication failed.")],
        ),
    )
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-auth", image_path="photo.jpg"))
    assert state.status == TaskStatus.FAILED
    assert state.error is not None
    assert state.error.code == ErrorCode.AUTHENTICATION_ERROR
    assert "META_ACCESS_TOKEN" not in (state.error.message or "")


@pytest.mark.asyncio
async def test_rate_limit_retries_when_retry_after_is_safe(tmp_settings: Settings) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [
            AppError(
                ErrorCode.RATE_LIMITED,
                "Instagram rate limit reached.",
                retryable=True,
                retry_after_seconds=0.01,
                certainty=OperationCertainty.FAILED,
            ),
            Observation(
                success=True,
                tool="publish_instagram_media",
                data={"instagram_media_id": "media-1"},
            ),
        ],
    )
    tools = _replace(default_success_tools(), "publish_instagram_media", publisher)
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-rate", image_path="photo.jpg"))
    assert state.status == TaskStatus.COMPLETED
    assert publisher.calls == 2
    assert tools[5].calls == 1


@pytest.mark.asyncio
async def test_rate_limit_stops_without_safe_retry_metadata(tmp_settings: Settings) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [AppError(ErrorCode.RATE_LIMITED, "Instagram rate limit reached.")],
    )
    tools = _replace(default_success_tools(), "publish_instagram_media", publisher)
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-rate-stop", image_path="photo.jpg"))
    assert state.status == TaskStatus.FAILED
    assert publisher.calls == 1
    assert tools[5].calls == 0


@pytest.mark.asyncio
async def test_timeout_does_not_republish(tmp_settings: Settings) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [
            AppError(
                ErrorCode.TIMEOUT,
                "Instagram API timed out.",
                certainty=OperationCertainty.UNKNOWN,
            )
        ],
    )
    verifier = ScriptedTool(
        "verify_publication",
        "Verify publication",
        [AppError(ErrorCode.VERIFICATION_FAILED, "No Instagram media ID is available to verify.")],
    )
    tools = _replace(default_success_tools(), "publish_instagram_media", publisher)
    tools = _replace(tools, "verify_publication", verifier)
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-timeout", image_path="photo.jpg"))
    assert publisher.calls == 1
    assert state.status == TaskStatus.FAILED
    assert state.error is not None
    assert state.error.code == ErrorCode.AMBIGUOUS_PUBLICATION
    assert state.skip_publish is True


@pytest.mark.asyncio
async def test_ambiguous_publish_verifies_first(tmp_settings: Settings) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [
            AppError(
                ErrorCode.NETWORK_ERROR,
                "Unable to reach the Instagram API.",
                certainty=OperationCertainty.UNKNOWN,
            )
        ],
    )
    verifier = ScriptedTool(
        "verify_publication",
        "Verify publication",
        [
            Observation(
                success=True,
                tool="verify_publication",
                data={"verified": True, "instagram_media_id": "media-recovered", "ambiguous_resolved": True},
            )
        ],
    )
    tools = _replace(default_success_tools(), "publish_instagram_media", publisher)
    tools = _replace(tools, "verify_publication", verifier)
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-ambiguous", image_path="photo.jpg"))
    assert publisher.calls == 1
    assert verifier.calls == 1
    assert state.status == TaskStatus.COMPLETED
    assert state.instagram_media_id == "media-recovered"
    assert state.execution_trace[4].decision == "verify_first"


@pytest.mark.asyncio
async def test_verification_retry_does_not_publish_again(tmp_settings: Settings) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [Observation(success=True, tool="publish_instagram_media", data={"instagram_media_id": "media-1"})],
    )
    verifier = ScriptedTool(
        "verify_publication",
        "Verify publication",
        [
            AppError(ErrorCode.VERIFICATION_DELAY, "Publication is not yet visible.", retryable=True),
            Observation(
                success=True,
                tool="verify_publication",
                data={"verified": True, "instagram_media_id": "media-1"},
            ),
        ],
    )
    tools = _replace(default_success_tools(), "publish_instagram_media", publisher)
    tools = _replace(tools, "verify_publication", verifier)
    agent = make_agent(tmp_settings, tools)
    state = await agent.run(AgentState(task_id="task-verify-retry", image_path="photo.jpg"))
    assert state.status == TaskStatus.COMPLETED
    assert publisher.calls == 1
    assert verifier.calls == 2


@pytest.mark.asyncio
async def test_duplicate_publish_is_skipped_when_media_id_exists(tmp_settings: Settings) -> None:
    publisher = ScriptedTool(
        "publish_instagram_media",
        "Publish image",
        [Observation(success=True, tool="publish_instagram_media", data={"instagram_media_id": "media-1"})],
    )
    tools = _replace(default_success_tools(), "publish_instagram_media", publisher)

    class DoublePublishPlanner(DeterministicPlanner):
        def create_plan(self, task):  # type: ignore[no-untyped-def]
            plan = super().create_plan(task)
            extra = PlannedAction(tool="publish_instagram_media", purpose="Publish image")
            return Plan(actions=plan.actions + [extra])

    agent = make_agent(tmp_settings, tools)
    agent._planner = DoublePublishPlanner()  # noqa: SLF001
    state = await agent.run(AgentState(task_id="task-dup", image_path="photo.jpg"))
    assert state.status == TaskStatus.COMPLETED
    assert publisher.calls == 1
