"""Read-only Meta Graph client for account intelligence.

This client issues GET requests only. It does not create containers or publish.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from config import ALLOWED_GRAPH_HOSTS, Settings
from models.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

_AUTH_CODES = {102, 190, 467}
_PERMISSION_CODES = {10}
_INVALID_METRIC_CODES = {100}
_RATE_LIMIT_CODES = {4, 17, 32, 613}


class InstagramReadClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._settings.graph_api_root}/{path.lstrip('/')}"
        self._validate(url)
        try:
            response = await self._http.request(
                "GET",
                url,
                headers={"Authorization": f"Bearer {self._settings.meta_access_token}"},
                params=params,
            )
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.TIMEOUT,
                "Instagram API request timed out.",
                http_status=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(
                ErrorCode.NETWORK_ERROR,
                "Instagram API request failed.",
                http_status=502,
            ) from exc
        try:
            parsed = response.json() if response.content else {}
        except ValueError:
            parsed = {}
        payload = parsed if isinstance(parsed, dict) else {}
        if response.status_code >= 400 or "error" in payload:
            raise _map_error(payload, response.status_code)
        return payload

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def _validate(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host not in ALLOWED_GRAPH_HOSTS and host not in {"127.0.0.1", "localhost"}:
            raise AppError(ErrorCode.CONFIGURATION_ERROR, "Instagram API host is not allowed.", http_status=500)


def _map_error(payload: dict[str, Any], http_status: int) -> AppError:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    try:
        code = int(error.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    details = {"graph_code": code, "http_status": http_status}
    if code in _RATE_LIMIT_CODES or http_status == 429:
        return AppError(
            ErrorCode.RATE_LIMITED,
            "Instagram rate limit reached.",
            http_status=429,
            details=details,
        )
    if http_status == 408:
        return AppError(
            ErrorCode.TIMEOUT,
            "Instagram API request timed out.",
            http_status=504,
            details=details,
        )
    if code in _AUTH_CODES or http_status == 401:
        return AppError(
            ErrorCode.AUTHENTICATION_ERROR,
            "Instagram authentication failed.",
            http_status=401,
            details=details,
        )
    if code in _PERMISSION_CODES or 200 <= code <= 299 or http_status == 403:
        return AppError(
            ErrorCode.PERMISSION_ERROR,
            "Instagram permission denied for this metric.",
            http_status=403,
            details=details,
        )
    if code in _INVALID_METRIC_CODES:
        return AppError(
            ErrorCode.API_ERROR,
            "Instagram does not provide that metric.",
            http_status=400,
            details=details,
        )
    return AppError(
        ErrorCode.API_ERROR,
        "Instagram API request failed.",
        http_status=502 if http_status < 400 else http_status,
        details=details,
    )


class GraphInstagramReader:
    """Profile, media, and insight reads for one connected professional account."""

    def __init__(self, client: InstagramReadClient, account_id: str) -> None:
        self._client = client
        self._account_id = account_id.strip()

    async def read_profile(self) -> dict[str, Any]:
        return await self._client.get_json(
            self._account_id,
            {
                "fields": "id,username,name,biography,followers_count,follows_count,media_count,website",
            },
        )

    async def read_media(self, limit: int = 25) -> list[dict[str, Any]]:
        payload = await self._client.get_json(
            f"{self._account_id}/media",
            {
                "fields": "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count",
                "limit": max(1, min(limit, 50)),
            },
        )
        data = payload.get("data")
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def read_insight(self, object_id: str, metric: str, period: str) -> dict[str, Any]:
        return await self._client.get_json(
            f"{object_id}/insights",
            {"metric": metric, "period": period},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
