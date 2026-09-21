"""Festival campaign scheduler. Only verified Instagram publications count."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from agent.content_agent import ContentAgent
from festivals.festival_service import FestivalService
from config import Settings
from db.models import AutomationSettings, FestivalCampaign, FestivalPost, GeneratedImage, User
from db.repositories import (
    AutomationRepository,
    BusinessRepository,
    InstagramAccountRepository,
    UserRepository,
)
from models.content import ContentMode, ContentStrategyRequest
from models.errors import AppError
from services.clock import Clock
from services.publication import PublicationService
from scheduler.job_manager import JobManager
from scheduler.policies import attach_publication, automation_context


class FestivalScheduler:
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
        self._festivals = FestivalService(session)
        self._jobs = JobManager(session)

    async def run_user(
        self,
        user: User,
        automation: AutomationSettings,
        *,
        force: bool = False,
    ) -> list[dict[str, str]]:
        if not automation.festival_enabled:
            return [{"status": "skipped", "reason": "festival_disabled"}]
        tz = automation.timezone or self._settings.default_timezone
        today = self._clock.now(tz).date()
        required = automation.festival_posts_per_festival or 2
        pre_days = getattr(automation, "pre_festival_days", 1) or 1
        allow_same_day = bool(getattr(automation, "allow_same_day_festival_posts", False))
        self._festivals.ensure_campaigns(user.id, today.year, required_posts=required, enabled=True)
        profile = self._business.get_for_user(user.id)
        account = self._accounts.get_primary(user.id)
        results: list[dict[str, str]] = []
        if profile is None:
            return [{"status": "failed", "reason": "missing_business_profile"}]

        for campaign, kind in self._festivals.due_campaigns(
            user.id,
            today,
            pre_festival_days=pre_days,
            allow_same_day=allow_same_day,
        ):
            if campaign.remaining_posts <= 0:
                results.append(
                    {
                        "status": "skipped",
                        "reason": "campaign_complete",
                        "campaign": campaign.id,
                        "required_posts": str(campaign.required_posts),
                        "published_posts": str(campaign.published_posts),
                        "remaining_posts": str(campaign.remaining_posts),
                    }
                )
                continue

            if _has_blocking_festival_post_today(campaign, today) and not allow_same_day:
                results.append(
                    {
                        "status": "skipped",
                        "reason": "same_day_blocked",
                        "campaign": campaign.id,
                        "remaining_posts": str(campaign.remaining_posts),
                    }
                )
                continue

            scheduled_at = datetime.combine(today, time(10, 0), tzinfo=ZoneInfo(tz))
            reused = self._festivals._repo.reusable_festival_post(campaign)
            if reused is not None:
                fest_post = reused
                fest_post.status = "PENDING"
                fest_post.scheduled_for = scheduled_at
                sequence = fest_post.sequence_number
            else:
                sequence = self._festivals._repo.next_sequence(campaign)
                if sequence > campaign.required_posts:
                    continue
                fest_post = self._festivals._repo.add_festival_post(
                    campaign, sequence, status="PENDING", scheduled_for=scheduled_at
                )

            try:
                result = await self._content.run(
                    ContentStrategyRequest(
                        user_id=user.id,
                        mode=ContentMode.FESTIVAL,
                        business_profile=profile,
                        festival={
                            "name": campaign.festival_name,
                            "festival_name": campaign.festival_name,
                            "date": campaign.festival_date,
                            "year": campaign.year,
                            "campaign_id": campaign.id,
                            "required_posts": campaign.required_posts,
                            "published_posts": campaign.published_posts,
                        },
                        automation=automation_context(automation),
                        instagram_account_id=account.id if account else None,
                        now=self._clock.now(tz),
                    )
                )
            except AppError as exc:
                fest_post.status = "FAILED"
                results.append({"status": "failed", "reason": exc.code.value, "campaign": campaign.id})
                continue
            if not result.generated_image or not result.generated_image.id:
                fest_post.status = "FAILED"
                results.append({"status": "failed", "reason": "generation_failed", "campaign": campaign.id})
                continue

            image = self._session.get(GeneratedImage, result.generated_image.id)
            fest_post.generated_image_id = image.id if image else None
            if not result.handoff.auto_approved or image is None:
                results.append(
                    {
                        "status": "generated_pending_approval",
                        "campaign": campaign.id,
                        "remaining_posts": str(campaign.remaining_posts),
                    }
                )
                continue
            self._session.commit()
            try:
                post = await self._publisher.publish_generated_image(
                    user_id=user.id,
                    image=image,
                    account=account,
                    post_type="FESTIVAL",
                    trigger="FESTIVAL_AUTOMATION",
                    scheduled_date=today,
                    festival_campaign_id=campaign.id,
                    festival_sequence=sequence,
                )
            except AppError as exc:
                fest_post.status = "FAILED"
                results.append({"status": "failed", "reason": exc.code.value, "campaign": campaign.id})
                continue
            post = attach_publication(
                self._session,
                post,
                account=account,
                scheduled_date=today,
                post_type="FESTIVAL",
            )
            campaign = self._festivals.record_publication(campaign, fest_post, post)
            results.append(
                {
                    "status": post.status,
                    "campaign": campaign.id,
                    "kind": kind,
                    "required_posts": str(campaign.required_posts),
                    "generated_posts": str(campaign.generated_posts),
                    "published_posts": str(campaign.published_posts),
                    "remaining_posts": str(campaign.remaining_posts),
                }
            )
        self._jobs.mark_run(user.id, "FESTIVAL")
        return results

    async def run_all(self, *, force: bool = False) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for automation in self._automation.list_festival_enabled():
            user = self._users.get_by_id(automation.user_id)
            if user is None or not user.is_active:
                continue
            results.extend(await self.run_user(user, automation, force=force))
        return results


def _has_blocking_festival_post_today(campaign: FestivalCampaign, today: date) -> bool:
    for item in campaign.posts:
        scheduled = item.scheduled_for
        if scheduled is None:
            continue
        day = scheduled.date() if isinstance(scheduled, datetime) else scheduled
        if day == today and item.status in {"PUBLISHED", "AMBIGUOUS_PUBLICATION", "PUBLISHING"}:
            return True
    return False
