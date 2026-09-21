import json
import logging

import pytest

from agent.executor import Executor
from models.errors import AppError, ErrorCode, SAFE_INTERNAL_MESSAGE
from models.state import AgentState
from tests.helpers import ScriptedTool


@pytest.mark.asyncio
async def test_executor_hides_unexpected_exceptions() -> None:
    class Boom(ScriptedTool):
        async def execute(self, state):  # type: ignore[no-untyped-def]
            raise RuntimeError("secret-token-should-not-leak")

    executor = Executor()
    observation = await executor.execute(Boom("validate_image", "x", []), AgentState(task_id="t1", image_path="photo.jpg"))
    assert observation.success is False
    assert observation.error is not None
    assert observation.error.code == ErrorCode.INTERNAL_ERROR
    assert "secret-token" not in observation.error.message
    assert "RuntimeError" not in observation.error.message


@pytest.mark.asyncio
async def test_executor_preserves_structured_app_errors() -> None:
    tool = ScriptedTool(
        "validate_image",
        "Validate uploaded image",
        [AppError(ErrorCode.INVALID_IMAGE, "The provided image could not be found.")],
    )
    observation = await Executor().execute(tool, AgentState(task_id="t1", image_path="photo.jpg"))
    assert observation.success is False
    assert observation.error is not None
    assert observation.error.code == ErrorCode.INVALID_IMAGE
    assert "could not be found" in observation.error.message


def test_error_body_is_json_safe() -> None:
    error = AppError(ErrorCode.AUTHENTICATION_ERROR, "Instagram authentication failed.")
    payload = json.dumps(error.to_body().model_dump(mode="json"))
    assert "test-token" not in payload
    assert ErrorCode.AUTHENTICATION_ERROR.value in payload
