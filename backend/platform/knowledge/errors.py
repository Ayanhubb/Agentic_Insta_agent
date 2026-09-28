"""Structured knowledge failures. Messages do not include another tenant's content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MESSAGES = {
    "TENANT_REQUIRED": "Tenant context is required.",
    "NOT_FOUND": "Knowledge was not found.",
    "NOT_GRANTED": "Knowledge source is not granted.",
    "INVALID_DEFINITION": "Knowledge definition is invalid.",
    "INVALID_SOURCE": "Knowledge source is not registered.",
    "NOT_VERSIONED": "This knowledge source is loaded at run time.",
}


@dataclass(frozen=True, slots=True)
class KnowledgeFailure:
    code: str
    message: str
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class KnowledgeAccessError(Exception):
    def __init__(self, failure: KnowledgeFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure

    @property
    def code(self) -> str:
        return self.failure.code

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.failure.as_dict()}


def fail(code: str) -> KnowledgeAccessError:
    message = MESSAGES.get(code, MESSAGES["INVALID_DEFINITION"])
    return KnowledgeAccessError(KnowledgeFailure(code=code, message=message))
