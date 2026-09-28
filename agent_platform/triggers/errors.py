"""Errors for trigger dispatch.

Messages are fixed phrases. They do not include payloads, tokens, or tracebacks.
"""

from __future__ import annotations


class TriggerError(Exception):
    """Expected trigger failure with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)
