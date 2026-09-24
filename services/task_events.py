"""Ordered pipeline task events with correlation and request ids.

Observation payloads are redacted before they are logged or stored.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from services.logging import log_step, redact_value
from services.metrics import PipelineMetrics, pipeline_metrics

logger = logging.getLogger(__name__)


class PipelineEventName(str, Enum):
    MCP_CONTEXT_FETCHED = "MCP_CONTEXT_FETCHED"
    CONTENT_PLAN_CREATED = "CONTENT_PLAN_CREATED"
    IMAGE_GENERATION_STARTED = "IMAGE_GENERATION_STARTED"
    IMAGE_GENERATED = "IMAGE_GENERATED"
    IMAGE_QA_STARTED = "IMAGE_QA_STARTED"
    IMAGE_QA_PASSED = "IMAGE_QA_PASSED"
    IMAGE_QA_FAILED = "IMAGE_QA_FAILED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    INSTAGRAM_PUBLISH_STARTED = "INSTAGRAM_PUBLISH_STARTED"
    INSTAGRAM_PUBLISHED = "INSTAGRAM_PUBLISHED"
    INSTAGRAM_VERIFIED = "INSTAGRAM_VERIFIED"


class PipelineEvent(BaseModel):
    name: str
    task_id: str
    correlation_id: str
    request_id: str
    user_id: str | None = None
    status: str = "success"
    sequence: int
    timestamp: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


class TaskEventLog:
    """One scheduled pipeline run. Sequence is the order tests and the DB should follow."""

    def __init__(
        self,
        *,
        task_id: str | None = None,
        correlation_id: str | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        session: Any | None = None,
        metrics: PipelineMetrics | None = None,
    ) -> None:
        self.task_id = task_id or str(uuid4())
        self.correlation_id = correlation_id or str(uuid4())
        self.request_id = request_id or self.correlation_id
        self.user_id = user_id
        self.session = session
        self.metrics = metrics or pipeline_metrics
        self.events: list[PipelineEvent] = []
        self._sequence = 0
        self._task_ready = False

    def names(self) -> list[str]:
        return [event.name for event in self.events]

    def has(self, name: str) -> bool:
        return name in self.names()

    def record(self, name: str, *, status: str = "success", **detail: Any) -> PipelineEvent:
        self._sequence += 1
        clean = redact_value(detail)
        if not isinstance(clean, dict):
            clean = {}
        event = PipelineEvent(
            name=str(name),
            task_id=self.task_id,
            correlation_id=self.correlation_id,
            request_id=self.request_id,
            user_id=self.user_id,
            status=status,
            sequence=self._sequence,
            timestamp=datetime.now(timezone.utc) + timedelta(microseconds=self._sequence),
            detail=clean,
        )
        self.events.append(event)
        self.metrics.increment(str(name).lower())
        if status != "success":
            self.metrics.increment(f"{str(name).lower()}_{status}")
        log_step(
            logger,
            event=event.name,
            task_id=self.task_id,
            request_id=self.request_id,
            status=status,
            correlation_id=self.correlation_id,
            step=event.name,
        )
        self._persist(event)
        return event

    def _persist(self, event: PipelineEvent) -> None:
        if self.session is None:
            return
        try:
            from db.enums import TaskTrigger, TaskType
            from db.repositories import AgentEventRepository, AgentTaskRepository
            from db.schemas import AgentEventCreate

            if not self._task_ready:
                AgentTaskRepository(self.session).upsert(
                    id=self.task_id,
                    user_id=self.user_id,
                    task_type=TaskType.CONTENT_JOB,
                    trigger=TaskTrigger.SCHEDULER,
                    status="running",
                    current_step=event.name,
                    state_json={
                        "correlation_id": self.correlation_id,
                        "request_id": self.request_id,
                    },
                )
                self._task_ready = True
            else:
                task = AgentTaskRepository(self.session).get(self.task_id)
                if task is not None:
                    task.current_step = event.name
                    task.status = "running"
            AgentEventRepository(self.session).append(
                AgentEventCreate(
                    task_id=self.task_id,
                    from_state=None,
                    to_state=event.name,
                    tool=event.name,
                    result=event.status,
                    observation={
                        "correlation_id": self.correlation_id,
                        "request_id": self.request_id,
                        "sequence": event.sequence,
                        "status": event.status,
                        "detail": event.detail,
                    },
                    timestamp=event.timestamp,
                )
            )
        except Exception:
            logger.warning("Pipeline event was not stored", extra={"task_id": self.task_id, "step": event.name})
