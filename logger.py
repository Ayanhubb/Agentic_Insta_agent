"""Compatibility logging helpers."""

from services.logging import configure_logging, log_step, redact_text

LOGGER_NAME = "instagram_agent"


def log_event(event: str, **fields: object) -> None:
    import logging

    log_step(logging.getLogger(LOGGER_NAME), event=event, task_id=str(fields.get("task_id") or ""), **fields)


__all__ = ["LOGGER_NAME", "configure_logging", "log_event", "log_step", "redact_text"]
