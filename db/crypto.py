"""Fernet encryption for Instagram access tokens. Plaintext tokens are never stored."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import Settings
from db.exceptions import TokenEncryptionError


def _fernet_from_secret(secret: str) -> Fernet:
    raw = secret.encode("utf-8")
    if len(raw) == 44:
        try:
            return Fernet(raw)
        except (ValueError, InvalidToken):
            pass
    digest = hashlib.sha256(raw).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class TokenEncryptor:
    """Encrypts/decrypts Instagram OAuth tokens at rest."""

    def __init__(self, secret: str) -> None:
        if not (secret or "").strip():
            raise TokenEncryptionError(
                "TOKEN_ENCRYPTION_KEY is required; plaintext Instagram tokens are never stored."
            )
        self._fernet = _fernet_from_secret(secret.strip())

    @classmethod
    def from_settings(cls, settings: Settings) -> "TokenEncryptor":
        secret = (settings.token_encryption_key or "").strip()
        if not secret:
            raise TokenEncryptionError(
                "TOKEN_ENCRYPTION_KEY is required; plaintext Instagram tokens are never stored."
            )
        return cls(secret)

    @classmethod
    def generate_key(cls) -> str:
        return Fernet.generate_key().decode("ascii")

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise TokenEncryptionError("An Instagram access token is required.")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            raise TokenEncryptionError("Instagram account token is missing.")
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise TokenEncryptionError("Instagram account token could not be decrypted.") from exc


def encrypt_token(settings: Settings, token: str) -> str:
    return TokenEncryptor.from_settings(settings).encrypt(token)


def decrypt_token(settings: Settings, token_encrypted: str) -> str:
    return TokenEncryptor.from_settings(settings).decrypt(token_encrypted)
