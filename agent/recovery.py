"""Deterministic recovery policy. Never blindly republishes after an unknown result."""

from __future__ import annotations

from enum import Enum

from models.errors import ErrorCode, OperationCertainty
from models.observations import Observation
from models.state import AgentState

VALIDATION_FAILURE_CODES = {
    ErrorCode.INVALID_IMAGE,
    ErrorCode.IMAGE_CORRUPTED,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    ErrorCode.IMAGE_TOO_LARGE,
    ErrorCode.IMAGE_DIMENSIONS_INVALID,
    ErrorCode.IMAGE_VALIDATION_FAILED,
    ErrorCode.INVALID_REQUEST,
    ErrorCode.PREPARATION_FAILED,
    ErrorCode.IMAGE_TOO_SMALL,
    ErrorCode.UNSUPPORTED_FORMAT,
    ErrorCode.INVALID_IMAGE_URL,
    ErrorCode.INVALID_ACCOUNT,
}

CONFIGURATION_FAILURE_CODES = {
    ErrorCode.AUTHENTICATION_ERROR,
    ErrorCode.PERMISSION_ERROR,
    ErrorCode.CONFIGURATION_ERROR,
}


class Decision(str, Enum):
    CONTINUE = "continue"
    RETRY = "retry"
    FAIL = "fail"
    VERIFY_FIRST = "verify_first"
    SKIP = "skip"


class RecoveryDecision:
    def __init__(
        self,
        action: Decision,
        *,
        delay_seconds: float = 0.0,
        reason: str = "",
        next_tool: str | None = None,
    ) -> None:
        self.action = action
        self.delay_seconds = delay_seconds
        self.reason = reason
        self.next_tool = next_tool


class RecoveryPolicy:
    def __init__(
        self,
        *,
        max_retry_after_seconds: float,
        storage_retry_attempts: int,
        verification_retry_attempts: int = 3,
    ) -> None:
        self._max_retry_after_seconds = max_retry_after_seconds
        self._storage_retry_attempts = storage_retry_attempts
        self._verification_retry_attempts = verification_retry_attempts

    def decide(
        self,
        observation: Observation,
        state: AgentState,
        *,
        attempt: int,
    ) -> RecoveryDecision:
        if observation.success:
            return RecoveryDecision(Decision.CONTINUE, reason="tool succeeded")

        error = observation.error
        code = error.code if error else ErrorCode.INTERNAL_ERROR
        certainty = error.certainty if error else OperationCertainty.FAILED
        retry_after = None
        if error and error.details:
            retry_after = error.details.get("retry_after_seconds")

        if code in VALIDATION_FAILURE_CODES:
            return RecoveryDecision(Decision.FAIL, reason="image validation cannot be recovered")

        if code in CONFIGURATION_FAILURE_CODES:
            return RecoveryDecision(
                Decision.FAIL,
                reason="configuration or authorization error; check META_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID",
            )

        if observation.tool == "publish_instagram_media" and certainty == OperationCertainty.UNKNOWN:
            state.skip_publish = True
            state.publish_certainty = OperationCertainty.UNKNOWN
            state.force_next_tool = "verify_publication"
            return RecoveryDecision(
                Decision.VERIFY_FIRST,
                reason="publish result is unknown; verify before any further publish",
                next_tool="verify_publication",
            )

        if code in {ErrorCode.TIMEOUT, ErrorCode.INSTAGRAM_TIMEOUT} and observation.tool == "publish_instagram_media":
            state.skip_publish = True
            state.publish_certainty = OperationCertainty.UNKNOWN
            state.force_next_tool = "verify_publication"
            return RecoveryDecision(
                Decision.VERIFY_FIRST,
                reason="publish timed out; refuse to republish until verification",
                next_tool="verify_publication",
            )

        if code == ErrorCode.RATE_LIMITED:
            if (
                isinstance(retry_after, (int, float))
                and 0 < float(retry_after) <= self._max_retry_after_seconds
                and attempt < 2
            ):
                return RecoveryDecision(
                    Decision.RETRY,
                    delay_seconds=float(retry_after),
                    reason="rate limit included a short Retry-After",
                )
            return RecoveryDecision(Decision.FAIL, reason="rate limit exceeded safe retry window")

        if code == ErrorCode.STORAGE_TEMPORARY_FAILURE and attempt < self._storage_retry_attempts:
            return RecoveryDecision(
                Decision.RETRY,
                delay_seconds=0.0,
                reason="retry temporary storage failure",
            )

        if observation.tool == "verify_publication":
            if code == ErrorCode.VERIFICATION_DELAY and attempt < self._verification_retry_attempts:
                return RecoveryDecision(
                    Decision.RETRY,
                    delay_seconds=0.0,
                    reason="verification delay; retry verify without publishing again",
                )
            if state.publish_certainty == OperationCertainty.UNKNOWN:
                return RecoveryDecision(
                    Decision.FAIL,
                    reason="publication state remains unknown; refusing to publish again",
                )
            if state.instagram_media_id and attempt < self._verification_retry_attempts:
                return RecoveryDecision(
                    Decision.RETRY,
                    delay_seconds=0.0,
                    reason="published media is not visible yet; retry verification only",
                )

        if observation.tool == "create_instagram_media" and certainty == OperationCertainty.UNKNOWN:
            if not state.instagram_container_id and attempt < 2:
                return RecoveryDecision(
                    Decision.RETRY,
                    reason="container creation uncertain and no ID was returned",
                )
            if state.instagram_container_id:
                return RecoveryDecision(
                    Decision.SKIP,
                    reason="container ID already exists; do not create another container",
                    next_tool="publish_instagram_media",
                )
            return RecoveryDecision(Decision.FAIL, reason="container creation cannot be confirmed")

        if (
            code in {ErrorCode.TIMEOUT, ErrorCode.INSTAGRAM_TIMEOUT, ErrorCode.INSTAGRAM_API_ERROR}
            and error is not None
            and error.retryable
            and observation.tool not in {"publish_instagram_media"}
            and attempt < 2
        ):
            return RecoveryDecision(Decision.RETRY, delay_seconds=0.0, reason="retry transient Instagram error")

        return RecoveryDecision(Decision.FAIL, reason="unrecoverable tool failure")
