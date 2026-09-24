"""Encrypted per-user Canva token store.

Rows are always filtered by tenant id and user id together.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from backend.integrations.canva.contracts import CanvaOwner
from config import Settings
from db.crypto import TokenEncryptor
from db.exceptions import TokenEncryptionError
from db.models import CanvaConnection, CanvaOAuthState
from models.errors import AppError, ErrorCode


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@dataclass(frozen=True)
class StoredCanvaAuth:
    tenant_id: str
    user_id: str
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    status: str


class CanvaConnectionStore:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session] | None = None) -> None:
        self._settings = settings
        self._session_factory = session_factory

    def _factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        from db.session import get_session_factory

        return get_session_factory()

    def _encryptor(self) -> TokenEncryptor:
        try:
            return TokenEncryptor.from_settings(self._settings)
        except TokenEncryptionError as exc:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "TOKEN_ENCRYPTION_KEY is required to store Canva authorization.",
                http_status=503,
            ) from exc

    def _run(self, fn: Callable[[Session], Any]) -> Any:
        session = self._factory()()
        try:
            result = fn(session)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def is_connected(self, owner: CanvaOwner) -> bool:
        stored = self.get(owner)
        return stored is not None and stored.status == "connected" and bool(stored.access_token)

    def get(self, owner: CanvaOwner) -> StoredCanvaAuth | None:
        encryptor = self._encryptor()

        def _op(session: Session) -> StoredCanvaAuth | None:
            row = self._row(session, owner)
            if row is None:
                return None
            refresh = (
                encryptor.decrypt(row.refresh_token_encrypted) if row.refresh_token_encrypted else None
            )
            return StoredCanvaAuth(
                tenant_id=row.tenant_id,
                user_id=row.user_id,
                access_token=encryptor.decrypt(row.access_token_encrypted),
                refresh_token=refresh,
                expires_at=row.token_expires_at,
                status=row.status,
            )

        return self._run(_op)

    def save_tokens(
        self,
        owner: CanvaOwner,
        *,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
    ) -> None:
        if not access_token.strip():
            raise AppError(
                ErrorCode.CANVA_AUTHORIZATION_FAILED,
                "Canva authorization failed.",
                http_status=401,
            )
        encryptor = self._encryptor()
        access_encrypted = encryptor.encrypt(access_token)
        refresh_encrypted = encryptor.encrypt(refresh_token) if refresh_token else None
        expires_at = None
        if isinstance(expires_in, int) and expires_in > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        def _op(session: Session) -> None:
            row = self._row(session, owner)
            if row is None:
                row = CanvaConnection(tenant_id=owner.tenant_id, user_id=owner.user_id, access_token_encrypted=access_encrypted)
                session.add(row)
            row.access_token_encrypted = access_encrypted
            row.refresh_token_encrypted = refresh_encrypted
            row.token_expires_at = expires_at
            row.status = "connected"

        self._run(_op)

    def mark(self, owner: CanvaOwner, status: str) -> None:
        def _op(session: Session) -> None:
            row = self._row(session, owner)
            if row is not None:
                row.status = status

        self._run(_op)

    def disconnect(self, owner: CanvaOwner) -> None:
        def _op(session: Session) -> None:
            row = self._row(session, owner)
            if row is not None:
                session.delete(row)

        self._run(_op)

    def save_oauth_state(self, owner: CanvaOwner, *, state: str, verifier: str, expires_at: datetime) -> None:
        encrypted = self._encryptor().encrypt(verifier)

        def _op(session: Session) -> None:
            now = datetime.now(timezone.utc)
            for row in list(session.query(CanvaOAuthState).all()):
                if _aware(row.expires_at) <= now:
                    session.delete(row)
            session.merge(
                CanvaOAuthState(
                    state=state,
                    tenant_id=owner.tenant_id,
                    user_id=owner.user_id,
                    code_verifier_encrypted=encrypted,
                    expires_at=expires_at,
                )
            )

        self._run(_op)

    def consume_oauth_state(self, *, state: str, owner: CanvaOwner) -> str:
        encryptor = self._encryptor()

        def _op(session: Session) -> str | None:
            row = session.get(CanvaOAuthState, state)
            if row is None:
                return ""
            expired = _aware(row.expires_at) <= datetime.now(timezone.utc)
            mismatch = row.user_id != owner.user_id or row.tenant_id != owner.tenant_id
            if expired or mismatch:
                session.delete(row)
                return None
            verifier = encryptor.decrypt(row.code_verifier_encrypted)
            session.delete(row)
            return verifier

        verifier = self._run(_op)
        if not verifier:
            raise AppError(
                ErrorCode.CANVA_AUTHORIZATION_FAILED,
                "Canva authorization does not match the signed-in user.",
                http_status=401,
            )
        return verifier

    def _row(self, session: Session, owner: CanvaOwner) -> CanvaConnection | None:
        return (
            session.query(CanvaConnection)
            .filter(CanvaConnection.tenant_id == owner.tenant_id, CanvaConnection.user_id == owner.user_id)
            .one_or_none()
        )
