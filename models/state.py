"""Typed agent execution state, transitions, and UI trace."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from models.errors import ErrorBody, ErrorDetail, OperationCertainty
from models.observations import Observation


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


utc_now = utcnow


class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    VALIDATING = "validating"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    CREATING_MEDIA = "creating_media"
    PUBLISHING = "publishing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


STATUS_TO_STEP: dict[TaskStatus, str] = {
    TaskStatus.PLANNING: "plan",
    TaskStatus.VALIDATING: "validate_image",
    TaskStatus.PREPARING: "prepare_image",
    TaskStatus.UPLOADING: "upload_image",
    TaskStatus.CREATING_MEDIA: "create_instagram_media",
    TaskStatus.PUBLISHING: "publish_instagram_media",
    TaskStatus.VERIFYING: "verify_publication",
    TaskStatus.COMPLETED: "completed",
    TaskStatus.FAILED: "failed",
}

TOOL_CLASS_NAMES: dict[str, str] = {
    "validate_image": "ImageValidator",
    "prepare_image": "ImagePreparer",
    "upload_image": "ImageStorage",
    "create_instagram_media": "InstagramMediaCreator",
    "publish_instagram_media": "InstagramPublisher",
    "verify_publication": "InstagramVerifier",
    "Planner": "Planner",
}

TOOL_TO_STATUS: dict[str, TaskStatus] = {
    "validate_image": TaskStatus.VALIDATING,
    "prepare_image": TaskStatus.PREPARING,
    "upload_image": TaskStatus.UPLOADING,
    "create_instagram_media": TaskStatus.CREATING_MEDIA,
    "publish_instagram_media": TaskStatus.PUBLISHING,
    "verify_publication": TaskStatus.VERIFYING,
}

UI_STEP_LABELS: dict[str, str] = {
    "validate_image": "Image validated",
    "prepare_image": "Image prepared",
    "upload_image": "Image uploaded",
    "create_instagram_media": "Instagram media created",
    "publish_instagram_media": "Instagram media published",
    "verify_publication": "Publication verified",
}


class Transition(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    source: TaskStatus = Field(alias="from")
    target: TaskStatus = Field(alias="to")
    tool: str | None = None
    result: str | None = None
    model_config = ConfigDict(populate_by_name=True)

    @field_serializer("source", "target")
    def _serialize_status(self, value: TaskStatus) -> str:
        return value.name

    def model_dump_record(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


class ExecutionEvent(BaseModel):
    step: str
    status: Literal["success", "failed", "skipped"]
    message: str = ""
    timestamp: datetime = Field(default_factory=utcnow)
    data: dict[str, Any] = Field(default_factory=dict)
    tool: str | None = None
    result: str | None = None


class TraceStep(BaseModel):
    step: int
    tool: str
    purpose: str
    status: Literal["pending", "running", "success", "failed", "skipped"]
    duration_ms: int | None = None
    error_code: str | None = None
    decision: str | None = None
    observation_summary: dict[str, Any] = Field(default_factory=dict)


class AgentState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    task_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str | None = None
    user_id: str | None = None
    instagram_account_pk: str | None = None
    instagram_account_id: str | None = None
    generated_image_id: str | None = None
    content_job_id: str | None = None
    source: str | None = None
    caption: str | None = None
    image_path: str | None = None
    original_filename: str | None = None
    prepared_image_path: str | None = None
    image_url: str | None = None
    storage_key: str | None = None
    platform: Literal["instagram"] = "instagram"
    current_step: str | None = "pending"
    status: TaskStatus = TaskStatus.PENDING
    execution_history: list[Transition] = Field(default_factory=list)
    execution_trace: list[TraceStep] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list)
    instagram_container_id: str | None = None
    instagram_media_id: str | None = None
    permalink: str | None = None
    error: ErrorBody | ErrorDetail | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    publish_certainty: OperationCertainty | None = None
    skip_publish: bool = False
    force_next_tool: str | None = None
    completed_tools: list[str] = Field(default_factory=list)
    state_transitions: list[Transition] = Field(default_factory=list)
    temp_paths: list[str] = Field(default_factory=list)
    publication_source: str | None = None
    post_type: str | None = None
    scheduled_date: date | None = None
    festival_campaign_id: str | None = None
    instagram_account_ref: str | None = None
    db_post_id: str | None = None
    db_task_id: str | None = None
    verified: bool = False

    def touch(self) -> "AgentState":
        self.updated_at = utcnow()
        return self

    def record_transition(
        self,
        target: TaskStatus,
        *,
        tool: str | None = None,
        result: str | None = None,
    ) -> Transition:
        display_tool = TOOL_CLASS_NAMES.get(tool, tool) if tool else tool
        transition = Transition(source=self.status, target=target, tool=display_tool, result=result)
        self.execution_history.append(transition)
        self.status = target
        if target in STATUS_TO_STEP:
            self.current_step = STATUS_TO_STEP[target]
        self.touch()
        if target in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            self.completed_at = utcnow()
        return transition

    def apply_observation(self, observation: Observation) -> None:
        if not observation.success or not observation.data:
            return
        data = observation.data
        if "prepared_image_path" in data:
            self.prepared_image_path = str(data["prepared_image_path"])
        if "image_path" in data and data["image_path"]:
            # Preparer may replace the working path with a JPEG derivative.
            self.prepared_image_path = self.prepared_image_path or str(data["image_path"])
        if "image_url" in data:
            self.image_url = str(data["image_url"])
        if "storage_key" in data:
            self.storage_key = str(data["storage_key"])
        if "instagram_container_id" in data:
            self.instagram_container_id = str(data["instagram_container_id"])
        if "instagram_media_id" in data:
            self.instagram_media_id = str(data["instagram_media_id"])
        if "permalink" in data and data["permalink"]:
            self.permalink = str(data["permalink"])

    def public_steps(self) -> list[dict[str, Any]]:
        if self.execution_trace:
            return [
                {
                    "step": item.step,
                    "tool": item.tool,
                    "label": UI_STEP_LABELS.get(item.tool, item.purpose),
                    "status": item.status,
                    "duration_ms": item.duration_ms,
                }
                for item in self.execution_trace
            ]
        steps: list[dict[str, Any]] = []
        for index, event in enumerate(self.execution_history, start=1):
            tool_name = event.tool or ""
            steps.append(
                {
                    "step": index,
                    "tool": tool_name,
                    "label": UI_STEP_LABELS.get(tool_name, tool_name),
                    "status": event.result or "unknown",
                }
            )
        return steps

    def public_trace(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "steps": [
                {"step": item.step, "tool": item.tool, "status": item.status}
                for item in self.execution_trace
            ],
        }
