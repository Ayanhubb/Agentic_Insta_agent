"""State-machine helpers for the Instagram agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from errors import ErrorDetail
from models.state import AgentState, ExecutionEvent, TaskStatus

STEP_TO_STATUS: Mapping[str, TaskStatus] = {
    "validate_image": TaskStatus.VALIDATING,
    "prepare_image": TaskStatus.PREPARING,
    "upload_image": TaskStatus.UPLOADING,
    "create_instagram_media": TaskStatus.CREATING_MEDIA,
    "publish_instagram_media": TaskStatus.PUBLISHING,
    "verify_publication": TaskStatus.VERIFYING,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def touch(state: AgentState) -> AgentState:
    state.updated_at = utc_now()
    return state


def begin_planning(state: AgentState) -> AgentState:
    state.status = TaskStatus.PLANNING
    state.current_step = "planning"
    return touch(state)


def begin_step(state: AgentState, step: str) -> AgentState:
    state.current_step = step
    state.status = STEP_TO_STATUS.get(step, state.status)
    return touch(state)


def apply_updates(state: AgentState, updates: dict[str, Any]) -> AgentState:
    for key, value in updates.items():
        if key in state.model_fields:
            setattr(state, key, value)
    return touch(state)


def record_success(
    state: AgentState,
    step: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> AgentState:
    state.execution_history.append(
        ExecutionEvent(step=step, status="success", message=message, data=data or {})
    )
    return touch(state)


def record_failure(
    state: AgentState,
    step: str,
    error: ErrorDetail,
) -> AgentState:
    state.status = TaskStatus.FAILED
    state.error = error
    state.execution_history.append(
        ExecutionEvent(
            step=step,
            status="failed",
            message=error.message,
            data={"code": error.code},
        )
    )
    state.completed_at = utc_now()
    return touch(state)


def mark_completed(state: AgentState) -> AgentState:
    state.status = TaskStatus.COMPLETED
    state.current_step = "completed"
    state.completed_at = utc_now()
    return touch(state)
