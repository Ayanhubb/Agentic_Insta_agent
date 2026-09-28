"""Structured runtime errors. Messages must not carry secrets."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.sanitize import redact_text

SAFE_INTERNAL_MESSAGE = "The execution failed."


class RuntimeErrorCode(str, Enum):
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_PAUSED = "AGENT_PAUSED"
    AGENT_RETIRED = "AGENT_RETIRED"
    VERSION_NOT_PUBLISHED = "VERSION_NOT_PUBLISHED"
    DRAFT_NOT_ALLOWED = "DRAFT_NOT_ALLOWED"
    DRAFT_CANNOT_PUBLISH = "DRAFT_CANNOT_PUBLISH"
    PUBLISHED_POINTER_REQUIRED = "PUBLISHED_POINTER_REQUIRED"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    WORKFLOW_NOT_PINNED = "WORKFLOW_NOT_PINNED"
    HANDLER_NOT_REGISTERED = "HANDLER_NOT_REGISTERED"
    TOOL_NOT_GRANTED = "TOOL_NOT_GRANTED"
    ROLE_NOT_GRANTED = "ROLE_NOT_GRANTED"
    KNOWLEDGE_NOT_GRANTED = "KNOWLEDGE_NOT_GRANTED"
    ASSET_NOT_GRANTED = "ASSET_NOT_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_POLICY_NOT_FOUND = "APPROVAL_POLICY_NOT_FOUND"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    STEP_FAILED = "STEP_FAILED"
    INVALID_REQUEST = "INVALID_REQUEST"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class StructuredError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: RuntimeErrorCode
    message: str
    retryable: bool = False
    step_key: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RuntimeFailure(Exception):
    """Expected execution failure with a stable code."""

    def __init__(
        self,
        code: RuntimeErrorCode,
        message: str,
        *,
        retryable: bool = False,
        step_key: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        cleaned = redact_text(message)
        super().__init__(cleaned)
        self.code = code
        self.message = cleaned
        self.retryable = retryable
        self.step_key = step_key
        self.details = details or {}

    def to_error(self, *, step_key: str | None = None) -> StructuredError:
        return StructuredError(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            step_key=self.step_key if step_key is None else step_key,
            details=dict(self.details),
        )
