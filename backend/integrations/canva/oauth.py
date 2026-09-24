"""Per-user Canva OAuth with PKCE.

Authorization codes and tokens stay on the backend. React receives an
authorization URL and, after the callback, a connected flag.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.integrations.canva.endpoints import (
    require_canva_https_url,
    require_redirect_uri,
)
from config import Settings
from models.errors import AppError, ErrorCode

_SAFE_OAUTH_ERROR = re.compile(r"^[a-z0-9_]{1,64}$")
_STATE_TTL = timedelta(minutes=10)


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorize_url(settings: Settings, *, state: str, verifier: str) -> str:
    authorize = require_canva_https_url(
        settings.canva_authorize_url,
        allow_unofficial=settings.canva_allow_unofficial_endpoint,
    )
    resource = require_canva_https_url(
        settings.canva_mcp_url,
        allow_unofficial=settings.canva_allow_unofficial_endpoint,
    )
    redirect = require_redirect_uri(settings.canva_redirect_uri)
    query = {
        "response_type": "code",
        "client_id": settings.canva_client_id.strip(),
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
        "resource": resource,
    }
    scopes = settings.canva_oauth_scopes.strip()
    if scopes:
        query["scope"] = scopes
    return f"{authorize}?{urlencode(query)}"


def authorization_code_form(
    settings: Settings,
    *,
    code: str,
    verifier: str,
) -> dict[str, str]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": require_redirect_uri(settings.canva_redirect_uri),
        "client_id": settings.canva_client_id.strip(),
        "code_verifier": verifier,
        "resource": require_canva_https_url(
            settings.canva_mcp_url,
            allow_unofficial=settings.canva_allow_unofficial_endpoint,
        ),
    }
    secret = settings.canva_client_secret.strip()
    if secret:
        form["client_secret"] = secret
    return form


def refresh_form(settings: Settings, refresh_token: str) -> dict[str, str]:
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.canva_client_id.strip(),
        "resource": require_canva_https_url(
            settings.canva_mcp_url,
            allow_unofficial=settings.canva_allow_unofficial_endpoint,
        ),
    }
    secret = settings.canva_client_secret.strip()
    if secret:
        form["client_secret"] = secret
    return form


def state_expiry(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    return current + _STATE_TTL


def _safe_oauth_error(payload: dict[str, Any]) -> str:
    code = str(payload.get("error") or "")
    if _SAFE_OAUTH_ERROR.fullmatch(code):
        return code
    return "authorization_failed"


def _token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AppError(
            ErrorCode.CANVA_AUTHORIZATION_FAILED,
            "Canva authorization failed.",
            http_status=401,
        )
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token if isinstance(refresh_token, str) and refresh_token else None,
        "expires_in": expires_in if isinstance(expires_in, int) else None,
        "token_type": "Bearer",
    }


async def exchange_token(
    settings: Settings,
    form: dict[str, str],
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code or refresh token. The form is never logged."""
    token_url = require_canva_https_url(
        settings.canva_token_url,
        allow_unofficial=settings.canva_allow_unofficial_endpoint,
    )
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.canva_timeout_seconds)
    try:
        try:
            response = await http.post(
                token_url,
                data=form,
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.CANVA_TIMEOUT,
                "The Canva authorization request timed out.",
                http_status=504,
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise AppError(
                ErrorCode.CANVA_UNAVAILABLE,
                "Canva authorization could not be reached.",
                http_status=503,
                retryable=True,
            ) from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code in {401, 403} or response.status_code >= 400:
        error_code = "authorization_failed"
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict):
            error_code = _safe_oauth_error(body)
        raise AppError(
            ErrorCode.CANVA_AUTHORIZATION_FAILED,
            "Canva authorization failed.",
            http_status=401,
            details={"oauth_error": error_code},
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise AppError(
            ErrorCode.CANVA_AUTHORIZATION_FAILED,
            "Canva authorization failed.",
            http_status=401,
        ) from exc
    if not isinstance(body, dict):
        raise AppError(
            ErrorCode.CANVA_AUTHORIZATION_FAILED,
            "Canva authorization failed.",
            http_status=401,
        )
    return _token_payload(body)
