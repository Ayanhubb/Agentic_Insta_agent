"""Reject credentials and executable code before they can be stored or returned."""

from __future__ import annotations

import re
from typing import Any

from models.errors import AppError, ErrorCode
from services.logging import is_secret_key

_SECRET_VALUE = re.compile(
    r"(sk-|sk-proj-|EAAB|Bearer\s+|eyJ[A-Za-z0-9_-]{8,}\.|api_key\s*=|access_token\s*=)",
    re.IGNORECASE,
)

_MESSAGE = "Agent configuration cannot include credentials."
_CODE_MESSAGE = "Agent configuration cannot include executable code."
_CODE_KEYS = frozenset(
    {
        "callable",
        "code",
        "command",
        "commands",
        "eval",
        "exec",
        "handler",
        "handler_path",
        "import",
        "import_path",
        "python",
        "script",
        "shell",
        "subprocess",
    }
)


def looks_like_secret(value: str) -> bool:
    return _SECRET_VALUE.search(value) is not None


def assert_no_secrets(payload: Any, *, depth: int = 0) -> None:
    """Raise when a key or string looks like a credential or code. The value is not echoed."""

    if depth > 8:
        raise AppError(ErrorCode.INVALID_REQUEST, _CODE_MESSAGE)
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = str(key)
            if name.lower().replace("-", "_") in _CODE_KEYS:
                raise AppError(ErrorCode.INVALID_REQUEST, _CODE_MESSAGE)
            if is_secret_key(name):
                raise AppError(ErrorCode.INVALID_REQUEST, _MESSAGE)
            assert_no_secrets(value, depth=depth + 1)
        return
    if isinstance(payload, list):
        for item in payload:
            assert_no_secrets(item, depth=depth + 1)
        return
    if isinstance(payload, str) and looks_like_secret(payload):
        raise AppError(ErrorCode.INVALID_REQUEST, _MESSAGE)


def blank_secret(value: str) -> str:
    if looks_like_secret(value):
        return ""
    return value
