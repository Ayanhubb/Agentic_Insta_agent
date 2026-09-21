"""Bootstrap the default admin from environment variables. Credentials are not hard-coded."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from auth.passwords import hash_password
from config import Settings
from db.repositories import UserRepository

logger = logging.getLogger(__name__)


def bootstrap_admin(session: Session, settings: Settings) -> None:
    email = settings.default_admin_email.strip().lower()
    password = settings.default_admin_password
    if not email or not password:
        return
    repo = UserRepository(session)
    existing = repo.get_by_email(email)
    if existing is not None:
        return
    repo.create(
        email=email,
        password_hash=hash_password(password, rounds=getattr(settings, "bcrypt_rounds", 12)),
        is_admin=True,
        is_active=True,
        must_change_password=True,
    )
    logger.info("Bootstrapped default admin account")
