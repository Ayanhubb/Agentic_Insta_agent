"""Structured asset failures. Messages do not name files, paths, or other tenants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MESSAGES = {
    "TENANT_REQUIRED": "Tenant context is required.",
    "NOT_FOUND": "Asset was not found.",
    "NOT_GRANTED": "Asset role is not granted.",
    "INVALID_FILE": "The provided file is invalid.",
    "UNSUPPORTED_FORMAT": "This file type is not supported.",
    "FILE_TOO_LARGE": "The file exceeds the maximum allowed size.",
    "INVALID_FILENAME": "The file name is not allowed.",
    "INVALID_PATH": "Invalid storage path.",
    "INVALID_DEFINITION": "Asset definition is invalid.",
}


@dataclass(frozen=True, slots=True)
class AssetFailure:
    code: str
    message: str
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class AssetAccessError(Exception):
    def __init__(self, failure: AssetFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure

    @property
    def code(self) -> str:
        return self.failure.code

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.failure.as_dict()}


def fail(code: str) -> AssetAccessError:
    message = MESSAGES.get(code, MESSAGES["INVALID_DEFINITION"])
    return AssetAccessError(AssetFailure(code=code, message=message))
