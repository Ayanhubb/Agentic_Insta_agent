"""Structured logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(access_token=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"(META_ACCESS_TOKEN\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(OPENAI_API_KEY\s*[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(Authorization['\"]?\s*[:=]\s*['\"]?)[^'\"\s]+", re.IGNORECASE),
    re.compile(r"(token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9\-_]{16,}"),
)

_SECRET_KEYS = frozenset(
    {
        "access_token",
        "meta_access_token",
        "authorization",
        "token",
        "password",
        "secret",
        "client_secret",
        "openai_api_key",
        "api_key",
        "password_hash",
        "jwt_secret",
        "token_encryption_key",
        "default_admin_password",
        "access_token_encrypted",
    }
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 0:
            redacted = pattern.sub(REDACTED, redacted)
        else:
            redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in _SECRET_KEYS else redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_value(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(redact_value(arg) for arg in record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "task_id",
            "request_id",
            "step",
            "tool",
            "status",
            "duration_ms",
            "current_step",
            "error_code",
            "http_status",
            "graph_code",
            "graph_subcode",
            "graph_message",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(redact_value(payload), default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log_step(
    logger: logging.Logger,
    *,
    event: str,
    task_id: str,
    step: str | None = None,
    tool: str | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    request_id: str | None = None,
    **extra: Any,
) -> None:
    payload = {
        "task_id": task_id,
        "step": step,
        "tool": tool,
        "status": status,
        "duration_ms": duration_ms,
        "request_id": request_id,
    }
    payload.update(extra)
    logger.info(event, extra={key: value for key, value in payload.items() if value is not None})
