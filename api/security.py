"""Authentication dependency used by owner-scoped media routes.

Production session/JWT validation lives in `auth.deps`. This module keeps a
stable import path for generation tests that override `get_current_user`.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from pydantic import BaseModel, ConfigDict

from auth.tokens import COOKIE_NAME, decode_access_token
from db.repositories import SessionRepository, UserRepository
from models.errors import AppError, ErrorCode


class CurrentUser(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    email: str = ""
    is_admin: bool = False
    is_active: bool = True
    must_change_password: bool = False


def _coerce_user(value: Any) -> CurrentUser | None:
    if value is None:
        return None
    if isinstance(value, CurrentUser):
        return value
    if hasattr(value, "id"):
        return CurrentUser(
            id=str(value.id),
            email=str(getattr(value, "email", "") or ""),
            is_admin=bool(getattr(value, "is_admin", False)),
            is_active=bool(getattr(value, "is_active", True)),
            must_change_password=bool(getattr(value, "must_change_password", False)),
        )
    if isinstance(value, dict) and value.get("id"):
        return CurrentUser.model_validate(value)
    return None


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE_NAME)


async def get_current_user(request: Request) -> CurrentUser:
    resolver = getattr(request.app.state, "current_user_resolver", None)
    if callable(resolver):
        resolved = resolver(request)
        if hasattr(resolved, "__await__"):
            resolved = await resolved
        user = _coerce_user(resolved)
        if user is not None:
            return user

    for candidate in (
        getattr(request.state, "user", None),
        getattr(request.app.state, "current_user", None),
    ):
        user = _coerce_user(candidate)
        if user is not None:
            return user

    settings = getattr(request.app.state, "settings", None)
    factory = getattr(request.app.state, "session_factory", None)
    token = _token_from_request(request)
    if settings is not None and factory is not None and token:
        session = factory()
        try:
            payload = decode_access_token(settings, token)
            jti = payload.get("jti") or ""
            if jti and SessionRepository(session).get_valid(jti) is not None:
                db_user = UserRepository(session).get_by_id(payload["user_id"])
                user = _coerce_user(db_user)
                if user is not None:
                    if not user.is_active:
                        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "This account is inactive.")
                    if user.must_change_password:
                        raise AppError(
                            ErrorCode.MUST_CHANGE_PASSWORD,
                            "You must change your password before continuing.",
                        )
                    request.state.user = db_user
                    return user
        finally:
            session.close()

    raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Authentication is required.", http_status=401)
