"""JWT access tokens. Secrets never appear in logs or API bodies."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from config import Settings
from models.errors import AppError, ErrorCode

ALGORITHM = "HS256"
COOKIE_NAME = "access_token"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    settings: Settings,
    *,
    user_id: str,
    email: str,
    jti: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    lifetime = expires_delta if expires_delta is not None else timedelta(minutes=max(settings.jwt_expire_minutes, 0))
    payload = {
        "sub": user_id,
        "email": email,
        "jti": jti or str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(settings: Settings, token: str) -> dict[str, str]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Authentication token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Invalid authentication token.") from exc
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AppError(ErrorCode.AUTHENTICATION_ERROR, "Invalid authentication token.")
    return {
        "user_id": user_id,
        "email": str(payload.get("email") or ""),
        "jti": str(payload.get("jti") or ""),
    }
