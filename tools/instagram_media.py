"""Instagram Graph API client and Agent tools for one-image publishing.

HTTP calls stay in this module. The Agent only sees tool observations.
The contract follows Meta's Content Publishing API:

1. POST /{ig-user-id}/media?image_url=...  → container id
2. GET  /{ig-container-id}?fields=status_code
3. POST /{ig-user-id}/media_publish?creation_id=...  → media id
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from config import ALLOWED_GRAPH_HOSTS, Settings
from models.errors import AppError, ErrorCode, OperationCertainty
from models.observations import Observation
from models.state import AgentState
from tools.base import Tool

logger = logging.getLogger("instagram_agent")

READY_STATUSES = {"FINISHED", "PUBLISHED"}
FAILED_STATUSES = {"ERROR", "EXPIRED"}

# Official Graph API error codes from Meta's error-handling and Instagram
# Platform error-code references. Unknown codes fall through to API_ERROR.
GRAPH_AUTH_CODES = {102, 190, 467}
GRAPH_PERMISSION_CODES = {10}
GRAPH_RATE_LIMIT_CODES = {4, 17, 32, 613}
GRAPH_TRANSIENT_CODES = {1, 2}
GRAPH_INVALID_PARAM_CODES = {100}
GRAPH_INVALID_USER_CODES = {803, 110}
# Instagram publishing: media could not be fetched from the supplied URI.
GRAPH_IMAGE_URL_CODES = {9004, 36000, 36001, 36003}
GRAPH_IMAGE_URL_SUBCODES = {2207052, 2207004, 2207005, 2207009}
GRAPH_DAILY_PUBLISH_SUBCODES = {2207042}


class InstagramClient(Protocol):
    async def create_image_container(
        self, image_url: str, *, task_id: str | None = None
    ) -> dict[str, Any]: ...

    async def get_container_status(self, container_id: str) -> dict[str, Any]: ...

    async def publish_container(
        self, container_id: str, *, task_id: str | None = None
    ) -> dict[str, Any]: ...

    async def get_media(self, media_id: str) -> dict[str, Any]: ...

    async def list_recent_media(self, limit: int = 5) -> list[dict[str, Any]]: ...

    async def get_account(self, account_id: str | None = None) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


def _redact(value: str) -> str:
    lowered = value.lower()
    if any(token in lowered for token in ("access token", "oauth", "bearer ", "secret")):
        return "Instagram rejected the request."
    return value[:300]


def _is_public_https_image_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return False
    if host.endswith(".localhost"):
        return False
    return True


class InstagramMediaService:
    """httpx client for Instagram image container create + publish."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        timeout = httpx.Timeout(settings.request_timeout_seconds)
        self._http = client or httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self._published_containers: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        return self._settings.credentials_configured

    def _require_credentials(self) -> None:
        if not self.configured:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "Instagram publishing is not configured. Set META_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID.",
                http_status=503,
            )

    def _validate_graph_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and self._settings.allow_insecure_graph_http:
            if host in {"127.0.0.1", "localhost"}:
                return
        if parsed.scheme != "https":
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "Instagram Graph API requests must use HTTPS.",
                http_status=503,
            )
        if not self._settings.strict_graph_hosts:
            return
        if host not in ALLOWED_GRAPH_HOSTS:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "Instagram Graph API host is not allowlisted.",
                http_status=503,
            )

    def _validate_image_url(self, image_url: str) -> None:
        if not _is_public_https_image_url(image_url):
            raise AppError(
                ErrorCode.INVALID_IMAGE_URL,
                "Instagram requires a publicly reachable HTTPS image URL.",
                http_status=400,
            )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._settings.meta_access_token}"}

    def _map_graph_error(
        self,
        payload: dict[str, Any],
        http_status: int,
        *,
        fallback: ErrorCode,
        retry_after: float | None,
        certainty: OperationCertainty,
    ) -> AppError:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raw_code = error.get("code")
        try:
            code = int(raw_code or 0)
        except (TypeError, ValueError):
            code = 0
        try:
            subcode = int(error.get("error_subcode") or 0)
        except (TypeError, ValueError):
            subcode = 0
        error_type = str(error.get("type") or "")
        graph_trace_id = error.get("fbtrace_id")
        details = {"graph_code": code, "http_status": http_status}
        if graph_trace_id:
            details["graph_trace_id"] = str(graph_trace_id)
        if subcode:
            details["graph_subcode"] = subcode

        if code in GRAPH_AUTH_CODES or http_status == 401 or error_type == "OAuthException" and code not in GRAPH_RATE_LIMIT_CODES:
            if code in GRAPH_AUTH_CODES or http_status == 401:
                return AppError(
                    ErrorCode.AUTHENTICATION_ERROR,
                    "Instagram authentication failed.",
                    http_status=401,
                    details=details,
                )
        if code in GRAPH_PERMISSION_CODES or 200 <= code <= 299 or http_status == 403:
            return AppError(
                ErrorCode.PERMISSION_ERROR,
                "Instagram permission denied for content publishing.",
                http_status=403,
                details=details,
            )
        if (
            code in GRAPH_RATE_LIMIT_CODES
            or subcode in GRAPH_DAILY_PUBLISH_SUBCODES
            or http_status == 429
        ):
            return AppError(
                ErrorCode.RATE_LIMITED,
                "Instagram rate limit reached.",
                http_status=429,
                retryable=bool(retry_after and 0 < retry_after <= self._settings.max_retry_after_seconds),
                retry_after_seconds=retry_after,
                details=details,
            )
        if code in GRAPH_INVALID_USER_CODES or http_status == 404:
            return AppError(
                ErrorCode.INVALID_ACCOUNT,
                "The configured Instagram professional account could not be used.",
                http_status=400,
                details=details,
            )
        if code in GRAPH_IMAGE_URL_CODES or subcode in GRAPH_IMAGE_URL_SUBCODES:
            return AppError(
                ErrorCode.INVALID_IMAGE_URL,
                "Instagram could not fetch the supplied image URL.",
                http_status=400,
                details=details,
            )
        if http_status in {408, 504}:
            return AppError(
                ErrorCode.TIMEOUT,
                "Instagram API timed out.",
                http_status=504,
                retryable=True,
                certainty=certainty,
                details=details,
            )
        if code in GRAPH_TRANSIENT_CODES or http_status >= 500:
            return AppError(
                ErrorCode.API_ERROR,
                "Instagram API is temporarily unavailable.",
                http_status=502,
                retryable=True,
                certainty=certainty,
                details=details,
            )
        user_message = _redact(str(error.get("error_user_msg") or error.get("message") or "").strip())
        if code in GRAPH_INVALID_PARAM_CODES:
            lowered = user_message.lower()
            if "image_url" in lowered or "uri" in lowered:
                return AppError(
                    ErrorCode.INVALID_IMAGE_URL,
                    user_message or "Instagram rejected the image URL.",
                    http_status=400,
                    details=details,
                )
            if "user" in lowered or "account" in lowered:
                return AppError(
                    ErrorCode.INVALID_ACCOUNT,
                    user_message or "The configured Instagram account id is invalid.",
                    http_status=400,
                    details=details,
                )
        return AppError(
            fallback,
            user_message or "Instagram API request failed.",
            http_status=502,
            certainty=certainty,
            details=details,
        )

    def _parse_retry_after(self, response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        form: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        fallback: ErrorCode,
        certainty_on_transport_failure: OperationCertainty = OperationCertainty.FAILED,
        retry: bool = True,
    ) -> dict[str, Any]:
        self._require_credentials()
        url = f"{self._settings.graph_api_root}/{path.lstrip('/')}"
        self._validate_graph_url(url)

        attempts = self._settings.graph_retry_attempts if retry else 1
        last_error: AppError | None = None
        graph_host = (urlparse(self._settings.meta_graph_api_base_url).hostname or "").lower()
        send_json = graph_host == "graph.instagram.com"
        for attempt in range(1, attempts + 1):
            try:
                request_kwargs: dict[str, Any] = {
                    "headers": self._headers(),
                    "params": params,
                }
                if form is not None:
                    if send_json:
                        request_kwargs["json"] = form
                    else:
                        request_kwargs["data"] = form
                response = await self._http.request(method, url, **request_kwargs)
            except httpx.TimeoutException as exc:
                last_error = AppError(
                    ErrorCode.TIMEOUT,
                    "Instagram API timed out.",
                    http_status=504,
                    retryable=True,
                    certainty=certainty_on_transport_failure,
                )
                if not retry or attempt >= attempts:
                    raise last_error from exc
                await asyncio.sleep(min(0.4 * attempt, self._settings.max_retry_after_seconds))
                continue
            except httpx.HTTPError as exc:
                last_error = AppError(
                    ErrorCode.NETWORK_ERROR,
                    "Unable to reach the Instagram API.",
                    http_status=502,
                    retryable=True,
                    certainty=certainty_on_transport_failure,
                )
                if not retry or attempt >= attempts:
                    raise last_error from exc
                await asyncio.sleep(min(0.4 * attempt, self._settings.max_retry_after_seconds))
                continue

            payload: dict[str, Any]
            try:
                parsed = response.json() if response.content else {}
            except ValueError:
                parsed = {}
            payload = parsed if isinstance(parsed, dict) else {}

            if response.status_code >= 400 or "error" in payload:
                retry_after = self._parse_retry_after(response)
                error = self._map_graph_error(
                    payload,
                    response.status_code,
                    fallback=fallback,
                    retry_after=retry_after,
                    certainty=certainty_on_transport_failure,
                )
                graph_error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                logger.info(
                    "INSTAGRAM_GRAPH_ERROR",
                    extra={
                        "step": path,
                        "status": "failed",
                        "http_status": response.status_code,
                        "error_code": error.code.value,
                        "graph_code": graph_error.get("code"),
                        "graph_subcode": graph_error.get("error_subcode"),
                        "graph_message": _redact(
                            str(graph_error.get("error_user_msg") or graph_error.get("message") or "")
                        ),
                    },
                )
                if error.retryable and retry and attempt < attempts:
                    delay = error.retry_after_seconds or (0.4 * attempt)
                    await asyncio.sleep(min(delay, self._settings.max_retry_after_seconds))
                    last_error = error
                    continue
                raise error

            logger.info(
                "INSTAGRAM_GRAPH_OK",
                extra={
                    "step": path,
                    "status": "success",
                    "http_status": response.status_code,
                },
            )
            return payload

        if last_error:
            raise last_error
        raise AppError(fallback, "Instagram API request failed.", http_status=502)

    async def create_image_container(
        self, image_url: str, *, task_id: str | None = None
    ) -> dict[str, Any]:
        self._validate_image_url(image_url)
        account_id = self._settings.instagram_account_id.strip()
        payload = await self._request(
            "POST",
            f"{account_id}/media",
            form={"image_url": image_url},
            fallback=ErrorCode.MEDIA_CREATION_FAILED,
            certainty_on_transport_failure=OperationCertainty.UNKNOWN,
        )
        container_id = payload.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise AppError(
                ErrorCode.MEDIA_CREATION_FAILED,
                "Instagram did not return a media container ID.",
                http_status=502,
            )
        logger.info(
            "INSTAGRAM_CONTAINER_CREATED",
            extra={"task_id": task_id, "step": "create_instagram_media", "status": "success"},
        )
        return {
            "id": container_id,
            "instagram_container_id": container_id,
            "task_id": task_id,
            "api_response": {"id": container_id},
        }

    async def get_container_status(self, container_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            container_id,
            params={"fields": "status_code"},
            fallback=ErrorCode.MEDIA_CREATION_FAILED,
            retry=True,
        )
        status = str(payload.get("status_code") or "UNKNOWN").upper()
        return {"id": container_id, "status_code": status}

    async def wait_until_ready(self, container_id: str) -> str:
        last_status = "UNKNOWN"
        attempts = max(1, self._settings.container_ready_attempts)
        for attempt in range(attempts):
            status_payload = await self.get_container_status(container_id)
            last_status = status_payload["status_code"]
            if last_status in READY_STATUSES:
                return last_status
            if last_status in FAILED_STATUSES:
                raise AppError(
                    ErrorCode.MEDIA_CREATION_FAILED,
                    "Instagram reported that the media container is not publishable.",
                    http_status=502,
                )
            # Image containers are typically ready as soon as an id is returned.
            if last_status in {"UNKNOWN", ""}:
                return "FINISHED"
            if attempt + 1 < attempts:
                await asyncio.sleep(self._settings.container_ready_delay_seconds)
        raise AppError(
            ErrorCode.MEDIA_CREATION_FAILED,
            "Timed out waiting for Instagram to finish processing the image.",
            http_status=504,
            retryable=True,
            certainty=OperationCertainty.UNKNOWN,
        )

    async def publish_container(
        self, container_id: str, *, task_id: str | None = None
    ) -> dict[str, Any]:
        cached = self._published_containers.get(container_id)
        if cached:
            return {
                "id": cached,
                "instagram_media_id": cached,
                "task_id": task_id,
                "deduplicated": True,
                "api_response": {"id": cached},
            }

        status = "UNKNOWN"
        try:
            status = (await self.get_container_status(container_id))["status_code"]
        except AppError as exc:
            if exc.code not in {ErrorCode.TIMEOUT, ErrorCode.NETWORK_ERROR, ErrorCode.API_ERROR}:
                raise

        if status == "PUBLISHED":
            raise AppError(
                ErrorCode.MEDIA_PUBLISH_FAILED,
                "The media container was already published; refusing to publish again.",
                http_status=409,
                certainty=OperationCertainty.UNKNOWN,
                details={"instagram_container_id": container_id},
            )
        if status in FAILED_STATUSES:
            raise AppError(
                ErrorCode.MEDIA_PUBLISH_FAILED,
                "Instagram reported that the media container is not publishable.",
                http_status=502,
            )
        if status == "IN_PROGRESS":
            status = await self.wait_until_ready(container_id)

        account_id = self._settings.instagram_account_id.strip()
        payload = await self._request(
            "POST",
            f"{account_id}/media_publish",
            form={"creation_id": container_id},
            fallback=ErrorCode.MEDIA_PUBLISH_FAILED,
            certainty_on_transport_failure=OperationCertainty.UNKNOWN,
            retry=False,
        )
        media_id = payload.get("id")
        if not isinstance(media_id, str) or not media_id:
            raise AppError(
                ErrorCode.MEDIA_PUBLISH_FAILED,
                "Instagram did not return a published media ID.",
                http_status=502,
                certainty=OperationCertainty.UNKNOWN,
            )
        self._published_containers[container_id] = media_id
        logger.info(
            "INSTAGRAM_MEDIA_PUBLISHED",
            extra={"task_id": task_id, "step": "publish_instagram_media", "status": "success"},
        )
        return {
            "id": media_id,
            "instagram_media_id": media_id,
            "instagram_container_id": container_id,
            "task_id": task_id,
            "api_response": {"id": media_id},
        }

    async def get_media(self, media_id: str) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            media_id,
            params={"fields": "id,media_type,permalink,timestamp"},
            fallback=ErrorCode.VERIFICATION_FAILED,
        )
        return payload

    async def list_recent_media(self, limit: int = 5) -> list[dict[str, Any]]:
        account_id = self._settings.instagram_account_id.strip()
        payload = await self._request(
            "GET",
            f"{account_id}/media",
            params={"fields": "id,timestamp,media_type", "limit": max(1, min(limit, 10))},
            fallback=ErrorCode.VERIFICATION_FAILED,
        )
        data = payload.get("data")
        return data if isinstance(data, list) else []

    async def get_account(self, account_id: str | None = None) -> dict[str, Any]:
        aid = (account_id or self._settings.instagram_account_id).strip()
        if not aid:
            raise AppError(
                ErrorCode.INVALID_ACCOUNT,
                "An Instagram professional account id is required.",
                http_status=400,
            )
        payload = await self._request(
            "GET",
            aid,
            params={"fields": "id"},
            fallback=ErrorCode.INVALID_ACCOUNT,
            retry=False,
        )
        confirmed = str(payload.get("id") or "").strip()
        if confirmed and confirmed != aid:
            raise AppError(
                ErrorCode.INVALID_ACCOUNT,
                "The Instagram account id did not match the connected professional account.",
                http_status=400,
            )
        return {"id": confirmed or aid}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()


InstagramGraphClient = InstagramMediaService


class InstagramMediaCreator(Tool):
    name = "create_instagram_media"
    purpose = "Create Instagram media container"
    description = "Create an Instagram media container from the hosted image URL."

    def __init__(self, settings: Settings, client: InstagramMediaService) -> None:
        self._settings = settings
        self._client = client

    async def execute(self, state: AgentState) -> Observation:
        if not state.image_url:
            raise AppError(
                ErrorCode.MEDIA_CREATION_FAILED,
                "A public image URL is required before creating media.",
                http_status=500,
            )
        created = await self._client.create_image_container(state.image_url, task_id=state.task_id)
        container_id = str(created["instagram_container_id"])
        status_code = await self._client.wait_until_ready(container_id)
        return Observation(
            success=True,
            tool=self.name,
            data={
                "instagram_container_id": container_id,
                "container_status": status_code,
                "task_id": state.task_id,
                "api_response": created.get("api_response"),
            },
        )


class InstagramPublisher(Tool):
    name = "publish_instagram_media"
    purpose = "Publish image"
    description = "Publish a finished Instagram media container to the feed."

    def __init__(self, client: InstagramMediaService) -> None:
        self._client = client

    async def execute(self, state: AgentState) -> Observation:
        if state.skip_publish:
            return Observation(
                success=True,
                tool=self.name,
                data={
                    "skipped": True,
                    "instagram_media_id": state.instagram_media_id,
                    "reason": "publish skipped because prior result was unknown",
                },
            )
        if not state.instagram_container_id:
            raise AppError(
                ErrorCode.MEDIA_PUBLISH_FAILED,
                "Cannot publish because no Instagram media container exists.",
                http_status=500,
            )
        published = await self._client.publish_container(
            state.instagram_container_id,
            task_id=state.task_id,
        )
        return Observation(
            success=True,
            tool=self.name,
            data={
                "instagram_media_id": published["instagram_media_id"],
                "task_id": state.task_id,
                "api_response": published.get("api_response"),
            },
        )


CreateInstagramMediaTool = InstagramMediaCreator
PublishInstagramMediaTool = InstagramPublisher
