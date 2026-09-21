"""Campaign helpers for Indian festivals."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from festivals.india_festivals import festivals_for_year
from db.models import FestivalCampaign
from db.repositories import FestivalRepository


class FestivalService:
    def __init__(self, session: Session) -> None:
        self._repo = FestivalRepository(session)

    def catalog(self, year: int) -> list[dict[str, object]]:
        return festivals_for_year(year)

    def ensure_campaigns(
        self, user_id: str, year: int, *, required_posts: int = 2, enabled: bool = True
    ) -> list[FestivalCampaign]:
        campaigns: list[FestivalCampaign] = []
        for item in festivals_for_year(year):
            occurs = date.fromisoformat(str(item["date"]))
            campaigns.append(
                self._repo.get_or_create_campaign(
                    user_id,
                    festival_name=str(item["festival_name"]),
                    festival_date=occurs,
                    year=year,
                    required_posts=required_posts,
                    enabled=enabled,
                )
            )
        return campaigns

    def due_campaigns(
        self,
        user_id: str,
        today: date,
        *,
        pre_festival_days: int = 1,
        allow_same_day: bool = False,
    ) -> list[tuple[FestivalCampaign, str]]:
        """Return campaigns that should produce a post today.

        Default: one pre-festival post and one festival-day post.
        Both never run on the same day unless allow_same_day is configured.
        """
        due: list[tuple[FestivalCampaign, str]] = []
        offset = max(0, int(pre_festival_days))
        for campaign in self._repo.list_campaigns(user_id):
            if not campaign.enabled:
                continue
            pre_date = campaign.festival_date - timedelta(days=offset)
            kinds: list[str] = []
            if today == pre_date:
                kinds.append("pre-festival")
            if today == campaign.festival_date:
                kinds.append("festival-day")
            started = int(campaign.generated_posts or 0) > 0 or int(campaign.published_posts or 0) > 0
            if (
                started
                and campaign.remaining_posts > 0
                and today > campaign.festival_date
                and today <= campaign.festival_date + timedelta(days=7)
            ):
                kinds.append("catch-up")
            if not kinds:
                continue
            if len(kinds) > 1 and not allow_same_day:
                kinds = ["festival-day"] if "festival-day" in kinds else kinds[:1]
            due.append((campaign, kinds[0]))
        return due

    def record_publication(self, campaign: FestivalCampaign, festival_post, instagram_post) -> FestivalCampaign:
        """Count only verified Instagram publications. Failures do not reset the campaign."""
        from db.models import InstagramPost

        post_id = getattr(instagram_post, "id", None)
        festival_post.post_id = post_id if post_id and self._repo.session.get(InstagramPost, post_id) else None
        status = getattr(instagram_post, "status", None)
        if status == "PUBLISHED":
            festival_post.status = "PUBLISHED"
            festival_post.published_at = getattr(instagram_post, "published_at", None) or datetime.now(timezone.utc)
        elif status == "AMBIGUOUS_PUBLICATION":
            festival_post.status = "AMBIGUOUS_PUBLICATION"
        else:
            festival_post.status = "FAILED"
        return self._repo.refresh_published_count(campaign.id)
