"""Authentication service. Route handlers must not hash passwords or issue tokens themselves."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Response
from sqlalchemy.orm import Session

from auth.isolation import public_user
from auth.passwords import hash_password, validate_new_password, verify_password
from auth.tokens import COOKIE_NAME, create_access_token, token_hash
from config import Settings
from db.models import User
from db.repositories import SessionRepository, UserRepository
from models.errors import AppError, ErrorCode


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def set_access_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=bool(getattr(settings, "jwt_cookie_secure", False)),
        max_age=max(int(settings.jwt_expire_minutes), 0) * 60,
        path="/",
    )


def clear_access_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def issue_session(db: Session, settings: Settings, user: User, response: Response) -> dict[str, object]:
    expires_at = _utcnow() + timedelta(minutes=max(int(settings.jwt_expire_minutes), 0))
    row = SessionRepository(db).create(user_id=user.id, token_hash="pending", expires_at=expires_at)
    token = create_access_token(settings, user_id=user.id, email=user.email, jti=row.id)
    row.token_hash = token_hash(token)
    db.flush()
    set_access_cookie(response, settings, token)
    return {"user": public_user(user), "access_token": token, "token_type": "bearer"}


def register_user(db: Session, settings: Settings, *, email: str, password: str, response: Response) -> dict[str, object]:
    repo = UserRepository(db)
    if repo.get_by_email(email):
        raise AppError(ErrorCode.CONFLICT, "An account with this email already exists.")
    validate_new_password(password)
    user = repo.create(
        email=email.strip().lower(),
        password_hash=hash_password(password, rounds=getattr(settings, "bcrypt_rounds", 12)),
        is_admin=False,
        is_active=True,
        must_change_password=False,
    )
    return issue_session(db, settings, user, response)


def authenticate_user(db: Session, settings: Settings, *, email: str, password: str, response: Response) -> dict[str, object]:
    repo = UserRepository(db)
    user = repo.get_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Invalid email or password.")
    if not user.is_active:
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Invalid email or password.")
    sessions = SessionRepository(db)
    if sessions.count_for_user(user.id) == 0 and user.is_admin:
        user.must_change_password = True
        db.flush()
    return issue_session(db, settings, user, response)


def change_user_password(
    db: Session,
    settings: Settings,
    user: User,
    *,
    current_password: str,
    new_password: str,
    response: Response,
    current_session_id: str | None = None,
) -> dict[str, object]:
    if not verify_password(current_password, user.password_hash):
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Invalid email or password.")
    validate_new_password(new_password)
    user.password_hash = hash_password(new_password, rounds=getattr(settings, "bcrypt_rounds", 12))
    user.must_change_password = False
    SessionRepository(db).revoke_all_for_user(user.id, except_id=current_session_id)
    db.flush()
    return issue_session(db, settings, user, response)


def logout_user(db: Session, response: Response, session_id: str | None) -> dict[str, bool]:
    if session_id:
        SessionRepository(db).revoke(session_id)
    clear_access_cookie(response)
    return {"success": True}
