"""Instagram publication gateway.

The LLM and Scheduler create Agent tasks through this module. They never
receive access tokens and never call Graph APIs. Existing tools stay in
control of validate → prepare → upload → create → publish → verify.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.agent import InstagramAgent
from api.task_store import TaskStore
from config import Settings
from db.crypto import decrypt_token, encrypt_token
from db.exceptions import TokenEncryptionError
from models.errors import AppError, ErrorCode, OperationCertainty
from models.state import AgentState, TaskStatus, utcnow
from services.instagram_client import InstagramClient, InstagramGraphClient
from services.logging import log_step, redact_text
from services.publication_store import (
    COUNTABLE_STATUS,
    AccountStatus,
    AgentEventRecord,
    AgentTaskRecord,
    FestivalCampaignRecord,
    InMemoryPublicationStore,
    InstagramAccountRecord,
    InstagramPostRecord,
    PublicationSource,
    PublicationStats,
    PublicationStatus,
    PublicationStore,
    source_to_post_type,
)

logger = logging.getLogger("instagram_agent")

ClientFactory = Callable[[Settings], InstagramClient]
AgentFactory = Callable[..., InstagramAgent]
SECRET_RESPONSE_KEYS = frozenset(
    {
        "access_token",
        "access_token_encrypted",
        "meta_access_token",
        "instagram_access_token",
        "token",
        "authorization",
        "openai_api_key",
        "deepseek_api_key",
        "password",
        "password_hash",
        "jwt_secret",
        "token_encryption_key",
    }
)


class InstagramPublicationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str
    image_path: str
    source: str = PublicationSource.UPLOAD.value
    generated_image_id: str | None = None
    festival_campaign_id: str | None = None
    festival_sequence: int | None = None
    scheduled_date: date | None = None
    instagram_account_id: str | None = None
    original_filename: str | None = None
    request_id: str | None = None
    caption: str | None = None
    wait: bool = True
    allow_environment_fallback: bool = False

    @field_validator("user_id", "image_path")
    @classmethod
    def _required(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("value is required")
        return text

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        text = (value or PublicationSource.UPLOAD.value).strip().upper().replace(" ", "_")
        aliases = {
            "USER_UPLOAD": PublicationSource.UPLOAD.value,
            "MANUAL": PublicationSource.UPLOAD.value,
            "DAILY": PublicationSource.DAILY_AUTOMATION.value,
            "DAILY_RETAIL_POST": PublicationSource.DAILY_AUTOMATION.value,
            "FESTIVAL": PublicationSource.FESTIVAL_AUTOMATION.value,
        }
        return aliases.get(text, text)


class InstagramConnectRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instagram_account_id: str
    access_token: str
    token_expires_at: datetime | None = None

    @field_validator("instagram_account_id", "access_token")
    @classmethod
    def _required(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("value is required")
        return text


def strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key.lower() not in SECRET_RESPONSE_KEYS}


def map_agent_outcome(state: AgentState) -> str:
    if state.status == TaskStatus.COMPLETED and state.instagram_media_id:
        return PublicationStatus.PUBLISHED.value
    if state.error and state.error.code == ErrorCode.AMBIGUOUS_PUBLICATION:
        return PublicationStatus.AMBIGUOUS_PUBLICATION.value
    if state.publish_certainty == OperationCertainty.UNKNOWN and not state.instagram_media_id:
        return PublicationStatus.AMBIGUOUS_PUBLICATION.value
    return PublicationStatus.FAILED.value


def is_account_connected(status: str | None, token: str | None) -> bool:
    return (status or "").upper() == AccountStatus.CONNECTED.value and bool(token)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _token_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    return _as_utc(expires_at) <= datetime.now(timezone.utc)


def publication_block(account: Any) -> tuple[ErrorCode, str] | None:
    """Why this owned account cannot publish. None means it can.

    A missing row and a disconnected row are `INSTAGRAM_NOT_CONNECTED`.
    An expired or empty token is `AUTHENTICATION_ERROR`. This never inspects
    process-level Meta credentials.
    """
    if account is None:
        return (
            ErrorCode.INSTAGRAM_NOT_CONNECTED,
            "Connect an Instagram professional account before publishing.",
        )
    status = str(getattr(account, "status", "") or "").upper()
    token = str(getattr(account, "access_token_encrypted", "") or "").strip()
    expires_at = getattr(account, "token_expires_at", None)
    if status in {"", AccountStatus.DISCONNECTED.value}:
        return (
            ErrorCode.INSTAGRAM_NOT_CONNECTED,
            "Connect an Instagram professional account before publishing.",
        )
    if status == AccountStatus.EXPIRED.value or _token_expired(expires_at):
        return (ErrorCode.AUTHENTICATION_ERROR, "Instagram access token is expired.")
    if status != AccountStatus.CONNECTED.value:
        return (
            ErrorCode.INSTAGRAM_NOT_CONNECTED,
            "Connect an Instagram professional account before publishing.",
        )
    if not token:
        return (ErrorCode.AUTHENTICATION_ERROR, "Instagram access token is missing.")
    return None


def _select_owned_account(
    accounts: list[InstagramAccountRecord],
    requested_instagram_id: str | None,
) -> InstagramAccountRecord | None:
    rows = list(accounts)
    requested = (requested_instagram_id or "").strip()
    if requested:
        rows = [row for row in rows if row.instagram_account_id == requested]
        if not rows:
            return None
    rank = {
        AccountStatus.CONNECTED.value: 0,
        AccountStatus.EXPIRED.value: 1,
        AccountStatus.ERROR.value: 2,
        AccountStatus.DISCONNECTED.value: 3,
    }
    rows.sort(key=lambda row: rank.get((row.status or "").upper(), 9))
    return rows[0] if rows else None


class PublicationGateway:
    """Single publication authority for authenticated and autonomous flows."""

    def __init__(
        self,
        settings: Settings,
        store: PublicationStore | None = None,
        *,
        task_store: TaskStore | None = None,
        client_factory: ClientFactory | None = None,
        agent_factory: AgentFactory | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        session_factory: Callable[[], Any] | None = None,
        instagram_client: InstagramClient | None = None,
    ) -> None:
        self._settings = settings
        self.store = store or InMemoryPublicationStore()
        self.task_store = task_store or TaskStore()
        injected = instagram_client
        self._client_factory = client_factory or (
            (lambda _user_settings: injected) if injected is not None else (lambda user_settings: InstagramGraphClient(user_settings))
        )
        self._agent_factory = agent_factory
        self._sleeper = sleeper
        self._session_factory = session_factory
        self._shared_session = None

    def _agent(self, *, on_change, instagram_client: InstagramClient, settings: Settings) -> InstagramAgent:
        if self._agent_factory is not None:
            try:
                return self._agent_factory(
                    on_change=on_change,
                    instagram_client=instagram_client,
                    settings=settings,
                )
            except TypeError:
                return InstagramAgent(
                    settings,
                    instagram_client=instagram_client,
                    on_change=on_change,
                    sleeper=self._sleeper,
                )
        return InstagramAgent(
            settings,
            instagram_client=instagram_client,
            on_change=on_change,
            sleeper=self._sleeper,
        )

    def _client_for(self, access_token: str, instagram_account_id: str) -> tuple[Settings, InstagramClient]:
        user_settings = self._settings.model_copy(
            update={
                "meta_access_token": access_token,
                "instagram_access_token": access_token,
                "instagram_account_id": instagram_account_id,
                "instagram_ig_user_id": instagram_account_id,
            }
        )
        return user_settings, self._client_factory(user_settings)

    async def connect_account(self, user_id: str, request: InstagramConnectRequest) -> dict[str, Any]:
        _user_settings, client = self._client_for(request.access_token, request.instagram_account_id)
        try:
            getter = getattr(client, "get_account", None)
            if callable(getter):
                await getter(request.instagram_account_id)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                ErrorCode.AUTHENTICATION_ERROR,
                "Instagram authentication failed.",
                http_status=401,
            ) from exc

        encrypted = encrypt_token(self._settings, request.access_token)
        existing = await self.store.get_account(user_id)
        account = InstagramAccountRecord(
            id=existing.id if existing else str(uuid4()),
            user_id=user_id,
            instagram_account_id=request.instagram_account_id,
            access_token_encrypted=encrypted,
            token_expires_at=request.token_expires_at,
            status=AccountStatus.CONNECTED.value,
            connected_at=existing.connected_at if existing else utcnow(),
        )
        saved = await self.store.upsert_account(account)
        self._db_upsert_account(user_id, request.instagram_account_id, encrypted, request.token_expires_at)
        log_step(
            logger,
            event="INSTAGRAM_ACCOUNT_CONNECTED",
            task_id=user_id,
            status="success",
            step="connect",
        )
        return strip_secrets(saved.public_dict())

    async def disconnect_account(self, user_id: str) -> dict[str, Any]:
        account = await self.store.disconnect_account(user_id)
        self._db_disconnect_account(user_id)
        if account is None:
            return {
                "connected": False,
                "status": AccountStatus.DISCONNECTED.value,
                "instagram_account_id": None,
            }
        log_step(
            logger,
            event="INSTAGRAM_ACCOUNT_DISCONNECTED",
            task_id=user_id,
            status="success",
            step="disconnect",
        )
        return strip_secrets(account.public_dict()) | {"connected": False}

    async def account_status(self, user_id: str | None) -> dict[str, Any]:
        if not user_id:
            return strip_secrets(
                {
                    "connected": False,
                    "source": "none",
                    "environment_configured": False,
                    "instagram_account_id": None,
                    "status": AccountStatus.DISCONNECTED.value,
                    "stats": PublicationStats().model_dump(),
                }
            )
        owned = await self._owned_accounts(user_id)
        account = _select_owned_account(owned, None)
        stats = await self.store.stats_for(user_id)
        connected = publication_block(account) is None
        status = account.status if account else AccountStatus.DISCONNECTED.value
        if account is not None and _token_expired(account.token_expires_at):
            status = AccountStatus.EXPIRED.value
        payload = {
            "connected": connected,
            "source": "user" if connected else "none",
            "environment_configured": False,
            "instagram_account_id": account.instagram_account_id if account else None,
            "status": status,
            "connected_at": account.connected_at.isoformat() if account else None,
            "stats": stats.model_dump(),
        }
        return strip_secrets(payload)

    async def resolve_credentials(
        self,
        user_id: str,
        *,
        allow_environment_fallback: bool = False,
        instagram_account_id: str | None = None,
    ) -> tuple[str, str, str | None]:
        """Return this user's decrypted token and Instagram account id.

        Process `META_ACCESS_TOKEN` / `INSTAGRAM_ACCOUNT_ID` are not used unless
        the caller opts in and `Settings.legacy_environment_credentials_allowed()`
        is true. Production never allows that. A requested account id that is
        not owned by `user_id` is rejected.
        """
        requested = (instagram_account_id or "").strip() or None
        owned = await self._owned_accounts(user_id)
        selected = _select_owned_account(owned, requested)
        if selected is None:
            if requested and owned:
                raise AppError(
                    ErrorCode.PERMISSION_ERROR,
                    "You can only publish to your connected Instagram account.",
                    http_status=403,
                )
            if allow_environment_fallback and self._settings.legacy_environment_credentials_allowed():
                env_id = self._settings.instagram_account_id.strip()
                if requested and requested != env_id:
                    raise AppError(
                        ErrorCode.PERMISSION_ERROR,
                        "You can only publish to your connected Instagram account.",
                        http_status=403,
                    )
                log_step(
                    logger,
                    event="INSTAGRAM_LEGACY_ENVIRONMENT_CREDENTIALS",
                    task_id=user_id,
                    status="development",
                    step="credentials",
                )
                return self._settings.meta_access_token, env_id, None
            raise AppError(
                ErrorCode.INSTAGRAM_NOT_CONNECTED,
                "Connect an Instagram professional account before publishing.",
                http_status=409,
            )
        if selected.user_id != user_id:
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "You can only publish to your connected Instagram account.",
                http_status=403,
            )
        await self._raise_if_unusable(selected)
        try:
            token = decrypt_token(self._settings, selected.access_token_encrypted)
        except TokenEncryptionError as exc:
            raise AppError(
                ErrorCode.AUTHENTICATION_ERROR,
                "Instagram access token could not be used.",
                http_status=401,
            ) from exc
        if requested and requested != selected.instagram_account_id:
            raise AppError(
                ErrorCode.PERMISSION_ERROR,
                "You can only publish to your connected Instagram account.",
                http_status=403,
            )
        return token, selected.instagram_account_id, selected.id

    async def enqueue_publication(self, request: InstagramPublicationRequest) -> AgentState:
        source = request.source
        post_type = source_to_post_type(source)
        token, account_id, account_pk = await self.resolve_credentials(
            request.user_id,
            allow_environment_fallback=request.allow_environment_fallback,
            instagram_account_id=request.instagram_account_id,
        )
        await self._guard_duplicates(request, account_id, post_type)

        db_task = await self.store.create_task(
            AgentTaskRecord(
                user_id=request.user_id,
                trigger=source,
                status="pending",
                started_at=utcnow(),
            )
        )
        post = await self.store.create_post(
            InstagramPostRecord(
                user_id=request.user_id,
                instagram_account_id=account_id,
                generated_image_id=request.generated_image_id,
                status=PublicationStatus.PUBLISHING.value,
                post_type=post_type,
                source=source,
                scheduled_date=request.scheduled_date,
                festival_campaign_id=request.festival_campaign_id,
                agent_task_id=db_task.id,
                verified=False,
            )
        )
        self._db_create_task_and_post(request, post, db_task, account_pk)
        state = AgentState(
            request_id=request.request_id,
            user_id=request.user_id,
            instagram_account_pk=account_pk,
            generated_image_id=request.generated_image_id,
            source=source,
            publication_source=source,
            post_type=post_type,
            scheduled_date=request.scheduled_date,
            festival_campaign_id=request.festival_campaign_id,
            instagram_account_ref=account_id,
            db_post_id=post.id,
            db_task_id=db_task.id,
            image_path=request.image_path,
            original_filename=request.original_filename,
            caption=request.caption,
            current_step="pending",
        )
        await self.task_store.save(state, persist=False)
        log_step(
            logger,
            event="INSTAGRAM_TASK_ENQUEUED",
            task_id=state.task_id,
            request_id=state.request_id,
            status="pending",
            step="enqueue",
        )

        if request.wait:
            await self._run_agent(state, token, account_id)
            return await self.task_store.get(state.task_id)

        asyncio.create_task(self._run_agent(state, token, account_id))
        return state

    async def publish_generated_image(
        self,
        *,
        user_id: str,
        image: Any,
        account: Any | None = None,
        post_type: str = PublicationSource.USER_PROMPT.value,
        trigger: str | None = None,
        scheduled_date: date | None = None,
        festival_campaign_id: str | None = None,
        festival_sequence: int | None = None,
        caption: str | None = None,
        wait: bool = True,
        request_id: str | None = None,
    ) -> InstagramPostRecord:
        image_path = getattr(image, "storage_path", None) or getattr(image, "path", None)
        generated_id = getattr(image, "id", None)
        ig_account_id = None
        if account is not None:
            owner = getattr(account, "user_id", None)
            if owner is not None and owner != user_id:
                raise AppError(
                    ErrorCode.PERMISSION_ERROR,
                    "You can only publish to your connected Instagram account.",
                    http_status=403,
                )
            ig_account_id = getattr(account, "instagram_account_id", None)
        state = await self.enqueue_publication(
            InstagramPublicationRequest(
                user_id=user_id,
                image_path=str(image_path),
                source=trigger or post_type,
                generated_image_id=str(generated_id) if generated_id else None,
                festival_campaign_id=festival_campaign_id,
                festival_sequence=festival_sequence,
                scheduled_date=scheduled_date,
                instagram_account_id=ig_account_id,
                original_filename=getattr(image, "filename", None),
                caption=caption,
                wait=wait,
                request_id=request_id,
            )
        )
        if not state.db_post_id:
            raise AppError(ErrorCode.INTERNAL_ERROR, "Publication record was not created.")
        post = await self.store.get_post(state.db_post_id, user_id=user_id)
        if post is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "Publication record was not created.")
        return post

    async def _guard_duplicates(
        self, request: InstagramPublicationRequest, account_id: str, post_type: str
    ) -> None:
        if request.generated_image_id:
            existing = await self.store.find_by_generated_image(request.user_id, request.generated_image_id)
            if existing is not None:
                raise AppError(
                    ErrorCode.DUPLICATE_PUBLICATION,
                    "This generated image already has a publication in progress or completed.",
                    http_status=409,
                    details={"post_id": existing.id, "status": existing.status},
                )
        if post_type == "DAILY_RETAIL_POST":
            scheduled = request.scheduled_date or date.today()
            existing = await self.store.find_blocking_daily(request.user_id, account_id, scheduled)
            if existing is not None:
                raise AppError(
                    ErrorCode.DUPLICATE_PUBLICATION,
                    "A daily Instagram publication already exists for this account and date.",
                    http_status=409,
                    details={"post_id": existing.id, "status": existing.status},
                )

    async def _run_agent(self, state: AgentState, access_token: str, account_id: str) -> AgentState:
        user_settings, client = self._client_for(access_token, account_id)

        async def on_change(updated: AgentState) -> None:
            await self.task_store.save(updated, persist=False)
            if updated.db_task_id:
                await self.store.update_task(
                    AgentTaskRecord(
                        id=updated.db_task_id,
                        user_id=updated.user_id,
                        trigger=updated.publication_source or updated.source or PublicationSource.UPLOAD.value,
                        status=updated.status.value,
                        current_step=updated.current_step,
                        started_at=updated.created_at,
                        completed_at=updated.completed_at,
                        error=updated.error.message if updated.error else None,
                    )
                )

        agent = self._agent(on_change=on_change, instagram_client=client, settings=user_settings)
        try:
            await agent.run(state)
        finally:
            await self._persist_outcome(state)
        return state

    async def _persist_outcome(self, state: AgentState) -> None:
        status = map_agent_outcome(state)
        verified = status == COUNTABLE_STATUS
        state.verified = verified
        if state.db_post_id:
            post = await self.store.get_post(state.db_post_id, user_id=state.user_id)
            if post is not None:
                post.status = status
                post.instagram_media_id = state.instagram_media_id
                post.permalink = state.permalink
                post.verified = verified
                post.published_at = utcnow() if verified else None
                post.error = redact_text(state.error.message) if state.error else None
                await self.store.update_post(post)
                self._db_apply_result(state, post)
        if state.db_task_id:
            await self.store.update_task(
                AgentTaskRecord(
                    id=state.db_task_id,
                    user_id=state.user_id,
                    trigger=state.publication_source or state.source or PublicationSource.UPLOAD.value,
                    status=state.status.value,
                    current_step=state.current_step,
                    started_at=state.created_at,
                    completed_at=state.completed_at or utcnow(),
                    error=redact_text(state.error.message) if state.error else None,
                )
            )
        for event in state.execution_history:
            payload = event.model_dump(by_alias=True, mode="json")
            await self.store.add_event(
                AgentEventRecord(
                    task_id=state.db_task_id or state.task_id,
                    from_state=str(payload.get("from")),
                    to_state=str(payload.get("to")),
                    tool=event.tool,
                    result=event.result,
                )
            )
        self._persist_agent_state_db(state)
        await self.task_store.save(state, persist=False)
        log_step(
            logger,
            event="INSTAGRAM_PUBLICATION_RECORDED",
            task_id=state.task_id,
            request_id=state.request_id,
            status=status,
            step="persist",
            counted=verified,
        )

    async def ensure_campaign(
        self,
        user_id: str,
        *,
        campaign_id: str | None = None,
        festival_name: str,
        festival_date: date | None = None,
        year: int | None = None,
        required_posts: int = 2,
    ) -> FestivalCampaignRecord:
        if campaign_id:
            existing = await self.store.get_campaign(campaign_id, user_id=user_id)
            if existing:
                return existing
        campaign = FestivalCampaignRecord(
            id=campaign_id or str(uuid4()),
            user_id=user_id,
            festival_name=festival_name,
            festival_date=festival_date,
            year=year,
            required_posts=required_posts,
        )
        saved = await self.store.upsert_campaign(campaign)
        return saved

    def _session(self) -> tuple[Any | None, bool]:
        if self._shared_session is not None:
            return self._shared_session, False
        if self._session_factory is None:
            return None, False
        try:
            return self._session_factory(), True
        except Exception:
            logger.warning("Could not open a database session for Instagram persistence")
            return None, False

    def _release_session(self, session: Any | None, owned: bool) -> None:
        if owned and session is not None:
            session.close()

    def _persist_agent_state_db(self, state: AgentState) -> None:
        session, owned = self._session()
        if session is None:
            return
        try:
            from db.persist import persist_agent_state

            persist_agent_state(session, state)
            if owned:
                session.commit()
            else:
                session.flush()
        except Exception:
            if owned:
                session.rollback()
            logger.exception("Could not persist publication outcome for task %s", state.task_id)
        finally:
            self._release_session(session, owned)

    def _db_upsert_account(
        self,
        user_id: str,
        instagram_account_id: str,
        encrypted: str,
        token_expires_at: datetime | None,
    ) -> None:
        session, owned = self._session()
        if session is None:
            return
        try:
            from db.models import User
            from db.repositories import InstagramAccountRepository

            if session.get(User, user_id) is None:
                return
            repo = InstagramAccountRepository(session)
            account = repo.upsert(user_id, instagram_account_id, encrypted)
            account.token_expires_at = token_expires_at
            if owned:
                session.commit()
            else:
                session.flush()
        except Exception:
            if owned:
                session.rollback()
            logger.warning("Could not persist Instagram account", extra={"user_id": user_id})
        finally:
            self._release_session(session, owned)

    def _db_disconnect_account(self, user_id: str) -> None:
        session, owned = self._session()
        if session is None:
            return
        try:
            from db.repositories import InstagramAccountRepository

            repo = InstagramAccountRepository(session)
            account = repo.get_primary(user_id)
            if account is not None:
                repo.disconnect(account)
                if owned:
                    session.commit()
                else:
                    session.flush()
        except Exception:
            if owned:
                session.rollback()
        finally:
            self._release_session(session, owned)

    async def _owned_accounts(self, user_id: str) -> list[InstagramAccountRecord]:
        found: list[InstagramAccountRecord] = []
        memory = await self.store.get_account(user_id)
        if memory is not None and memory.user_id == user_id:
            found.append(memory)
        for row in self._db_list_accounts(user_id):
            if any(item.instagram_account_id == row.instagram_account_id for item in found):
                continue
            found.append(row)
        return found

    async def _raise_if_unusable(self, account: InstagramAccountRecord) -> None:
        block = publication_block(account)
        if block is None:
            return
        code, message = block
        if code == ErrorCode.AUTHENTICATION_ERROR and "expired" in message:
            account.status = AccountStatus.EXPIRED.value
            await self.store.upsert_account(account)
            self._db_mark_expired(account.user_id, account.id)
        http_status = 401 if code == ErrorCode.AUTHENTICATION_ERROR else 409
        raise AppError(code, message, http_status=http_status)

    def _db_mark_expired(self, user_id: str, account_id: str) -> None:
        session, owned = self._session()
        if session is None:
            return
        try:
            from db.models import InstagramAccount

            row = session.get(InstagramAccount, account_id)
            if row is not None and row.user_id == user_id:
                row.status = AccountStatus.EXPIRED.value
                if owned:
                    session.commit()
                else:
                    session.flush()
        except Exception:
            if owned:
                session.rollback()
        finally:
            self._release_session(session, owned)

    def _db_list_accounts(self, user_id: str) -> list[InstagramAccountRecord]:
        session, owned = self._session()
        if session is None:
            return []
        try:
            from db.repositories import InstagramAccountRepository

            copied: list[InstagramAccountRecord] = []
            for row in InstagramAccountRepository(session).list_for_user(user_id):
                if row.user_id != user_id:
                    continue
                copied.append(
                    InstagramAccountRecord(
                        id=row.id,
                        user_id=row.user_id,
                        instagram_account_id=row.instagram_account_id,
                        access_token_encrypted=row.access_token_encrypted,
                        token_expires_at=row.token_expires_at,
                        status=row.status,
                        connected_at=row.connected_at,
                    )
                )
            return copied
        except Exception:
            return []
        finally:
            self._release_session(session, owned)

    def _db_create_task_and_post(
        self,
        request: InstagramPublicationRequest,
        post: InstagramPostRecord,
        task: AgentTaskRecord,
        account_pk: str | None,
    ) -> None:
        # TaskStore + db.persist already write agent_tasks/instagram_posts.
        # A second session here deadlocks SQLite.
        return

    def _db_apply_result(self, state: AgentState, post: InstagramPostRecord) -> None:
        session, owned = self._session()
        if session is None:
            return
        if owned:
            # persist.py owns task/post rows; extra connections lock SQLite.
            self._release_session(session, owned)
            return
        try:
            from db.models import GeneratedImage

            if post.generated_image_id:
                image = session.get(GeneratedImage, post.generated_image_id)
                if image is not None:
                    image.publication_status = post.status
            session.flush()
        except Exception:
            logger.warning("Could not apply publication result", extra={"task_id": state.task_id})


class PublicationService(PublicationGateway):
    """SQLAlchemy-oriented facade. Scheduler/content code may construct this."""

    def __init__(
        self,
        settings: Settings,
        session: Any | None = None,
        store: TaskStore | None = None,
        *,
        instagram_client: InstagramClient | None = None,
        **kwargs: Any,
    ) -> None:
        session_factory = kwargs.pop("session_factory", None)
        kwargs.pop("media", None)
        super().__init__(
            settings,
            task_store=store,
            instagram_client=instagram_client,
            session_factory=session_factory,
        )
        self._shared_session = session
