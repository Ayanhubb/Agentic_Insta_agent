"""Execute a single allowlisted tool and convert results into observations."""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable

from models.errors import AppError, ErrorCode, OperationCertainty
from models.observations import Observation
from models.state import AgentState
from services.logging import log_step
from tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

StateCallback = Callable[[AgentState], Awaitable[None]]


class Executor:
    def __init__(self, on_change: StateCallback | None = None) -> None:
        self._on_change = on_change

    async def execute(self, tool: Tool, state: AgentState) -> Observation:
        started = time.perf_counter()
        try:
            raw = tool.execute(state)
            if inspect.isawaitable(raw):
                raw = await raw
            if isinstance(raw, Observation):
                observation = raw
            elif isinstance(raw, ToolResult):
                observation = raw.to_observation(tool.name)
            else:
                raise AppError(ErrorCode.INTERNAL_ERROR, "Tool returned an unknown result type.")
        except AppError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            observation = Observation(
                success=False,
                tool=tool.name,
                error=exc.to_body(),
                duration_ms=duration_ms,
            )
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("Unhandled tool failure", extra={"task_id": state.task_id, "tool": tool.name})
            observation = Observation(
                success=False,
                tool=tool.name,
                error=AppError(
                    ErrorCode.INTERNAL_ERROR,
                    "An internal tool error occurred.",
                    http_status=500,
                    certainty=OperationCertainty.UNKNOWN
                    if tool.name == "publish_instagram_media"
                    else OperationCertainty.FAILED,
                ).to_body(),
                duration_ms=duration_ms,
            )
        else:
            observation.duration_ms = int((time.perf_counter() - started) * 1000)

        log_step(
            logger,
            event="TOOL_EXECUTED",
            task_id=state.task_id,
            request_id=getattr(state, "request_id", None),
            step=tool.name,
            tool=tool.name,
            status="success" if observation.success else "failed",
            duration_ms=observation.duration_ms,
            error_code=observation.error.code.value if observation.error else None,
        )
        return observation
