"""FastAPI authentication dependencies."""

from __future__ import annotations

import inspect
from collections.abc import Generator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from auth.tokens import COOKIE_NAME, decode_access_token
from config import Settings
from db.models import User
from db.repositories import SessionRepository, UserRepository
from models.errors import AppError, ErrorCode

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _extract_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> str:
    if credentials and credentials.credentials:
        return credentials.credentials
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie
    raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Authentication is required.")


async def _from_provider(request: Request) -> User | None:
    provider = getattr(request.app.state, "current_user_provider", None)
    if not callable(provider):
        return None
    result = provider(request)
    if inspect.isawaitable(result):
        result = await result
    return result


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db),
) -> User:
    provided = await _from_provider(request)
    if provided is not None:
        request.state.user = provided
        return provided

    token = _extract_token(request, credentials, settings)
    payload = decode_access_token(settings, token)
    jti = payload.get("jti") or ""
    if not jti or SessionRepository(db).get_valid(jti) is None:
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Authentication is required.")
    user = UserRepository(db).get_by_id(payload["user_id"])
    if user is None:
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Authentication is required.")
    if not user.is_active:
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "This account is inactive.")
    request.state.user = user
    request.state.session_id = jti
    return user


def require_active_user(user: User = Depends(get_current_user)) -> User:
    if not getattr(user, "is_active", True):
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "This account is inactive.")
    return user


def require_password_ok(user: User = Depends(require_active_user)) -> User:
    if getattr(user, "must_change_password", False):
        raise AppError(
            ErrorCode.MUST_CHANGE_PASSWORD,
            "You must change your password before continuing.",
        )
    return user


def require_admin(user: User = Depends(require_password_ok)) -> User:
    if not getattr(user, "is_admin", False):
        raise AppError(ErrorCode.PERMISSION_ERROR, "Administrator access is required.")
    return user


async def optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db),
) -> User | None:
    try:
        return await get_current_user(request, credentials, settings, db)
    except AppError:
        return None
