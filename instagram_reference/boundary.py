"""Publishing boundary for the reference agent.

Meta create, publish, and verify stay inside ``InstagramAgent``. This module
only recognizes the names that must not appear on a platform tool grant.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS

_TOOL_FIELDS = frozenset(
    {
        "action",
        "actions",
        "step",
        "steps",
        "tool",
        "tool_name",
        "tool_names",
        "tools",
    }
)


def mentions_publish_tool(value: object, *, depth: int = 0) -> bool:
    """True when a plan or payload names an Instagram publishing tool."""

    if depth > 8:
        return False
    if isinstance(value, str):
        return value in INSTAGRAM_PUBLISHING_TOOLS
    if isinstance(value, list):
        return any(
            mentions_publish_tool(item, depth=depth + 1)
            for item in value
            if isinstance(item, (Mapping, list))
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            if name in INSTAGRAM_PUBLISHING_TOOLS:
                return True
            if name in _TOOL_FIELDS and _field_names_a_publish_tool(item):
                return True
            if isinstance(item, (Mapping, list)) and mentions_publish_tool(item, depth=depth + 1):
                return True
    return False


def _field_names_a_publish_tool(value: object) -> bool:
    if isinstance(value, str):
        return value in INSTAGRAM_PUBLISHING_TOOLS
    if isinstance(value, list):
        return any(_field_names_a_publish_tool(item) for item in value)
    return False


def public_text(value: object, *, limit: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:limit]
