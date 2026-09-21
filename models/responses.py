"""API response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from models.state import AgentState, TaskStatus, TraceStep


class PublicError(BaseModel):
    code: str
    message: str


class PublishResponse(BaseModel):
    success: bool
    task_id: str
    platform: Literal["instagram"] = "instagram"
    status: TaskStatus
    instagram_media_id: str | None = None
    message: str | None = None
    error: PublicError | None = None
    steps: list[TraceStep] = Field(default_factory=list)

    @classmethod
    def from_state(cls, state: AgentState, message: str | None = None) -> PublishResponse:
        success = state.status == TaskStatus.COMPLETED
        if message is None:
            if success:
                message = "Image published successfully"
            elif state.error is not None:
                message = None
            else:
                message = "Image publishing did not complete"
        return cls(
            success=success,
            task_id=state.task_id,
            platform=state.platform,
            status=state.status,
            instagram_media_id=state.instagram_media_id,
            message=message,
            error=PublicError(code=state.error.code.value, message=state.error.message)
            if state.error
            else None,
            steps=state.execution_trace,
        )


class TaskStatusResponse(BaseModel):
    success: bool
    task_id: str
    request_id: str | None = None
    platform: Literal["instagram"] = "instagram"
    status: TaskStatus
    current_step: str | None = None
    instagram_media_id: str | None = None
    image_url: str | None = None
    message: str | None = None
    error: PublicError | None = None
    steps: list[TraceStep] = Field(default_factory=list)
    execution_trace: list[TraceStep] = Field(default_factory=list)

    @classmethod
    def from_state(cls, state: AgentState) -> TaskStatusResponse:
        success = state.status == TaskStatus.COMPLETED
        return cls(
            success=success,
            task_id=state.task_id,
            request_id=state.request_id,
            platform=state.platform,
            status=state.status,
            current_step=state.current_step,
            instagram_media_id=state.instagram_media_id,
            image_url=state.image_url,
            message="Image published successfully" if success else None,
            error=PublicError(code=state.error.code.value, message=state.error.message)
            if state.error
            else None,
            steps=state.execution_trace,
            execution_trace=state.execution_trace,
        )
