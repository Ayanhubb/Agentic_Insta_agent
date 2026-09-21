"""Password hashing. Never store or log plaintext passwords."""

from __future__ import annotations

import bcrypt

from models.errors import AppError, ErrorCode

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str, *, rounds: int = 12) -> str:
    if not password:
        raise AppError(ErrorCode.INVALID_REQUEST, "Password is required.")
    cost = max(4, min(int(rounds), 16))
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=cost))
    return hashed.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def validate_new_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )
    return password
