"""Canva capability adapter.

Calls only tools discovered from the connected user's Canva MCP session.
Does not publish to Instagram or Meta. Image generation does not depend on it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import sessionmaker

from backend.integrations.canva.catalog import (
    CANCEL_EDIT,
    COMMIT_EDIT,
    CREATE_DESIGN,
    CREATE_FROM_CANDIDATE,
    EXPORT_DESIGN,
    EXPORT_FORMATS,
    GENERATE_DESIGN,
    GET_ASSETS,
    LIST_BRAND_KITS,
    PERFORM_EDIT,
    SEARCH_BRAND_TEMPLATES,
    SEARCH_DESIGNS,
    START_EDIT,
    DiscoveredTool,
    available_capabilities,
    bind_schema,
    clean_operations,
    shape_format,
)
from backend.integrations.canva.contracts import (
    CanvaAccountStatus,
    CanvaBrandAssets,
    CanvaDesignCreation,
    CanvaDesignSearch,
    CanvaEditResult,
    CanvaExportResult,
    CanvaOwner,
    owner_from_user_id,
)
from backend.integrations.canva.mcp_client import CanvaMcpClient
from backend.integrations.canva.normalize import (
    brand_assets,
    continuation_of,
    creation_from_design,
    creation_from_generation,
    export_result,
    search_results,
    supported_formats,
    transaction_id,
)
from backend.integrations.canva.oauth import (
    authorization_code_form,
    authorize_url,
    exchange_token,
    new_code_verifier,
    new_state,
    refresh_form,
    state_expiry,
)
from backend.integrations.canva.store import CanvaConnectionStore
from config import Settings
from models.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

TokenExchange = Callable[[dict[str, str]], Awaitable[dict[str, Any]]]
SessionOpener = Callable[[str], Any]


def _intent(value: str | None, fallback: str) -> str:
    text = (value or fallback).strip() or fallback
    return text[:255]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class CanvaAdapter:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker | None = None,
        *,
        token_exchange: TokenExchange | None = None,
        session_opener: SessionOpener | None = None,
    ) -> None:
        self._settings = settings
        self._store = CanvaConnectionStore(settings, session_factory)
        self.token_exchange = token_exchange or self._default_exchange
        self.session_opener = session_opener

    async def _default_exchange(self, form: dict[str, str]) -> dict[str, Any]:
        return await exchange_token(self._settings, form)

    def _ensure_enabled(self) -> None:
        if not self._settings.canva_enabled:
            raise AppError(
                ErrorCode.CANVA_DISABLED,
                "Canva is disabled. Image generation continues without it.",
                http_status=503,
            )
        if not self._settings.canva_configured:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "Canva is enabled but the OAuth client is not configured.",
                http_status=503,
            )

    def begin_authorization(self, owner: CanvaOwner) -> str:
        self._ensure_enabled()
        state = new_state()
        verifier = new_code_verifier()
        self._store.save_oauth_state(owner, state=state, verifier=verifier, expires_at=state_expiry())
        url = authorize_url(self._settings, state=state, verifier=verifier)
        logger.info(
            "canva_oauth_started",
            extra={"tool": "oauth", "status": "started", "user_id": owner.user_id, "tenant_id": owner.tenant_id},
        )
        return url

    async def complete_authorization(self, owner: CanvaOwner, *, code: str, state: str) -> CanvaAccountStatus:
        self._ensure_enabled()
        if not code.strip() or not state.strip():
            raise AppError(ErrorCode.CANVA_AUTHORIZATION_FAILED, "Canva authorization failed.", http_status=401)
        verifier = self._store.consume_oauth_state(state=state, owner=owner)
        payload = await self.token_exchange(
            authorization_code_form(self._settings, code=code, verifier=verifier)
        )
        self._store.save_tokens(
            owner,
            access_token=str(payload["access_token"]),
            refresh_token=payload.get("refresh_token") if isinstance(payload.get("refresh_token"), str) else None,
            expires_in=payload.get("expires_in") if isinstance(payload.get("expires_in"), int) else None,
        )
        logger.info(
            "canva_oauth_completed",
            extra={"tool": "oauth", "status": "connected", "user_id": owner.user_id, "tenant_id": owner.tenant_id},
        )
        return await self.status(owner)

    def disconnect(self, owner: CanvaOwner) -> None:
        self._store.disconnect(owner)
        logger.info(
            "canva_disconnected",
            extra={"tool": "oauth", "status": "disconnected", "user_id": owner.user_id, "tenant_id": owner.tenant_id},
        )

    async def status(self, owner: CanvaOwner) -> CanvaAccountStatus:
        enabled = bool(self._settings.canva_enabled)
        configured = bool(self._settings.canva_configured)
        if not enabled:
            return CanvaAccountStatus(enabled=False, configured=False, connected=False)
        if not self._store.is_connected(owner):
            return CanvaAccountStatus(enabled=True, configured=configured, connected=False)
        try:
            tools = await self._discovered(owner)
        except AppError as exc:
            if exc.code == ErrorCode.CANVA_AUTHORIZATION_FAILED:
                self._store.mark(owner, "authorization_failed")
                return CanvaAccountStatus(enabled=True, configured=configured, connected=False)
            return CanvaAccountStatus(enabled=True, configured=configured, connected=True)
        return CanvaAccountStatus(
            enabled=True,
            configured=configured,
            connected=True,
            capabilities=available_capabilities(tools),
            tools=sorted(tools),
        )

    async def apply(
        self,
        *,
        user_id: str,
        action: str,
        image_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Optional scheduler hook. Never publishes and never invents tool calls."""
        del image_id, correlation_id
        if not self._settings.canva_enabled:
            return {"applied": False, "reason": "disabled"}
        owner = owner_from_user_id(user_id)
        account = await self.status(owner)
        return {
            "applied": False,
            "reason": "explicit_capability_required",
            "action": action,
            "connected": account.connected,
            "capabilities": account.capabilities,
        }

    async def create_design(
        self,
        owner: CanvaOwner,
        *,
        query: str,
        design_type: str | None = None,
        candidate_id: str | None = None,
        job_id: str | None = None,
        brand_kit_id: str | None = None,
        user_intent: str | None = None,
    ) -> CanvaDesignCreation:
        self._ensure_enabled()
        brief = (query or "").strip()
        selected = (candidate_id or "").strip()
        selected_job = (job_id or "").strip()
        if bool(selected) != bool(selected_job):
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "Choosing a Canva candidate requires both the job id and the candidate id.",
                http_status=400,
            )
        if not brief and not selected:
            raise AppError(ErrorCode.INVALID_REQUEST, "A design brief is required.", http_status=400)
        intent = _intent(user_intent, "Create a Canva design.")
        async with self._session(owner) as session:
            tools = await self._index(session)
            self._require_capability(tools, "create_design")
            if selected:
                return await self._materialize_candidate(
                    session,
                    tools,
                    brief=brief,
                    candidate_id=selected,
                    job_id=selected_job,
                    intent=intent,
                )
            if CREATE_DESIGN in tools:
                try:
                    payload = await self._call(
                        session,
                        tools[CREATE_DESIGN],
                        {
                            "query": brief,
                            "prompt": brief,
                            "design_type": (design_type or "").strip() or None,
                            "brand_kit_id": (brand_kit_id or "").strip() or None,
                            "user_intent": intent,
                        },
                    )
                except AppError as exc:
                    if exc.code != ErrorCode.CANVA_CAPABILITY_UNAVAILABLE or GENERATE_DESIGN not in tools:
                        raise
                else:
                    return _creation_payload(payload, job_id=None)
            if GENERATE_DESIGN not in tools or CREATE_FROM_CANDIDATE not in tools:
                raise AppError(
                    ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
                    "This Canva connection cannot create designs.",
                    http_status=409,
                )
            generated = await self._call(
                session,
                tools[GENERATE_DESIGN],
                {
                    "query": brief,
                    "design_type": (design_type or "").strip() or None,
                    "brand_kit_id": (brand_kit_id or "").strip() or None,
                    "user_intent": intent,
                },
            )
            return creation_from_generation(generated)

    async def _materialize_candidate(
        self,
        session: Any,
        tools: dict[str, DiscoveredTool],
        *,
        brief: str,
        candidate_id: str,
        job_id: str,
        intent: str,
    ) -> CanvaDesignCreation:
        primary = tools.get(CREATE_DESIGN)
        primary_props = (primary.input_schema.get("properties") or {}) if primary is not None else {}
        if primary is not None and ("candidate_id" in primary_props or "job_id" in primary_props):
            payload = await self._call(
                session,
                primary,
                {
                    "query": brief or None,
                    "candidate_id": candidate_id,
                    "job_id": job_id,
                    "user_intent": intent,
                },
            )
            return _creation_payload(payload, job_id=job_id)
        if CREATE_FROM_CANDIDATE not in tools:
            raise AppError(
                ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
                "This Canva connection cannot save a chosen design candidate.",
                http_status=409,
            )
        payload = await self._call(
            session,
            tools[CREATE_FROM_CANDIDATE],
            {"job_id": job_id, "candidate_id": candidate_id, "user_intent": intent},
        )
        return creation_from_design(payload, job_id=job_id)

    async def edit_design(
        self,
        owner: CanvaOwner,
        *,
        design_id: str,
        operations: list[dict[str, Any]],
        page_index: int = 1,
        user_intent: str | None = None,
    ) -> CanvaEditResult:
        self._ensure_enabled()
        if not (design_id or "").strip():
            raise AppError(ErrorCode.INVALID_REQUEST, "A Canva design id is required.", http_status=400)
        if page_index < 1:
            raise AppError(ErrorCode.INVALID_REQUEST, "Canva page indexes start at 1.", http_status=400)
        intent = _intent(user_intent, "Edit the selected Canva design.")
        async with self._session(owner) as session:
            tools = await self._index(session)
            self._require_capability(tools, "edit_design")
            cleaned = clean_operations(tools[PERFORM_EDIT].input_schema, operations)
            if not cleaned:
                raise AppError(ErrorCode.INVALID_REQUEST, "At least one Canva edit operation is required.", http_status=400)
            started = await self._call(
                session,
                tools[START_EDIT],
                {"design_id": design_id.strip(), "user_intent": intent},
            )
            txn = transaction_id(started)
            if not txn:
                raise AppError(
                    ErrorCode.CANVA_UNAVAILABLE,
                    "Canva did not open an editing transaction.",
                    http_status=503,
                )
            try:
                values: dict[str, Any] = {
                    "transaction_id": txn,
                    "page_index": page_index,
                    "operations": cleaned,
                    "user_intent": intent,
                }
                pages = started.get("pages")
                if isinstance(pages, list):
                    values["pages"] = pages
                await self._call(session, tools[PERFORM_EDIT], values)
                committed = await self._call(
                    session,
                    tools[COMMIT_EDIT],
                    {"transaction_id": txn, "user_intent": intent},
                )
            except AppError:
                await self._cancel(session, tools, txn, intent)
                raise
            transaction = committed.get("transaction") if isinstance(committed.get("transaction"), dict) else {}
            status = transaction.get("status") or committed.get("status") or "committed"
            return CanvaEditResult(design_id=design_id.strip(), transaction_id=txn, status=str(status))

    async def search_designs(
        self,
        owner: CanvaOwner,
        *,
        query: str | None = None,
        limit: int | None = None,
        continuation: str | None = None,
        user_intent: str | None = None,
    ) -> CanvaDesignSearch:
        self._ensure_enabled()
        intent = _intent(user_intent, "Search the user's Canva designs.")
        async with self._session(owner) as session:
            tools = await self._index(session)
            self._require_capability(tools, "search_designs")
            tool = tools[SEARCH_DESIGNS]
            values: dict[str, Any] = {
                "query": (query or "").strip() or None,
                "limit": limit,
                "continuation": (continuation or "").strip() or None,
                "user_intent": intent,
            }
            sort_prop = (tool.input_schema.get("properties") or {}).get("sort_by") or {}
            enum = sort_prop.get("enum") if isinstance(sort_prop, dict) else None
            if values["query"] and (not isinstance(enum, list) or "relevance" in enum):
                values["sort_by"] = "relevance"
            payload = await self._call(session, tool, values)
            return search_results(payload)

    async def get_brand_assets(
        self,
        owner: CanvaOwner,
        *,
        query: str | None = None,
        asset_ids: list[str] | None = None,
        limit: int | None = None,
        continuation: str | None = None,
        user_intent: str | None = None,
    ) -> CanvaBrandAssets:
        self._ensure_enabled()
        intent = _intent(user_intent, "List the user's Canva brand assets.")
        async with self._session(owner) as session:
            tools = await self._index(session)
            self._require_capability(tools, "get_brand_assets")
            assets = []
            page: str | None = None
            called = False
            if LIST_BRAND_KITS in tools:
                payload = await self._call(
                    session,
                    tools[LIST_BRAND_KITS],
                    {
                        "limit": limit,
                        "continuation": (continuation or "").strip() or None,
                        "user_intent": intent,
                    },
                )
                assets.extend(brand_assets(payload, kind="brand_kit"))
                page = continuation_of(payload)
                called = True
            if (query or "").strip() and SEARCH_BRAND_TEMPLATES in tools:
                payload = await self._call(
                    session,
                    tools[SEARCH_BRAND_TEMPLATES],
                    {"query": query.strip(), "limit": limit, "user_intent": intent},
                )
                assets.extend(brand_assets(payload, kind="brand_template"))
                called = True
            ids = [item.strip() for item in (asset_ids or []) if item and item.strip()]
            if ids and GET_ASSETS in tools:
                payload = await self._call(
                    session,
                    tools[GET_ASSETS],
                    {"asset_ids": ids, "user_intent": intent},
                )
                assets.extend(brand_assets(payload, kind="asset"))
                called = True
            if not called:
                raise AppError(
                    ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
                    "This Canva connection cannot list brand assets with the supplied arguments.",
                    http_status=409,
                )
            return CanvaBrandAssets(assets=assets, continuation=page)

    async def export_design(
        self,
        owner: CanvaOwner,
        *,
        design_id: str,
        format: str,
        user_intent: str | None = None,
    ) -> CanvaExportResult:
        self._ensure_enabled()
        if not (design_id or "").strip():
            raise AppError(ErrorCode.INVALID_REQUEST, "A Canva design id is required.", http_status=400)
        chosen = (format or "").strip()
        if not chosen:
            raise AppError(ErrorCode.INVALID_REQUEST, "An export format is required.", http_status=400)
        intent = _intent(user_intent, "Export the selected Canva design.")
        async with self._session(owner) as session:
            tools = await self._index(session)
            self._require_capability(tools, "export_design")
            if EXPORT_FORMATS in tools:
                listed = await self._call(
                    session,
                    tools[EXPORT_FORMATS],
                    {"design_id": design_id.strip(), "user_intent": intent},
                )
                formats = supported_formats(listed)
                if formats:
                    match = next((item for item in formats if item.lower() == chosen.lower()), None)
                    if match is None:
                        raise AppError(
                            ErrorCode.INVALID_REQUEST,
                            "That export format is not available for this Canva design.",
                            http_status=400,
                        )
                    chosen = match
            fmt_value = shape_format(tools[EXPORT_DESIGN].input_schema, chosen)
            payload = await self._call(
                session,
                tools[EXPORT_DESIGN],
                {"design_id": design_id.strip(), "format": fmt_value, "user_intent": intent},
            )
            return export_result(payload, format_name=chosen)

    async def _discovered(self, owner: CanvaOwner) -> dict[str, DiscoveredTool]:
        async with self._session(owner) as session:
            return await self._index(session)

    async def _index(self, session: Any) -> dict[str, DiscoveredTool]:
        listed = await session.list_tools()
        return {tool.name: tool for tool in listed}

    def _require_capability(self, tools: dict[str, DiscoveredTool], name: str) -> None:
        if name not in available_capabilities(tools):
            raise AppError(
                ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
                "This Canva connection does not offer that capability.",
                http_status=409,
            )

    async def _call(self, session: Any, tool: DiscoveredTool, values: dict[str, Any]) -> dict[str, Any]:
        arguments = bind_schema(tool.input_schema, values)
        started = time.perf_counter()
        try:
            payload = await session.call_tool(tool.name, arguments)
        except AppError:
            self._log(tool.name, "failed", started)
            raise
        self._log(tool.name, "ok", started)
        if not isinstance(payload, dict):
            return {}
        return payload

    async def _cancel(self, session: Any, tools: dict[str, DiscoveredTool], txn: str, intent: str) -> None:
        tool = tools.get(CANCEL_EDIT)
        if tool is None:
            return
        try:
            await self._call(session, tool, {"transaction_id": txn, "user_intent": intent})
        except AppError:
            logger.info("canva_edit_cancel_failed", extra={"tool": CANCEL_EDIT, "status": "failed"})

    def _log(self, tool: str, status: str, started: float) -> None:
        logger.info(
            "canva_capability",
            extra={"tool": tool, "status": status, "duration_ms": int((time.perf_counter() - started) * 1000)},
        )

    async def _access_token(self, owner: CanvaOwner) -> str:
        stored = self._store.get(owner)
        if stored is None or stored.status != "connected" or not stored.access_token:
            raise AppError(
                ErrorCode.CANVA_NOT_CONNECTED,
                "Canva is not connected for this user.",
                http_status=409,
            )
        if stored.expires_at is None or _aware(stored.expires_at) > datetime.now(timezone.utc) + timedelta(seconds=60):
            return stored.access_token
        if not stored.refresh_token:
            self._store.mark(owner, "authorization_failed")
            raise AppError(ErrorCode.CANVA_AUTHORIZATION_FAILED, "Canva authorization expired.", http_status=401)
        try:
            payload = await self.token_exchange(refresh_form(self._settings, stored.refresh_token))
        except AppError:
            self._store.mark(owner, "authorization_failed")
            raise
        refresh_token = payload.get("refresh_token") if isinstance(payload.get("refresh_token"), str) else stored.refresh_token
        self._store.save_tokens(
            owner,
            access_token=str(payload["access_token"]),
            refresh_token=refresh_token,
            expires_in=payload.get("expires_in") if isinstance(payload.get("expires_in"), int) else None,
        )
        return str(payload["access_token"])

    def _open_session(self, access_token: str) -> Any:
        if self.session_opener is not None:
            return self.session_opener(access_token)
        return CanvaMcpClient(
            endpoint=self._settings.canva_mcp_url,
            access_token=access_token,
            timeout_seconds=self._settings.canva_timeout_seconds,
            allow_unofficial_endpoint=self._settings.canva_allow_unofficial_endpoint,
        )

    @asynccontextmanager
    async def _session(self, owner: CanvaOwner):
        token = await self._access_token(owner)
        session = self._open_session(token)
        try:
            yield session
        finally:
            close = getattr(session, "aclose", None)
            if callable(close):
                await close()


def _creation_payload(payload: dict[str, Any], *, job_id: str | None) -> CanvaDesignCreation:
    created = creation_from_design(payload, job_id=job_id)
    if created.design is not None:
        return created
    return creation_from_generation(payload)


def allowed_success_url(settings: Settings) -> str:
    target = (settings.canva_oauth_success_url or "").strip()
    if not target:
        return ""
    parsed = urlparse(target)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in settings.cors_origin_list():
        return ""
    return target


def get_canva_client(settings: Settings) -> CanvaAdapter | None:
    """Scheduler hook. Returns None when Canva is disabled so image generation is unchanged."""
    if not getattr(settings, "canva_enabled", False):
        return None
    return CanvaAdapter(settings)
