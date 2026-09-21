"""Daily automation: one verified post per enabled Instagram account per local date."""

from __future__ import annotations

from sqlalchemy.orm import Session

from agent.content_agent import ContentAgent
from config import Settings
from db.models import AutomationSettings, User
from db.repositories import (
    AutomationRepository,
    BusinessRepository,
    DailySlotRepository,
    InstagramAccountRepository,
    PostRepository,
    UserRepository,
)
from models.content import ContentMode, ContentStrategyRequest
from models.errors import AppError
from services.clock import Clock
from services.publication import PublicationService
from scheduler.job_manager import JobManager
from scheduler.policies import attach_publication, automation_context, is_at_or_after_local_time


class DailyScheduler:
    def __init__(
        self,
        settings: Settings,
        session: Session,
        content_agent: ContentAgent,
        publisher: PublicationService,
        clock: Clock,
    ) -> None:
        self._settings = settings
        self._session = session
        self._content = content_agent
        self._publisher = publisher
        self._clock = clock
        self._users = UserRepository(session)
        self._business = BusinessRepository(session)
        self._accounts = InstagramAccountRepository(session)
        self._automation = AutomationRepository(session)
        self._slots = DailySlotRepository(session)
        self._posts = PostRepository(session)
        self._jobs = JobManager(session)

    async def run_user(
        self,
        user: User,
        automation: AutomationSettings,
        *,
        force: bool = False,
    ) -> dict[str, str]:
        if not automation.daily_enabled:
            return {"status": "skipped", "reason": "automation_disabled"}
        tz = automation.timezone or self._settings.default_timezone
        now = self._clock.now(tz)
        today = now.date()
        if not force and not is_at_or_after_local_time(now, automation.daily_post_time):
            return {"status": "skipped", "reason": "before_post_time", "scheduled_date": today.isoformat()}

        account = self._accounts.get_primary(user.id)
        account_pk = account.id if account else ""
        if self._posts.has_published_daily(user.id, account_pk or None, today):
            return {"status": "skipped", "reason": "already_published", "scheduled_date": today.isoformat()}

        existing = self._slots.get(user.id, account_pk, today)
        if existing and existing.status in {"PUBLISHED", "AMBIGUOUS"}:
            return {"status": "skipped", "reason": existing.status.lower(), "scheduled_date": today.isoformat()}

        slot = self._slots.claim(user.id, account_pk, today)
        if slot is None:
            return {"status": "skipped", "reason": "already_claimed", "scheduled_date": today.isoformat()}

        profile = self._business.get_for_user(user.id)
        if profile is None:
            slot.status = "FAILED"
            return {"status": "failed", "reason": "missing_business_profile"}
        try:
            result = await self._content.run(
                ContentStrategyRequest(
                    user_id=user.id,
                    mode=ContentMode.DAILY,
                    business_profile=profile,
                    automation=automation_context(automation),
                    instagram_account_id=account.id if account else None,
                    now=now,
                )
            )
        except AppError as exc:
            slot.status = "FAILED"
            return {"status": "failed", "reason": exc.code.value}

        if not result.handoff.auto_approved or not result.generated_image or not result.generated_image.id:
            slot.status = "FAILED" if not result.generated_image else "CLAIMED"
            return {"status": "generated_pending_approval"}

        from db.models import GeneratedImage

        image = self._session.get(GeneratedImage, result.generated_image.id)
        if image is None:
            slot.status = "FAILED"
            return {"status": "failed", "reason": "image_missing"}
        self._session.commit()
        try:
            post = await self._publisher.publish_generated_image(
                user_id=user.id,
                image=image,
                account=account,
                post_type="DAILY_RETAIL_POST",
                trigger="DAILY_AUTOMATION",
                scheduled_date=today,
            )
        except AppError as exc:
            slot.status = "FAILED"
            return {"status": "failed", "reason": exc.code.value}

        post = attach_publication(
            self._session,
            post,
            account=account,
            scheduled_date=today,
            post_type="DAILY_RETAIL_POST",
        )
        if post.status == "PUBLISHED":
            slot.status = "PUBLISHED"
            slot.post_id = post.id
        elif post.status == "AMBIGUOUS_PUBLICATION":
            slot.status = "AMBIGUOUS"
            slot.post_id = post.id
        else:
            slot.status = "FAILED"
            slot.post_id = post.id
        self._jobs.mark_run(user.id, "DAILY")
        return {
            "status": post.status,
            "post_id": post.id,
            "scheduled_date": today.isoformat(),
            "verified": str(post.status == "PUBLISHED"),
        }

    async def run_all(self, *, force: bool = False) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for automation in self._automation.list_daily_enabled():
            user = self._users.get_by_id(automation.user_id)
            if user is None or not user.is_active:
                continue
            results.append(await self.run_user(user, automation, force=force))
        return results
