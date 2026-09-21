"""Resolve the authenticated user without assuming Agent 3's final module layout."""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import Request


def user_id_of(user: Any) -> str | None:
    if user is None:
        return None
    if isinstance(user, str) and user.strip():
        return user.strip()
    for attr in ("id", "user_id", "uid"):
        value = getattr(user, attr, None)
        if value:
            return str(value)
    if isinstance(user, dict):
        for key in ("id", "user_id", "uid"):
            value = user.get(key)
            if value:
                return str(value)
    return None


async def resolve_request_user(request: Request) -> Any | None:
    provider = getattr(request.app.state, "current_user_provider", None)
    if callable(provider):
        result = provider(request)
        if inspect.isawaitable(result):
            result = await result
        if result is not None:
            return result
    for attr in ("user", "current_user", "account"):
        value = getattr(request.state, attr, None)
        if value is not None:
            return value
    return None


async def optional_user_id(request: Request) -> str | None:
    return user_id_of(await resolve_request_user(request))
