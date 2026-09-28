"""Structured tool failures. Messages stay generic so they cannot leak secrets or other tenants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.logging import redact_text


@dataclass(frozen=True, slots=True)
class ToolFailure:
    code: str
    message: str
    tool: str | None = None
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "tool": self.tool,
            "retryable": self.retryable,
        }


class ToolExecutionError(Exception):
    """A tool call that did not run, or ran and was rejected. The public body is `as_dict()`."""

    def __init__(self, failure: ToolFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure

    @property
    def code(self) -> str:
        return self.failure.code

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.failure.as_dict()}


def fail(
    code: str,
    message: str,
    *,
    tool: str | None = None,
    retryable: bool = False,
) -> ToolExecutionError:
    safe_tool = None
    if tool is not None:
        safe_tool = redact_text(str(tool))[:80] or None
    return ToolExecutionError(ToolFailure(code=code, message=message, tool=safe_tool, retryable=retryable))
