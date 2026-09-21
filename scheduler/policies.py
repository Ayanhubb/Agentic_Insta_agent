"""Scheduler policies: publication counting, timing, and retry rules."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Kolkata"

COUNTED_STATUSES = frozenset({"PUBLISHED"})
BLOCKING_DAILY_STATUSES = frozenset({"PUBLISHED", "AMBIGUOUS_PUBLICATION", "PUBLISHING"})
RETRYABLE_STATUSES = frozenset({"FAILED", "GENERATION_FAILED"})


def counts_as_published(status: str) -> bool:
    return status == "PUBLISHED"


def remaining_festival_posts(required: int, published: int) -> int:
    return max(0, int(required) - int(published))


class FestivalDiversityPolicy:
    """Festival campaigns need multiple posts about the same occasion.

    Daily uniqueness still applies through DailyScheduler's ContentAgent.
    """

    def evaluate(self, plan, history):
        from models.content import DiversityVerdict

        return DiversityVerdict(
            accepted=True,
            reason="festival_campaign_slot",
            score=0.0,
            backend="festival_scheduler",
            blocking=False,
        )


def resolve_timezone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((name or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def parse_hhmm(value: str | time | None) -> time:
    if isinstance(value, time):
        return value
    raw = (value or "10:00").strip() or "10:00"
    parts = raw.split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def is_at_or_after_local_time(now: datetime, post_time: str | time | None) -> bool:
    current = now.timetz().replace(tzinfo=None) if now.tzinfo else now.time()
    return current >= parse_hhmm(post_time)


def attach_publication(session, post, *, account=None, scheduled_date=None, post_type: str | None = None):
    """Copy a verified publication into the scheduler session.

    Instagram Agent persistence uses a separate connection. SQLite will not
    see that row inside this open transaction, so campaign FKs and dashboard
    counts must use a row on the scheduler session.
    """
    from sqlalchemy import select

    from db.models import GeneratedImage, InstagramAccount, InstagramPost

    if isinstance(post, InstagramPost):
        return post

    post_id = getattr(post, "id", None)
    row = session.get(InstagramPost, post_id) if post_id else None
    account_pk = getattr(account, "id", None) if account is not None else None
    raw_account = getattr(post, "instagram_account_id", None)
    if account_pk is None and raw_account:
        if session.get(InstagramAccount, raw_account) is not None:
            account_pk = raw_account
        else:
            matched = session.scalar(
                select(InstagramAccount).where(InstagramAccount.instagram_account_id == raw_account)
            )
            if matched is not None:
                account_pk = matched.id

    status = getattr(post, "status", None) or "FAILED"
    media_id = getattr(post, "instagram_media_id", None)
    published_at = getattr(post, "published_at", None)
    generated_id = getattr(post, "generated_image_id", None)
    if generated_id and session.get(GeneratedImage, generated_id) is None:
        generated_id = None
    if status == "PUBLISHED" and not media_id:
        status = "FAILED"
    if status == "PUBLISHED" and published_at is None:
        published_at = datetime.now(timezone.utc)
    if media_id:
        taken = session.scalar(
            select(InstagramPost.id).where(
                InstagramPost.instagram_media_id == media_id,
                InstagramPost.id != (post_id or ""),
            )
        )
        if taken:
            media_id = f"{media_id}-{str(post_id or '')[:8]}"

    values = {
        "user_id": getattr(post, "user_id", None),
        "instagram_account_id": account_pk,
        "generated_image_id": generated_id,
        "instagram_media_id": media_id,
        "permalink": getattr(post, "permalink", None),
        "status": status,
        "post_type": post_type or getattr(post, "post_type", None) or "USER_PROMPT",
        "published_at": published_at,
        "error": None if status == "PUBLISHED" else getattr(post, "error", None),
        "scheduled_date": scheduled_date if scheduled_date is not None else getattr(post, "scheduled_date", None),
        "agent_task_id": getattr(post, "agent_task_id", None),
    }
    if row is None:
        row = InstagramPost(id=post_id, **values)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    session.flush()
    return row


def automation_context(automation: object) -> dict[str, object]:
    post_time = getattr(automation, "daily_post_time", None)
    if isinstance(post_time, time):
        post_time = post_time.strftime("%H:%M")
    return {
        "daily_enabled": bool(getattr(automation, "daily_enabled", False)),
        "daily_posts_per_day": int(getattr(automation, "daily_posts_per_day", 1) or 1),
        "daily_post_time": post_time or "10:00",
        "festival_enabled": bool(getattr(automation, "festival_enabled", False)),
        "festival_posts_per_festival": int(getattr(automation, "festival_posts_per_festival", 2) or 2),
        "auto_daily_publish": bool(getattr(automation, "auto_daily_publish", False)),
        "auto_festival_publish": bool(getattr(automation, "auto_festival_publish", False)),
        "timezone": getattr(automation, "timezone", None) or DEFAULT_TIMEZONE,
    }
