"""Backend user isolation helpers. Frontend checks are never sufficient."""

from __future__ import annotations

from db.models import User
from models.errors import AppError, ErrorCode


def public_user(user: User) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
    }


def assert_owner(resource_user_id: str | None, user: User, *, message: str = "Resource was not found.") -> None:
    """Fail closed: missing owner or another user's resource is indistinguishable."""
    if resource_user_id is None or resource_user_id != user.id:
        raise AppError(ErrorCode.NOT_FOUND, message, http_status=404)


def assert_task_owner(resource_user_id: str | None, user: User) -> None:
    if resource_user_id is None or resource_user_id != user.id:
        raise AppError(ErrorCode.TASK_NOT_FOUND, "Task was not found.", http_status=404)
