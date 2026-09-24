"""Streamable HTTP client for Canva's official remote MCP server.

Tool names are taken from `tools/list`. `tools/call` refuses any name that was
not in that response.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from backend.integrations.canva.catalog import DiscoveredTool
from backend.integrations.canva.endpoints import require_canva_https_url
from backend.integrations.canva.normalize import strip_secrets
from models.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
_MAX_TOOL_PAGES = 20


class CanvaMcpClient:
    def __init__(
        self,
        *,
        endpoint: str,
        access_token: str,
        timeout_seconds: float,
        allow_unofficial_endpoint: bool = False,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = require_canva_https_url(endpoint, allow_unofficial=allow_unofficial_endpoint)
        self._access_token = access_token
        self._timeout = timeout_seconds
        self._http = http_client
        self._owns_http = http_client is None
        self._session_id = ""
        self._protocol = PROTOCOL_VERSION
        self._request_id = 0
        self._initialized = False
        self._tools: dict[str, DiscoveredTool] | None = None

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def list_tools(self) -> list[DiscoveredTool]:
        tools = await self._tool_index()
        return list(tools.values())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tools = await self._tool_index()
        if name not in tools:
            raise AppError(
                ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
                "That Canva tool is not available on this connection.",
                http_status=409,
            )
        started = time.perf_counter()
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        duration_ms = int((time.perf_counter() - started) * 1000)
        if isinstance(result, dict) and result.get("isError"):
            logger.info(
                "canva_mcp_call",
                extra={"tool": name, "status": "failed", "duration_ms": duration_ms},
            )
            raise AppError(
                ErrorCode.CANVA_UNAVAILABLE,
                "Canva rejected the tool call.",
                http_status=503,
                retryable=True,
            )
        logger.info(
            "canva_mcp_call",
            extra={"tool": name, "status": "ok", "duration_ms": duration_ms},
        )
        return _unwrap_tool_result(result if isinstance(result, dict) else {})

    async def _tool_index(self) -> dict[str, DiscoveredTool]:
        if self._tools is not None:
            return self._tools
        await self._ensure_initialized()
        discovered: dict[str, DiscoveredTool] = {}
        cursor: str | None = None
        for _ in range(_MAX_TOOL_PAGES):
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = await self._rpc("tools/list", params)
            if not isinstance(result, dict):
                break
            for item in result.get("tools") or []:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
                description = item.get("description") if isinstance(item.get("description"), str) else ""
                discovered[item["name"]] = DiscoveredTool(
                    name=item["name"],
                    input_schema=schema,
                    description=description,
                )
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        self._tools = discovered
        logger.info(
            "canva_tool_discovery",
            extra={"tool": "tools/list", "status": "ok"},
        )
        return discovered

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "instagram-agentic-ai", "version": "2.0.0"},
            },
        )
        if isinstance(result, dict) and isinstance(result.get("protocolVersion"), str):
            self._protocol = result["protocolVersion"]
        await self._rpc("notifications/initialized", {}, notification=True)
        self._initialized = True

    async def _rpc(self, method: str, params: dict[str, Any], *, notification: bool = False) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            self._request_id += 1
            payload["id"] = self._request_id
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._protocol,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = await self._post(headers, payload)
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if response.status_code in {401, 403}:
            raise AppError(
                ErrorCode.CANVA_AUTHORIZATION_FAILED,
                "Canva authorization failed.",
                http_status=401,
            )
        if response.status_code >= 400:
            raise AppError(
                ErrorCode.CANVA_UNAVAILABLE,
                "The Canva MCP server rejected the request.",
                http_status=503,
                retryable=response.status_code >= 500,
            )
        if notification or not response.content:
            return None
        body = _parse_mcp_body(response)
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            error = body["error"]
            code = error.get("code")
            message = str(error.get("message") or "").lower()
            if code in {-32001, 401} or "unauthor" in message or "forbidden" in message:
                raise AppError(
                    ErrorCode.CANVA_AUTHORIZATION_FAILED,
                    "Canva authorization failed.",
                    http_status=401,
                )
            raise AppError(
                ErrorCode.CANVA_UNAVAILABLE,
                "The Canva MCP request failed.",
                http_status=503,
                retryable=True,
            )
        if isinstance(body, dict):
            return body.get("result")
        return None

    async def _post(self, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        try:
            return await self._http.post(self._endpoint, headers=headers, json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.CANVA_TIMEOUT,
                "The Canva MCP request timed out.",
                http_status=504,
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise AppError(
                ErrorCode.CANVA_UNAVAILABLE,
                "The Canva MCP server could not be reached.",
                http_status=503,
                retryable=True,
            ) from exc


def _parse_mcp_body(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        messages: list[Any] = []
        data_lines: list[str] = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and data_lines:
                messages.append(_load_sse_data(data_lines))
                data_lines = []
        if data_lines:
            messages.append(_load_sse_data(data_lines))
        for message in reversed(messages):
            if isinstance(message, dict) and ("result" in message or "error" in message or "id" in message):
                return message
        return messages[-1] if messages else {}
    try:
        return response.json()
    except Exception as exc:
        raise AppError(
            ErrorCode.CANVA_UNAVAILABLE,
            "The Canva MCP server returned an unreadable response.",
            http_status=503,
        ) from exc


def _load_sse_data(lines: list[str]) -> Any:
    raw = "\n".join(lines).strip()
    if not raw or raw == "[DONE]":
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(
            ErrorCode.CANVA_UNAVAILABLE,
            "The Canva MCP server returned an unreadable response.",
            http_status=503,
        ) from exc


def _unwrap_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return strip_secrets(structured)
    if isinstance(structured, list):
        return strip_secrets({"items": structured})
    text = _content_text(result.get("content"))
    parsed = _parse_json_text(text) if text else None
    if isinstance(parsed, dict):
        return strip_secrets(parsed)
    if "content" not in result:
        return strip_secrets(result)
    return {}


def _content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts).strip()


def _parse_json_text(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
