"""Authentication and admin-only identity routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from auth.deps import get_current_user, get_db, get_settings_dep, require_active_user, require_admin
from auth.isolation import public_user
from auth.service import authenticate_user, change_user_password, logout_user, register_user
from config import Settings
from db.models import User
from db.repositories import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.post("/register")
def register(
    body: RegisterBody,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    return register_user(db, settings, email=str(body.email), password=body.password, response=response)


@router.post("/login")
def login(
    body: LoginBody,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    return authenticate_user(db, settings, email=str(body.email), password=body.password, response=response)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return logout_user(db, response, getattr(request.state, "session_id", None))


@router.get("/me")
def me(user: User = Depends(require_active_user)) -> dict:
    return {"user": public_user(user)}


@router.post("/change-password")
def change_password(
    body: PasswordBody,
    request: Request,
    response: Response,
    user: User = Depends(require_active_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    return change_user_password(
        db,
        settings,
        user,
        current_password=body.current_password,
        new_password=body.new_password,
        response=response,
        current_session_id=getattr(request.state, "session_id", None),
    )


@admin_router.get("/users")
def admin_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    users = UserRepository(db).list_all()
    return {"users": [public_user(item) for item in users]}
