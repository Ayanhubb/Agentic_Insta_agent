"""Repository layer. All queries live here — never in FastAPI route handlers."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Iterable

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.base import utcnow
from db.crypto import TokenEncryptor
from db.enums import (
    DEFAULT_TIMEZONE,
    AccountStatus,
    ApprovalStatus,
    FestivalPostStatus,
    GenerationStatus,
    ImageSource,
    JobStatus,
    JobType,
    PostStatus,
    PostType,
    TaskTrigger,
    TaskType,
)
from db.exceptions import DuplicateRecordError, RecordNotFoundError
from db.models import (
    AgentEvent,
    AgentTask,
    AuthSession,
    AutomationSettings,
    BusinessProfile,
    DailyPostSlot,
    FestivalCampaign,
    FestivalPost,
    GeneratedImage,
    InstagramAccount,
    InstagramPost,
    ScheduledJob,
    User,
)
from db.schemas import (
    AgentEventCreate,
    AgentTaskCreate,
    AutomationSettingsWrite,
    BusinessProfileWrite,
    FestivalCampaignCreate,
    FestivalPostCreate,
    GeneratedImageCreate,
    InstagramAccountCreate,
    InstagramPostCreate,
    ScheduledJobCreate,
    UserCreate,
)


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def commit_or_raise(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateRecordError.from_integrity(exc) from exc


def flush_or_raise(session: Session) -> None:
    try:
        session.flush()
    except IntegrityError as exc:
        orig = str(getattr(exc, "orig", exc)).lower()
        session.rollback()
        if "unique" in orig or "uq_" in orig:
            raise DuplicateRecordError.from_integrity(exc) from exc
        raise


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, data: UserCreate | None = None, **kwargs: Any) -> User:
        if data is None:
            data = UserCreate(**kwargs)
        user = User(
            email=str(data.email).strip().lower(),
            password_hash=data.password_hash,
            is_active=data.is_active,
            is_admin=data.is_admin,
            must_change_password=data.must_change_password,
        )
        self.session.add(user)
        flush_or_raise(self.session)
        return user

    def get_by_id(self, user_id: str) -> User | None:
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        normalized = email.strip().lower()
        return self.session.scalar(select(User).where(User.email == normalized))

    def list_users(self, *, offset: int = 0, limit: int = 50) -> list[User]:
        stmt = select(User).order_by(User.created_at.asc()).offset(offset).limit(limit)
        return list(self.session.scalars(stmt))

    def list_all(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.created_at.asc())))

    def list_active(self) -> list[User]:
        return list(self.session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.created_at.asc())))

    def update(self, user_id: str, **fields: Any) -> User:
        user = self.get_by_id(user_id)
        if user is None:
            raise RecordNotFoundError("users", user_id)
        if "email" in fields and fields["email"] is not None:
            fields["email"] = str(fields["email"]).strip().lower()
        for key, value in fields.items():
            if hasattr(user, key) and key not in {"id", "created_at"}:
                setattr(user, key, value)
        user.updated_at = utcnow()
        flush_or_raise(self.session)
        return user


class InstagramAccountRepository:
    def __init__(self, session: Session, encryptor: TokenEncryptor | None = None) -> None:
        self.session = session
        self.encryptor = encryptor

    def _require_encryptor(self) -> TokenEncryptor:
        if self.encryptor is None:
            raise ValueError("TOKEN_ENCRYPTION_KEY is required to store Instagram tokens.")
        return self.encryptor

    def create(self, data: InstagramAccountCreate | None = None, **kwargs: Any) -> InstagramAccount:
        if data is None and {"user_id", "instagram_account_id", "access_token"} <= set(kwargs):
            data = InstagramAccountCreate(**kwargs)
        if data is not None:
            encrypted = self._require_encryptor().encrypt(data.access_token)
            account = InstagramAccount(
                user_id=data.user_id,
                instagram_account_id=data.instagram_account_id,
                access_token_encrypted=encrypted,
                token_expires_at=data.token_expires_at,
                status=_value(data.status),
            )
            self.session.add(account)
            flush_or_raise(self.session)
            return account
        raise ValueError("InstagramAccountCreate or access_token kwargs are required.")

    def upsert(self, user_id: str, instagram_account_id: str, access_token_encrypted: str) -> InstagramAccount:
        account = self.get_by_ig_user(user_id, instagram_account_id)
        if account is None:
            account = InstagramAccount(
                user_id=user_id,
                instagram_account_id=instagram_account_id,
                access_token_encrypted=access_token_encrypted,
                status=AccountStatus.CONNECTED.value,
            )
            self.session.add(account)
        else:
            account.access_token_encrypted = access_token_encrypted
            account.status = AccountStatus.CONNECTED.value
            account.updated_at = utcnow()
        flush_or_raise(self.session)
        return account

    def get_primary(self, user_id: str) -> InstagramAccount | None:
        connected = self.session.scalar(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user_id,
                InstagramAccount.status == AccountStatus.CONNECTED.value,
            )
        )
        if connected is not None:
            return connected
        return self.session.scalar(select(InstagramAccount).where(InstagramAccount.user_id == user_id))

    def disconnect(self, account: InstagramAccount) -> InstagramAccount:
        account.status = AccountStatus.DISCONNECTED.value
        account.updated_at = utcnow()
        flush_or_raise(self.session)
        return account

    def get_owned(self, user_id: str, account_pk: str) -> InstagramAccount | None:
        return self.session.scalar(
            select(InstagramAccount).where(InstagramAccount.id == account_pk, InstagramAccount.user_id == user_id)
        )

    def get_by_ig_user(self, user_id: str, instagram_account_id: str) -> InstagramAccount | None:
        return self.session.scalar(
            select(InstagramAccount).where(
                InstagramAccount.user_id == user_id,
                InstagramAccount.instagram_account_id == instagram_account_id,
            )
        )

    def list_for_user(self, user_id: str) -> list[InstagramAccount]:
        return list(
            self.session.scalars(select(InstagramAccount).where(InstagramAccount.user_id == user_id))
        )

    def get_access_token(self, user_id: str, account_pk: str) -> str:
        account = self.get_owned(user_id, account_pk)
        if account is None:
            raise RecordNotFoundError("instagram_accounts", account_pk)
        return self._require_encryptor().decrypt(account.access_token_encrypted)

    def update_token(
        self,
        user_id: str,
        account_pk: str,
        access_token: str,
        *,
        token_expires_at: datetime | None = None,
        status: AccountStatus = AccountStatus.CONNECTED,
    ) -> InstagramAccount:
        account = self.get_owned(user_id, account_pk)
        if account is None:
            raise RecordNotFoundError("instagram_accounts", account_pk)
        account.access_token_encrypted = self._require_encryptor().encrypt(access_token)
        account.token_expires_at = token_expires_at
        account.status = _value(status)
        account.updated_at = utcnow()
        flush_or_raise(self.session)
        return account


class BusinessProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: str) -> BusinessProfile | None:
        return self.session.scalar(select(BusinessProfile).where(BusinessProfile.user_id == user_id))

    def upsert(self, user_id: str, data: BusinessProfileWrite | None = None, **kwargs: Any) -> BusinessProfile:
        payload = data.model_dump() if data is not None else kwargs
        profile = self.get_for_user(user_id)
        if profile is None:
            profile = BusinessProfile(user_id=user_id, **payload)
            self.session.add(profile)
        else:
            for key, value in payload.items():
                setattr(profile, key, value)
            profile.updated_at = utcnow()
        flush_or_raise(self.session)
        return profile


class GeneratedImageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, data: GeneratedImageCreate | None = None, **kwargs: Any) -> GeneratedImage:
        payload = data.model_dump() if data is not None else dict(kwargs)
        for key in ("generation_status", "approval_status", "source", "publication_status"):
            if key in payload:
                payload[key] = _value(payload[key])
        allowed = {column.key for column in GeneratedImage.__table__.columns}
        image = GeneratedImage(**{key: value for key, value in payload.items() if key in allowed})
        self.session.add(image)
        flush_or_raise(self.session)
        return image

    def get_owned(self, user_id: str, image_id: str) -> GeneratedImage | None:
        return self.session.scalar(
            select(GeneratedImage).where(GeneratedImage.id == image_id, GeneratedImage.user_id == user_id)
        )

    def list_for_user(self, user_id: str, *, limit: int | None = None) -> list[GeneratedImage]:
        stmt = select(GeneratedImage).where(GeneratedImage.user_id == user_id).order_by(GeneratedImage.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def mark_approved(self, user_id: str, image_id: str) -> GeneratedImage:
        image = self.get_owned(user_id, image_id)
        if image is None:
            raise RecordNotFoundError("generated_images", image_id)
        image.approval_status = ApprovalStatus.APPROVED.value
        image.approved_at = utcnow()
        flush_or_raise(self.session)
        return image

    def mark_generation(self, user_id: str, image_id: str, status: GenerationStatus, **fields: Any) -> GeneratedImage:
        image = self.get_owned(user_id, image_id)
        if image is None:
            raise RecordNotFoundError("generated_images", image_id)
        image.generation_status = _value(status)
        for key, value in fields.items():
            if hasattr(image, key):
                setattr(image, key, value)
        flush_or_raise(self.session)
        return image


class InstagramPostRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, data: InstagramPostCreate | None = None, **kwargs: Any) -> InstagramPost:
        if data is None:
            kwargs = dict(kwargs)
            kwargs["status"] = _value(kwargs.get("status", PostStatus.GENERATED))
            kwargs["post_type"] = _value(kwargs.get("post_type", PostType.USER_PROMPT))
            if kwargs.get("id"):
                post = InstagramPost(
                    id=str(kwargs["id"]),
                    user_id=kwargs.get("user_id"),
                    instagram_account_id=kwargs.get("instagram_account_id"),
                    generated_image_id=kwargs.get("generated_image_id"),
                    instagram_media_id=kwargs.get("instagram_media_id"),
                    permalink=kwargs.get("permalink"),
                    status=kwargs["status"],
                    post_type=kwargs["post_type"],
                    published_at=kwargs.get("published_at"),
                    error=kwargs.get("error"),
                    scheduled_date=kwargs.get("scheduled_date"),
                    agent_task_id=kwargs.get("agent_task_id"),
                )
                self.session.add(post)
                flush_or_raise(self.session)
                return post
            data = InstagramPostCreate(**kwargs)
        status = _value(data.status)
        post_type = _value(data.post_type)
        if status == PostStatus.PUBLISHED.value:
            if not data.verified:
                raise ValueError("Only VERIFIED successful publications may have status=PUBLISHED.")
            if not data.instagram_media_id:
                raise ValueError("A verified publication requires instagram_media_id.")
        if post_type == PostType.DAILY_RETAIL_POST.value and data.scheduled_date is None:
            raise ValueError("DAILY_RETAIL_POST requires scheduled_date for duplicate protection.")
        post = InstagramPost(
            **({"id": data.id} if data.id else {}),
            user_id=data.user_id,
            instagram_account_id=data.instagram_account_id,
            generated_image_id=data.generated_image_id,
            instagram_media_id=data.instagram_media_id,
            permalink=data.permalink,
            status=status,
            post_type=post_type,
            published_at=data.published_at,
            error=data.error,
            scheduled_date=data.scheduled_date,
            agent_task_id=data.agent_task_id,
        )
        self.session.add(post)
        flush_or_raise(self.session)
        return post

    def get_owned(self, user_id: str, post_id: str) -> InstagramPost | None:
        return self.session.scalar(
            select(InstagramPost).where(InstagramPost.id == post_id, InstagramPost.user_id == user_id)
        )

    def get_by_id(self, post_id: str) -> InstagramPost | None:
        return self.session.get(InstagramPost, post_id)

    def list_for_user(
        self, user_id: str, *, post_type: PostType | str | None = None, limit: int | None = None
    ) -> list[InstagramPost]:
        stmt: Select[tuple[InstagramPost]] = select(InstagramPost).where(InstagramPost.user_id == user_id)
        if post_type is not None:
            stmt = stmt.where(InstagramPost.post_type == _value(post_type))
        stmt = stmt.order_by(InstagramPost.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def list_recent(
        self,
        user_id: str,
        *,
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
        published_only: bool = False,
    ) -> list[InstagramPost]:
        stmt: Select[tuple[InstagramPost]] = select(InstagramPost).where(InstagramPost.user_id == user_id)
        if published_only:
            stmt = stmt.where(InstagramPost.status == PostStatus.PUBLISHED.value)
        if start is not None:
            column = InstagramPost.published_at if published_only else InstagramPost.created_at
            stmt = stmt.where(column >= start)
        if end is not None:
            column = InstagramPost.published_at if published_only else InstagramPost.created_at
            stmt = stmt.where(column < end)
        order = InstagramPost.published_at if published_only else InstagramPost.created_at
        stmt = stmt.order_by(order.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def latest_published_at(self, user_id: str) -> datetime | None:
        return self.session.scalar(
            select(func.max(InstagramPost.published_at)).where(
                InstagramPost.user_id == user_id,
                InstagramPost.status == PostStatus.PUBLISHED.value,
            )
        )

    def count_grouped(
        self,
        user_id: str,
        column: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        published_only: bool = False,
    ) -> list[tuple[str, int]]:
        field = InstagramPost.post_type if column == "post_type" else InstagramPost.status
        stmt = select(field, func.count()).where(InstagramPost.user_id == user_id)
        if published_only:
            stmt = stmt.where(InstagramPost.status == PostStatus.PUBLISHED.value)
        moment = InstagramPost.published_at if published_only else InstagramPost.created_at
        if start is not None:
            stmt = stmt.where(moment >= start)
        if end is not None:
            stmt = stmt.where(moment < end)
        stmt = stmt.group_by(field)
        rows = self.session.execute(stmt).all()
        return [(str(label), int(count)) for label, count in rows]

    def has_published_daily_post(
        self, user_id: str, instagram_account_id: str | None, scheduled_date: date
    ) -> bool:
        conditions = [
            InstagramPost.user_id == user_id,
            InstagramPost.scheduled_date == scheduled_date,
            InstagramPost.post_type == PostType.DAILY_RETAIL_POST.value,
            InstagramPost.status == PostStatus.PUBLISHED.value,
        ]
        if instagram_account_id:
            conditions.append(InstagramPost.instagram_account_id == instagram_account_id)
        stmt = select(func.count()).select_from(InstagramPost).where(*conditions)
        return int(self.session.scalar(stmt) or 0) > 0

    def has_published_daily(self, user_id: str, instagram_account_id: str | None, scheduled_date: date) -> bool:
        return self.has_published_daily_post(user_id, instagram_account_id or "", scheduled_date)

    def count_published(
        self,
        user_id: str,
        *,
        instagram_account_id: str | None = None,
        post_type: PostType | str | None = None,
        since: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(InstagramPost).where(
            InstagramPost.user_id == user_id,
            InstagramPost.status == PostStatus.PUBLISHED.value,
        )
        if instagram_account_id is not None:
            stmt = stmt.where(InstagramPost.instagram_account_id == instagram_account_id)
        if post_type is not None:
            stmt = stmt.where(InstagramPost.post_type == _value(post_type))
        if since is not None:
            stmt = stmt.where(InstagramPost.published_at >= since)
        return int(self.session.scalar(stmt) or 0)

    def mark_published(
        self,
        post_id: str,
        *,
        user_id: str | None,
        instagram_media_id: str,
        permalink: str | None = None,
        published_at: datetime | None = None,
        verified: bool = True,
    ) -> InstagramPost:
        if not verified:
            raise ValueError("Only VERIFIED successful publications may have status=PUBLISHED.")
        if not instagram_media_id:
            raise ValueError("A verified publication requires instagram_media_id.")
        post = self.session.get(InstagramPost, post_id)
        if post is None:
            raise RecordNotFoundError("instagram_posts", post_id)
        if user_id is not None and post.user_id != user_id:
            raise RecordNotFoundError("instagram_posts", post_id)
        post.status = PostStatus.PUBLISHED.value
        post.instagram_media_id = instagram_media_id
        post.permalink = permalink
        post.published_at = published_at or utcnow()
        post.error = None
        flush_or_raise(self.session)
        return post

    def upsert_from_publication(
        self,
        *,
        user_id: str | None,
        instagram_account_id: str | None,
        generated_image_id: str | None,
        agent_task_id: str | None,
        status: PostStatus,
        post_type: PostType,
        instagram_media_id: str | None,
        permalink: str | None,
        published_at: datetime | None,
        error: str | None,
        scheduled_date: date | None,
        verified: bool,
        id: str | None = None,
    ) -> InstagramPost | None:
        if status == PostStatus.PUBLISHED and (not verified or not instagram_media_id):
            status = PostStatus.AMBIGUOUS_PUBLICATION if instagram_media_id is None else PostStatus.FAILED
        existing = None
        if id:
            existing = self.session.get(InstagramPost, id)
        if existing is None and agent_task_id:
            existing = self.session.scalar(
                select(InstagramPost).where(InstagramPost.agent_task_id == agent_task_id)
            )
        if existing is None:
            return self.create(
                InstagramPostCreate(
                    id=id,
                    user_id=user_id,
                    instagram_account_id=instagram_account_id,
                    generated_image_id=generated_image_id,
                    instagram_media_id=instagram_media_id,
                    permalink=permalink,
                    status=status,
                    post_type=post_type,
                    published_at=published_at,
                    error=error,
                    scheduled_date=scheduled_date,
                    agent_task_id=agent_task_id,
                    verified=verified,
                )
            )
        existing.user_id = user_id or existing.user_id
        existing.instagram_account_id = instagram_account_id or existing.instagram_account_id
        existing.generated_image_id = generated_image_id or existing.generated_image_id
        existing.instagram_media_id = instagram_media_id or existing.instagram_media_id
        existing.permalink = permalink or existing.permalink
        existing.status = _value(status)
        existing.post_type = _value(post_type)
        existing.published_at = published_at
        existing.error = error
        existing.scheduled_date = scheduled_date or existing.scheduled_date
        existing.agent_task_id = agent_task_id or existing.agent_task_id
        flush_or_raise(self.session)
        return existing


class AgentTaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, data: AgentTaskCreate | None = None, **kwargs: Any) -> AgentTask:
        if data is None:
            payload = {key: _value(value) for key, value in kwargs.items()}
            data = AgentTaskCreate(**{k: v for k, v in payload.items() if k in AgentTaskCreate.model_fields})
        payload = data.model_dump()
        payload["task_type"] = _value(payload.get("task_type"))
        payload["trigger"] = _value(payload.get("trigger"))
        payload["status"] = _value(payload.get("status"))
        task = self.session.get(AgentTask, data.id)
        if task is None:
            task = AgentTask(**payload)
            self.session.add(task)
        else:
            for key, value in payload.items():
                if key == "id":
                    continue
                setattr(task, key, value)
        flush_or_raise(self.session)
        return task

    def create(self, **kwargs: Any) -> AgentTask:
        return self.upsert(**kwargs)

    def events_for_task(self, task_id: str) -> list[AgentEvent]:
        return AgentEventRepository(self.session).list_for_task(task_id)

    def get(self, task_id: str) -> AgentTask | None:
        return self.session.get(AgentTask, task_id)

    def get_owned(self, user_id: str, task_id: str) -> AgentTask | None:
        return self.session.scalar(select(AgentTask).where(AgentTask.id == task_id, AgentTask.user_id == user_id))

    def list_for_user(self, user_id: str) -> list[AgentTask]:
        return list(
            self.session.scalars(select(AgentTask).where(AgentTask.user_id == user_id).order_by(AgentTask.created_at.desc()))
        )


class AgentEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append(self, data: AgentEventCreate) -> AgentEvent:
        event = AgentEvent(
            task_id=data.task_id,
            from_state=data.from_state,
            to_state=data.to_state,
            tool=data.tool,
            result=data.result,
            observation=data.observation,
            timestamp=data.timestamp or utcnow(),
        )
        self.session.add(event)
        flush_or_raise(self.session)
        return event

    def replace_for_task(self, task_id: str, events: Iterable[AgentEventCreate]) -> list[AgentEvent]:
        existing = list(self.session.scalars(select(AgentEvent).where(AgentEvent.task_id == task_id)))
        for row in existing:
            self.session.delete(row)
        created: list[AgentEvent] = []
        for item in events:
            created.append(self.append(item.model_copy(update={"task_id": task_id})))
        return created

    def list_for_task(self, task_id: str) -> list[AgentEvent]:
        return list(
            self.session.scalars(select(AgentEvent).where(AgentEvent.task_id == task_id).order_by(AgentEvent.timestamp.asc()))
        )


class ScheduledJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, data: ScheduledJobCreate) -> ScheduledJob:
        job = ScheduledJob(**data.model_dump())
        self.session.add(job)
        flush_or_raise(self.session)
        return job

    def get_owned(self, user_id: str, job_id: str) -> ScheduledJob | None:
        return self.session.scalar(select(ScheduledJob).where(ScheduledJob.id == job_id, ScheduledJob.user_id == user_id))

    def list_for_user(self, user_id: str) -> list[ScheduledJob]:
        return list(self.session.scalars(select(ScheduledJob).where(ScheduledJob.user_id == user_id)))

    def get_by_account_and_type(
        self, user_id: str, instagram_account_id: str | None, job_type: JobType
    ) -> ScheduledJob | None:
        return self.session.scalar(
            select(ScheduledJob).where(
                ScheduledJob.user_id == user_id,
                ScheduledJob.instagram_account_id == instagram_account_id,
                ScheduledJob.job_type == _value(job_type),
            )
        )

    def upsert(self, user_id: str, job_type: str, **fields: Any) -> ScheduledJob:
        job = self.session.scalar(
            select(ScheduledJob).where(ScheduledJob.user_id == user_id, ScheduledJob.job_type == str(job_type))
        )
        if job is None:
            job = ScheduledJob(user_id=user_id, job_type=str(job_type), **{k: v for k, v in fields.items() if hasattr(ScheduledJob, k)})
            self.session.add(job)
        else:
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = utcnow()
        flush_or_raise(self.session)
        return job


class FestivalCampaignRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, data: FestivalCampaignCreate) -> FestivalCampaign:
        year = data.year if data.year is not None else data.festival_date.year
        campaign = FestivalCampaign(
            user_id=data.user_id,
            festival_name=data.festival_name,
            festival_date=data.festival_date,
            year=year,
            required_posts=data.required_posts,
            enabled=data.enabled,
        )
        self.session.add(campaign)
        flush_or_raise(self.session)
        return campaign

    def get_owned(self, user_id: str, campaign_id: str) -> FestivalCampaign | None:
        return self.session.scalar(
            select(FestivalCampaign).where(FestivalCampaign.id == campaign_id, FestivalCampaign.user_id == user_id)
        )

    def list_for_user(self, user_id: str) -> list[FestivalCampaign]:
        return list(self.session.scalars(select(FestivalCampaign).where(FestivalCampaign.user_id == user_id)))

    def list_campaigns(self, user_id: str) -> list[FestivalCampaign]:
        return list(
            self.session.scalars(
                select(FestivalCampaign)
                .where(FestivalCampaign.user_id == user_id)
                .order_by(FestivalCampaign.festival_date.asc())
            )
        )

    def get_or_create_campaign(
        self,
        user_id: str,
        *,
        festival_name: str,
        festival_date: date,
        year: int,
        required_posts: int = 2,
        enabled: bool = True,
    ) -> FestivalCampaign:
        existing = self.session.scalar(
            select(FestivalCampaign).where(
                FestivalCampaign.user_id == user_id,
                FestivalCampaign.festival_name == festival_name,
                FestivalCampaign.year == year,
            )
        )
        if existing is not None:
            return existing
        return self.create(
            FestivalCampaignCreate(
                user_id=user_id,
                festival_name=festival_name,
                festival_date=festival_date,
                year=year,
                required_posts=required_posts,
                enabled=enabled,
            )
        )

    def add_festival_post(self, campaign: FestivalCampaign, sequence_number: int, **kwargs: Any) -> FestivalPost:
        post = FestivalPost(campaign_id=campaign.id, sequence_number=sequence_number, **kwargs)
        self.session.add(post)
        campaign.generated_posts = int(campaign.generated_posts or 0) + 1
        flush_or_raise(self.session)
        return post

    def next_sequence(self, campaign: FestivalCampaign) -> int:
        current = self.session.scalar(
            select(func.max(FestivalPost.sequence_number)).where(FestivalPost.campaign_id == campaign.id)
        )
        return int(current or 0) + 1

    def reusable_festival_post(self, campaign: FestivalCampaign) -> FestivalPost | None:
        return self.session.scalar(
            select(FestivalPost)
            .where(
                FestivalPost.campaign_id == campaign.id,
                FestivalPost.status.in_(("FAILED", "PENDING", "GENERATION_FAILED")),
            )
            .order_by(FestivalPost.sequence_number.asc())
        )

    def increment_generated(self, campaign_id: str) -> FestivalCampaign:
        campaign = self.session.get(FestivalCampaign, campaign_id)
        if campaign is None:
            raise RecordNotFoundError("festival_campaigns", campaign_id)
        campaign.generated_posts += 1
        campaign.updated_at = utcnow()
        flush_or_raise(self.session)
        return campaign

    def refresh_published_count(self, campaign_id: str) -> FestivalCampaign:
        flush_or_raise(self.session)
        campaign = self.session.get(FestivalCampaign, campaign_id)
        if campaign is None:
            raise RecordNotFoundError("festival_campaigns", campaign_id)
        count = self.session.scalar(
            select(func.count()).select_from(FestivalPost).where(
                FestivalPost.campaign_id == campaign_id,
                FestivalPost.status == FestivalPostStatus.PUBLISHED.value,
            )
        )
        campaign.published_posts = int(count or 0)
        campaign.updated_at = utcnow()
        flush_or_raise(self.session)
        return campaign


class FestivalPostRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, data: FestivalPostCreate) -> FestivalPost:
        post = FestivalPost(**data.model_dump())
        self.session.add(post)
        flush_or_raise(self.session)
        return post

    def list_for_campaign(self, campaign_id: str) -> list[FestivalPost]:
        return list(
            self.session.scalars(
                select(FestivalPost)
                .where(FestivalPost.campaign_id == campaign_id)
                .order_by(FestivalPost.sequence_number.asc())
            )
        )

    def get(self, festival_post_id: str) -> FestivalPost | None:
        return self.session.get(FestivalPost, festival_post_id)

    def mark_published(
        self,
        festival_post_id: str,
        *,
        instagram_post_id: str | None = None,
        published_at: datetime | None = None,
    ) -> FestivalPost:
        row = self.session.get(FestivalPost, festival_post_id)
        if row is None:
            raise RecordNotFoundError("festival_posts", festival_post_id)
        row.status = FestivalPostStatus.PUBLISHED.value
        row.post_id = instagram_post_id or row.post_id
        row.published_at = published_at or utcnow()
        flush_or_raise(self.session)
        return row


class AutomationSettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: str) -> AutomationSettings | None:
        return self.session.scalar(select(AutomationSettings).where(AutomationSettings.user_id == user_id))

    def upsert(self, user_id: str, data: AutomationSettingsWrite | None = None, **kwargs: Any) -> AutomationSettings:
        payload = data.model_dump() if data is not None else (kwargs or AutomationSettingsWrite().model_dump())
        if "timezone" in payload and not payload["timezone"]:
            payload["timezone"] = DEFAULT_TIMEZONE
        raw_time = payload.get("daily_post_time")
        if hasattr(raw_time, "strftime"):
            payload["daily_post_time"] = raw_time.strftime("%H:%M")
        settings = self.get_for_user(user_id)
        if settings is None:
            settings = AutomationSettings(user_id=user_id, **payload)
            if not settings.timezone:
                settings.timezone = DEFAULT_TIMEZONE
            self.session.add(settings)
        else:
            for key, value in payload.items():
                setattr(settings, key, value)
            settings.updated_at = utcnow()
        flush_or_raise(self.session)
        return settings

    def get_or_create(self, user_id: str, timezone: str | None = None) -> AutomationSettings:
        row = self.get_for_user(user_id)
        if row is not None:
            if timezone and not row.timezone:
                row.timezone = timezone
            return row
        return self.upsert(user_id, AutomationSettingsWrite(timezone=timezone or DEFAULT_TIMEZONE))

    def list_daily_enabled(self) -> list[AutomationSettings]:
        return list(
            self.session.scalars(select(AutomationSettings).where(AutomationSettings.daily_enabled.is_(True)))
        )

    def list_festival_enabled(self) -> list[AutomationSettings]:
        return list(
            self.session.scalars(select(AutomationSettings).where(AutomationSettings.festival_enabled.is_(True)))
        )


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, user_id: str, token_hash: str, expires_at: datetime) -> AuthSession:
        row = AuthSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def get_valid(self, session_id: str) -> AuthSession | None:
        row = self.session.get(AuthSession, session_id)
        if row is None or row.revoked_at is not None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            from datetime import timezone as tz

            expires = expires.replace(tzinfo=tz.utc)
        if expires < utcnow():
            return None
        return row

    def count_for_user(self, user_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(AuthSession).where(
                    AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
                )
            )
            or 0
        )

    def revoke(self, session_id: str) -> None:
        row = self.session.get(AuthSession, session_id)
        if row is not None and row.revoked_at is None:
            row.revoked_at = utcnow()
            flush_or_raise(self.session)

    def revoke_all_for_user(self, user_id: str, *, except_id: str | None = None) -> None:
        rows = list(self.session.scalars(select(AuthSession).where(AuthSession.user_id == user_id)))
        now = utcnow()
        for row in rows:
            if except_id and row.id == except_id:
                continue
            if row.revoked_at is None:
                row.revoked_at = now
        flush_or_raise(self.session)


class DailySlotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str, instagram_account_id: str | None, local_date: date) -> DailyPostSlot | None:
        account_key = instagram_account_id or ""
        return self.session.scalar(
            select(DailyPostSlot).where(
                DailyPostSlot.user_id == user_id,
                DailyPostSlot.instagram_account_id == account_key,
                DailyPostSlot.local_date == local_date,
            )
        )

    def claim(self, user_id: str, instagram_account_id: str | None, local_date: date) -> DailyPostSlot | None:
        existing = self.get(user_id, instagram_account_id, local_date)
        if existing is not None:
            if existing.status in {"PUBLISHED", "AMBIGUOUS"}:
                return None
            if existing.status in {"FAILED", "CLAIMED"}:
                existing.status = "CLAIMED"
                flush_or_raise(self.session)
                return existing
            return None
        slot = DailyPostSlot(
            user_id=user_id,
            instagram_account_id=instagram_account_id or "",
            local_date=local_date,
            status="CLAIMED",
        )
        try:
            with self.session.begin_nested():
                self.session.add(slot)
                self.session.flush()
        except IntegrityError:
            return self.get(user_id, instagram_account_id, local_date)
        return slot


BusinessRepository = BusinessProfileRepository
AutomationRepository = AutomationSettingsRepository
PostRepository = InstagramPostRepository
TaskRepository = AgentTaskRepository
FestivalRepository = FestivalCampaignRepository
JobRepository = ScheduledJobRepository
