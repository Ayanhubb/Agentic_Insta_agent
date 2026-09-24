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

import httpx

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
from backend.integrations.canva.endpoints import allow_export_url
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
ExportFetcher = Callable[[str], Awaitable[bytes]]
_PUBLISH_MARKERS = ("publish", "instagram")
_EXPORT_BYTE_CAP = 8 * 1024 * 1024


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
        export_fetcher: ExportFetcher | None = None,
    ) -> None:
        self._settings = settings
        self._store = CanvaConnectionStore(settings, session_factory)
        self.token_exchange = token_exchange or self._default_exchange
        self.session_opener = session_opener
        self.export_fetcher = export_fetcher

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
        """Scheduler observation hook. The content agent already chose the renderer.

        This does not create a design, does not export, and does not publish.
        """
        del image_id, correlation_id
        normalized = str(action or "").strip().lower()
        if not self._settings.canva_enabled:
            return {"applied": False, "reason": "disabled", "code": ErrorCode.CANVA_DISABLED.value}
        if normalized in {"", "none"}:
            return {"applied": False, "reason": "not_selected", "action": "none"}
        owner = owner_from_user_id(user_id)
        code = self._connection_code(owner)
        if code:
            return {"applied": False, "reason": "not_connected", "code": code, "connected": False, "action": normalized}
        return {
            "applied": False,
            "reason": "rendered_by_content_agent",
            "action": normalized,
            "connected": True,
        }

    async def query(self, *, user_id: str) -> dict[str, Any]:
        """Facts for the creative plan. No tokens, URLs, or invented brand files."""
        owner = owner_from_user_id(user_id)
        empty: dict[str, Any] = {
            "queried": True,
            "connected": False,
            "action": "none",
            "asset_ids": [],
            "capabilities": [],
        }
        code = self._connection_code(owner)
        if code:
            return {**empty, "code": code}
        try:
            listed = await self.get_brand_assets(
                owner,
                query="brand template",
                user_intent="List this account's Canva brand templates and assets.",
            )
            account = await self.status(owner)
        except AppError as exc:
            if exc.code == ErrorCode.CANVA_AUTHORIZATION_FAILED:
                self._store.mark(owner, "authorization_failed")
            if exc.code == ErrorCode.CANVA_CAPABILITY_UNAVAILABLE:
                return {**empty, "connected": True, "code": None}
            mapped = exc.code.value if exc.code.value in {
                ErrorCode.CANVA_NOT_CONNECTED.value,
                ErrorCode.CANVA_AUTHORIZATION_FAILED.value,
                ErrorCode.CANVA_DISABLED.value,
            } else ErrorCode.CANVA_UNAVAILABLE.value
            return {**empty, "code": mapped}
        ids = [item.id for item in listed.assets if item.id]
        action = "none"
        if any(item.kind == "brand_template" for item in listed.assets):
            action = "apply_template"
        elif ids:
            action = "use_reference"
        return {
            "queried": True,
            "connected": bool(account.connected),
            "action": action,
            "asset_ids": ids,
            "capabilities": list(account.capabilities),
            "code": None,
        }

    async def produce(
        self,
        *,
        user_id: str,
        action: str,
        brief: str,
        asset_ids: list[str],
    ) -> dict[str, Any]:
        """Create and export a design for this user. The result has image bytes and no tokens."""
        owner = owner_from_user_id(user_id)
        code = self._connection_code(owner)
        if code == ErrorCode.CANVA_AUTHORIZATION_FAILED.value:
            raise AppError(ErrorCode.CANVA_AUTHORIZATION_FAILED, "Canva authorization expired.", http_status=401)
        if code:
            raise AppError(ErrorCode.CANVA_NOT_CONNECTED, "Canva is not connected for this user.", http_status=409)
        selected = str(getattr(action, "value", action) or "").strip().lower()
        if selected not in {"apply_template", "use_reference"}:
            raise AppError(ErrorCode.INVALID_REQUEST, "Canva was not selected for this creative.", http_status=400)
        requested = [item.strip() for item in asset_ids if item and str(item).strip()]
        if not requested:
            raise AppError(
                ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
                "Canva needs an asset from the connected account.",
                http_status=409,
            )
        text = (brief or "").strip()
        if len(text) < 20:
            raise AppError(ErrorCode.INVALID_REQUEST, "A design brief is required.", http_status=400)
        listed = await self.get_brand_assets(
            owner,
            query=text[:120] if selected == "apply_template" else None,
            asset_ids=requested,
            user_intent="Retrieve authorized Canva assets for this design.",
        )
        by_id = {item.id: item for item in listed.assets}
        if not set(requested) <= set(by_id):
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "Canva can only use assets from the connected account.",
                http_status=403,
            )
        template_id = next((item.id for item in listed.assets if item.kind == "brand_template" and item.id in requested), None)
        brand_kit_id = next((item.id for item in listed.assets if item.kind == "brand_kit" and item.id in requested), None)
        created = await self.create_design(
            owner,
            query=text[:1000],
            design_type="instagram_post",
            brand_kit_id=brand_kit_id,
            template_id=template_id,
            reference_asset_ids=requested,
            user_intent="Create a design from the approved creative brief.",
        )
        if created.design is None and created.job_id and created.candidates:
            created = await self.create_design(
                owner,
                query=text[:1000],
                candidate_id=created.candidates[0].candidate_id,
                job_id=created.job_id,
                user_intent="Save the selected Canva design candidate.",
            )
        if created.design is None or not created.design.id:
            raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva did not return a design.", http_status=503)
        exported = await self.export_design(
            owner,
            design_id=created.design.id,
            format="png",
            user_intent="Export the design for review.",
        )
        image_bytes = await self._download_export(exported)
        return {"provider": "canva", "image_bytes": image_bytes, "mime_type": "image/png"}

    def _connection_code(self, owner: CanvaOwner) -> str | None:
        if not self._settings.canva_enabled:
            return ErrorCode.CANVA_DISABLED.value
        stored = self._store.get(owner)
        if stored is not None and stored.status == "connected" and stored.access_token:
            return None
        if stored is not None and stored.status == "authorization_failed":
            return ErrorCode.CANVA_AUTHORIZATION_FAILED.value
        return ErrorCode.CANVA_NOT_CONNECTED.value

    async def create_design(
        self,
        owner: CanvaOwner,
        *,
        query: str,
        design_type: str | None = None,
        candidate_id: str | None = None,
        job_id: str | None = None,
        brand_kit_id: str | None = None,
        template_id: str | None = None,
        reference_asset_ids: list[str] | None = None,
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
        references = [item.strip() for item in (reference_asset_ids or []) if item and str(item).strip()] or None
        template = (template_id or "").strip() or None
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
                            "template_id": template,
                            "asset_ids": references,
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
                    "template_id": template,
                    "asset_ids": references,
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
        _refuse_publish_tool(tool.name)
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

    async def _download_export(self, exported: CanvaExportResult) -> bytes:
        if exported.status != "success" or not exported.download_urls:
            raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva did not export an image.", http_status=503)
        url = next((item for item in exported.download_urls if allow_export_url(item)), "")
        if not url:
            raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva export URL was rejected.", http_status=503)
        if self.export_fetcher is not None:
            data = await self.export_fetcher(url)
        else:
            data = await _http_get_export(url, timeout=self._settings.canva_timeout_seconds)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva export was empty.", http_status=503)
        if len(data) > _EXPORT_BYTE_CAP:
            raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva export was too large.", http_status=503)
        return bytes(data)

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


def _refuse_publish_tool(name: str) -> None:
    lowered = name.lower().replace("_", "-")
    if any(marker in lowered for marker in _PUBLISH_MARKERS):
        raise AppError(
            ErrorCode.CANVA_CAPABILITY_UNAVAILABLE,
            "Canva cannot publish to Instagram.",
            http_status=409,
        )


async def _http_get_export(url: str, *, timeout: float) -> bytes:
    """Download a pre-signed export. The Canva access token is not sent."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva export could not be downloaded.", http_status=503) from exc
    if response.status_code != 200:
        raise AppError(ErrorCode.CANVA_UNAVAILABLE, "Canva export could not be downloaded.", http_status=503)
    return response.content


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
