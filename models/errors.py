"""Structured application errors. Messages must never include secrets."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_FILE = "INVALID_FILE"
    INVALID_IMAGE = "INVALID_FILE"
    CORRUPTED_IMAGE = "CORRUPTED_IMAGE"
    IMAGE_CORRUPTED = "CORRUPTED_IMAGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    IMAGE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_DIMENSIONS = "INVALID_DIMENSIONS"
    IMAGE_TOO_SMALL = "INVALID_DIMENSIONS"
    IMAGE_DIMENSIONS_INVALID = "INVALID_DIMENSIONS"
    IMAGE_VALIDATION_FAILED = "IMAGE_VALIDATION_FAILED"
    VALIDATION_FAILED = "IMAGE_VALIDATION_FAILED"
    INVALID_MIME = "UNSUPPORTED_FORMAT"
    GENERATION_FAILED = "GENERATION_FAILED"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    STORAGE_FAILED = "STORAGE_FAILURE"
    STORAGE_TEMPORARY_FAILURE = "STORAGE_TEMPORARY_FAILURE"
    STORAGE_NOT_CONFIGURED = "STORAGE_NOT_CONFIGURED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INSTAGRAM_NOT_CONFIGURED = "CONFIGURATION_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    PERMISSION_DENIED = "PERMISSION_ERROR"
    INVALID_ACCOUNT = "INVALID_ACCOUNT"
    INVALID_IMAGE_URL = "INVALID_IMAGE_URL"
    MEDIA_CREATION_FAILED = "MEDIA_CREATION_FAILED"
    MEDIA_PUBLISH_FAILED = "MEDIA_PUBLISH_FAILED"
    PUBLISH_FAILED = "MEDIA_PUBLISH_FAILED"
    MEDIA_CONTAINER_NOT_READY = "MEDIA_CREATION_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    API_ERROR = "API_ERROR"
    INSTAGRAM_API_ERROR = "API_ERROR"
    TIMEOUT = "TIMEOUT"
    INSTAGRAM_TIMEOUT = "TIMEOUT"
    AMBIGUOUS_PUBLICATION = "AMBIGUOUS_PUBLICATION"
    INSTAGRAM_NOT_CONNECTED = "INSTAGRAM_NOT_CONNECTED"
    DUPLICATE_PUBLICATION = "DUPLICATE_PUBLICATION"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_DELAY = "VERIFICATION_DELAY"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    OPENAI_CONFIGURATION_ERROR = "OPENAI_CONFIGURATION_ERROR"
    OPENAI_API_ERROR = "OPENAI_API_ERROR"
    OPENAI_INVALID_RESPONSE = "OPENAI_INVALID_RESPONSE"
    DEEPSEEK_CONFIGURATION_ERROR = "DEEPSEEK_CONFIGURATION_ERROR"
    DEEPSEEK_API_ERROR = "DEEPSEEK_API_ERROR"
    DEEPSEEK_INVALID_RESPONSE = "DEEPSEEK_INVALID_RESPONSE"
    DEEPSEEK_TIMEOUT = "DEEPSEEK_TIMEOUT"
    DEEPSEEK_RATE_LIMITED = "DEEPSEEK_RATE_LIMITED"
    DEEPSEEK_UNAVAILABLE = "DEEPSEEK_UNAVAILABLE"
    VISION_PROVIDER_ERROR = "VISION_PROVIDER_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    MUST_CHANGE_PASSWORD = "MUST_CHANGE_PASSWORD"
    INACTIVE_USER = "AUTHENTICATION_ERROR"
    CONTENT_PROFILE_MISSING = "CONTENT_PROFILE_MISSING"
    CONTENT_LLM_FAILED = "CONTENT_LLM_FAILED"
    CONTENT_INVALID_PLAN = "CONTENT_INVALID_PLAN"
    CONTENT_DIVERSITY_REJECTED = "CONTENT_DIVERSITY_REJECTED"
    CONTENT_IMAGE_FAILED = "CONTENT_IMAGE_FAILED"
    CONTENT_AUTOMATION_DISABLED = "CONTENT_AUTOMATION_DISABLED"
    CONTENT_POLICY_VIOLATION = "CONTENT_POLICY_VIOLATION"
    CONTENT_QA_FAILED = "CONTENT_QA_FAILED"
    CANVA_DISABLED = "CANVA_DISABLED"
    CANVA_NOT_CONNECTED = "CANVA_NOT_CONNECTED"
    CANVA_AUTHORIZATION_FAILED = "CANVA_AUTHORIZATION_FAILED"
    CANVA_TIMEOUT = "CANVA_TIMEOUT"
    CANVA_UNAVAILABLE = "CANVA_UNAVAILABLE"
    CANVA_CAPABILITY_UNAVAILABLE = "CANVA_CAPABILITY_UNAVAILABLE"


class OperationCertainty(str, Enum):
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    UNKNOWN = "unknown"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    retryable: bool = False
    certainty: OperationCertainty = OperationCertainty.FAILED
    details: dict[str, Any] = Field(default_factory=dict)

    def public(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message}


class ErrorDetail(ErrorBody):
    """Alias used by the original executor/API surface."""


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        certainty: OperationCertainty = OperationCertainty.FAILED,
        retry_after_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code if isinstance(code, ErrorCode) else ErrorCode(code)
        self.message = message
        self.retryable = retryable
        self.certainty = certainty
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}
        self.http_status = http_status if http_status is not None else http_status_for(self.code)

    def to_body(self) -> ErrorBody:
        details = dict(self.details)
        if self.retry_after_seconds is not None:
            details["retry_after_seconds"] = self.retry_after_seconds
        return ErrorBody(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            certainty=self.certainty,
            details=details,
        )

    def to_detail(self) -> ErrorBody:
        return self.to_body()


HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.INVALID_IMAGE: 400,
    ErrorCode.INVALID_FILE: 400,
    ErrorCode.IMAGE_CORRUPTED: 400,
    ErrorCode.CORRUPTED_IMAGE: 400,
    ErrorCode.UNSUPPORTED_FORMAT: 400,
    ErrorCode.IMAGE_TOO_LARGE: 413,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.IMAGE_TOO_SMALL: 400,
    ErrorCode.IMAGE_DIMENSIONS_INVALID: 400,
    ErrorCode.INVALID_DIMENSIONS: 400,
    ErrorCode.IMAGE_VALIDATION_FAILED: 400,
    ErrorCode.GENERATION_FAILED: 500,
    ErrorCode.PREPARATION_FAILED: 400,
    ErrorCode.STORAGE_FAILURE: 500,
    ErrorCode.STORAGE_TEMPORARY_FAILURE: 503,
    ErrorCode.STORAGE_NOT_CONFIGURED: 503,
    ErrorCode.CONFIGURATION_ERROR: 503,
    ErrorCode.AUTHENTICATION_ERROR: 401,
    ErrorCode.PERMISSION_ERROR: 403,
    ErrorCode.INVALID_ACCOUNT: 400,
    ErrorCode.INVALID_IMAGE_URL: 400,
    ErrorCode.MEDIA_CREATION_FAILED: 502,
    ErrorCode.MEDIA_PUBLISH_FAILED: 502,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.NETWORK_ERROR: 502,
    ErrorCode.API_ERROR: 502,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.INSTAGRAM_TIMEOUT: 504,
    ErrorCode.AMBIGUOUS_PUBLICATION: 409,
    ErrorCode.INSTAGRAM_NOT_CONNECTED: 409,
    ErrorCode.DUPLICATE_PUBLICATION: 409,
    ErrorCode.VERIFICATION_FAILED: 502,
    ErrorCode.VERIFICATION_DELAY: 502,
    ErrorCode.TASK_NOT_FOUND: 404,
    ErrorCode.TOOL_NOT_ALLOWED: 500,
    ErrorCode.UNKNOWN_TOOL: 500,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.OPENAI_CONFIGURATION_ERROR: 503,
    ErrorCode.OPENAI_API_ERROR: 502,
    ErrorCode.OPENAI_INVALID_RESPONSE: 502,
    ErrorCode.DEEPSEEK_CONFIGURATION_ERROR: 503,
    ErrorCode.DEEPSEEK_API_ERROR: 502,
    ErrorCode.DEEPSEEK_INVALID_RESPONSE: 502,
    ErrorCode.DEEPSEEK_TIMEOUT: 504,
    ErrorCode.DEEPSEEK_RATE_LIMITED: 429,
    ErrorCode.DEEPSEEK_UNAVAILABLE: 503,
    ErrorCode.VISION_PROVIDER_ERROR: 503,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.MUST_CHANGE_PASSWORD: 403,
    ErrorCode.CONTENT_PROFILE_MISSING: 400,
    ErrorCode.CONTENT_LLM_FAILED: 502,
    ErrorCode.CONTENT_INVALID_PLAN: 502,
    ErrorCode.CONTENT_DIVERSITY_REJECTED: 409,
    ErrorCode.CONTENT_IMAGE_FAILED: 502,
    ErrorCode.CONTENT_AUTOMATION_DISABLED: 409,
    ErrorCode.CONTENT_POLICY_VIOLATION: 400,
    ErrorCode.CONTENT_QA_FAILED: 422,
    ErrorCode.CANVA_DISABLED: 503,
    ErrorCode.CANVA_NOT_CONNECTED: 409,
    ErrorCode.CANVA_AUTHORIZATION_FAILED: 401,
    ErrorCode.CANVA_TIMEOUT: 504,
    ErrorCode.CANVA_UNAVAILABLE: 503,
    ErrorCode.CANVA_CAPABILITY_UNAVAILABLE: 409,
}


def http_status_for(code: ErrorCode) -> int:
    return HTTP_STATUS_BY_CODE.get(code, 500)


SAFE_INTERNAL_MESSAGE = "An unexpected error occurred while processing the request."
