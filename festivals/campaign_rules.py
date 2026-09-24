"""Festival campaign windows, content phases, and uniqueness.

These rules describe the same scheduler contract the festival scheduler already
uses: two required posts, Asia/Kolkata civil dates, one slot per local day,
one campaign per user/festival/year, and verified PUBLISHED counts only.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

TIMEZONE = "Asia/Kolkata"
DEFAULT_REQUIRED_POSTS = 2
DEFAULT_PRE_FESTIVAL_DAYS = 1
CATCH_UP_DAYS = 7
COUNTED_STATUSES = frozenset({"PUBLISHED"})
IGNORED_STATUSES = frozenset({"FAILED", "AMBIGUOUS_PUBLICATION", "PUBLISHING", "PENDING", "GENERATED"})

PHASE_CONTENT: dict[str, dict[str, Any]] = {
    "pre-festival": {
        "phase": "pre-festival",
        "scheduler_kind": "pre-festival",
        "intent": "Preview this business before the festival: pre-orders, gifting, reservations, or a first look.",
        "include": ["the business product or service", "the festival name", "the stored festival date"],
        "exclude": ["an invented festival date", "a generic greeting that does not show the business"],
    },
    "festival-day": {
        "phase": "festival-day",
        "scheduler_kind": "festival-day",
        "intent": "Show the business inside the festival day itself.",
        "include": ["the business product or service", "the festival name", "the stored festival date"],
        "exclude": ["an invented festival date", "a generic greeting that does not show the business"],
    },
    "post-festival": {
        "phase": "post-festival",
        "scheduler_kind": "catch-up",
        "intent": "Catch up only after the festival, and only when the campaign has already started and posts remain.",
        "include": ["the business product or service", "the festival name", "the stored festival date"],
        "exclude": ["an invented festival date", "a new campaign after the catch-up window", "a second post on the same local day"],
    },
}


def counts_as_published(status: str | None) -> bool:
    return status == "PUBLISHED"


def remaining_posts(required: int, published: int) -> int:
    return max(0, int(required) - int(published))


def verified_published_count(statuses: list[str] | None, published_posts: int | None = None) -> int:
    if statuses is not None:
        return sum(1 for status in statuses if counts_as_published(status))
    return max(0, int(published_posts or 0))


def campaign_window(
    festival_date: date,
    *,
    pre_festival_days: int = DEFAULT_PRE_FESTIVAL_DAYS,
    catch_up_days: int = CATCH_UP_DAYS,
    required_posts: int = DEFAULT_REQUIRED_POSTS,
    allow_same_day: bool = False,
) -> dict[str, Any]:
    pre_days = max(0, int(pre_festival_days))
    catch_up = max(0, int(catch_up_days))
    required = max(1, int(required_posts))
    pre_date = festival_date - timedelta(days=pre_days)
    post_start = festival_date + timedelta(days=1)
    post_end = festival_date + timedelta(days=catch_up)
    return {
        "timezone": TIMEZONE,
        "festival_date": festival_date.isoformat(),
        "pre_festival_days": pre_days,
        "catch_up_days": catch_up,
        "required_posts": required,
        "allow_same_day": allow_same_day,
        "phases": {
            "pre-festival": _phase_payload("pre-festival", pre_date.isoformat(), pre_date.isoformat()),
            "festival-day": _phase_payload("festival-day", festival_date.isoformat(), festival_date.isoformat()),
            "post-festival": _phase_payload("post-festival", post_start.isoformat(), post_end.isoformat()),
        },
        "rules": campaign_rule_set(required_posts=required, allow_same_day=allow_same_day),
    }


def active_phase(
    festival_date: date,
    today: date,
    *,
    pre_festival_days: int = DEFAULT_PRE_FESTIVAL_DAYS,
    catch_up_days: int = CATCH_UP_DAYS,
    started: bool = False,
    remaining: int = DEFAULT_REQUIRED_POSTS,
    allow_same_day: bool = False,
) -> dict[str, Any] | None:
    """Match FestivalService.due_campaigns phase selection."""
    pre_days = max(0, int(pre_festival_days))
    pre_date = festival_date - timedelta(days=pre_days)
    kinds: list[str] = []
    if today == pre_date:
        kinds.append("pre-festival")
    if today == festival_date:
        kinds.append("festival-day")
    if (
        started
        and remaining > 0
        and today > festival_date
        and today <= festival_date + timedelta(days=max(0, int(catch_up_days)))
    ):
        kinds.append("post-festival")
    if not kinds:
        return None
    if len(kinds) > 1 and not allow_same_day:
        selected = "festival-day" if "festival-day" in kinds else kinds[0]
    else:
        selected = kinds[0]
    content = dict(PHASE_CONTENT[selected])
    return {
        "phase": selected,
        "scheduler_kind": content["scheduler_kind"],
        "candidates": [_scheduler_kind(kind) for kind in kinds],
        "content": content,
    }


def required_slots(
    festival_date: date,
    *,
    required_posts: int = DEFAULT_REQUIRED_POSTS,
    pre_festival_days: int = DEFAULT_PRE_FESTIVAL_DAYS,
    allow_same_day: bool = False,
) -> list[dict[str, Any]]:
    """One local calendar day per slot. Same-day pairs collapse unless allowed."""
    pre_days = max(0, int(pre_festival_days))
    required = max(1, int(required_posts))
    proposed: list[tuple[str, date]] = []
    if required >= 1:
        proposed.append(("pre-festival", festival_date - timedelta(days=pre_days)))
    if required >= 2:
        proposed.append(("festival-day", festival_date))
    extra = max(0, required - 2)
    for offset in range(1, extra + 1):
        if offset > CATCH_UP_DAYS:
            break
        proposed.append(("post-festival", festival_date + timedelta(days=offset)))

    slots: list[dict[str, Any]] = []
    used: set[str] = set()
    sequence = 1
    for kind, day in proposed:
        local = day.isoformat()
        if local in used and not allow_same_day:
            continue
        used.add(local)
        content = dict(PHASE_CONTENT[kind])
        slots.append(
            {
                "sequence": sequence,
                "phase": kind,
                "scheduler_kind": content["scheduler_kind"],
                "local_date": local,
                "timezone": TIMEZONE,
                "content": content,
            }
        )
        sequence += 1
        if len(slots) >= required:
            break
    return slots


def content_brief(phase: str, festival_name: str, festival_date: str, business_name: str | None = None) -> dict[str, Any]:
    content = dict(PHASE_CONTENT[phase])
    subject = (business_name or "the business").strip() or "the business"
    content["festival_name"] = festival_name
    content["festival_date"] = festival_date
    content["business_name"] = subject
    content["brief"] = f"{subject} — {content['intent']} Festival: {festival_name} on {festival_date}."
    return content


def campaign_rule_set(*, required_posts: int = DEFAULT_REQUIRED_POSTS, allow_same_day: bool = False) -> dict[str, Any]:
    return {
        "required_posts": required_posts,
        "local_timezone": TIMEZONE,
        "daily_slot_uniqueness": True,
        "campaign_uniqueness": "one campaign per user, festival name, and year",
        "allow_same_day_festival_posts": allow_same_day,
        "verified_publication_counting": {
            "counts": sorted(COUNTED_STATUSES),
            "does_not_count": sorted(IGNORED_STATUSES),
        },
        "dates_are_not_inferred": True,
    }


def campaign_key(user_id: str, festival_name: str, year: int) -> str:
    return f"{user_id}|{festival_name.casefold()}|{int(year)}"


class CampaignRegistry:
    """In-memory uniqueness with the same key as festival_campaigns (user, name, year)."""

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self._campaigns)

    def open(
        self,
        *,
        user_id: str,
        festival_name: str,
        year: int,
        festival_date: date,
        required_posts: int = DEFAULT_REQUIRED_POSTS,
        published_posts: int = 0,
        generated_posts: int = 0,
    ) -> dict[str, Any]:
        key = campaign_key(user_id, festival_name, year)
        existing = self._campaigns.get(key)
        if existing is not None:
            existing["published_posts"] = int(published_posts)
            existing["generated_posts"] = int(generated_posts)
            existing["remaining_posts"] = remaining_posts(existing["required_posts"], published_posts)
            existing["duplicate"] = True
            existing["created"] = False
            return dict(existing)
        record = {
            "campaign_key": key,
            "campaign_id": str(uuid4()),
            "user_id": user_id,
            "festival_name": festival_name,
            "year": int(year),
            "festival_date": festival_date.isoformat(),
            "timezone": TIMEZONE,
            "required_posts": max(1, int(required_posts)),
            "published_posts": int(published_posts),
            "generated_posts": int(generated_posts),
            "remaining_posts": remaining_posts(required_posts, published_posts),
            "duplicate": False,
            "created": True,
        }
        self._campaigns[key] = record
        return dict(record)


def _phase_payload(phase: str, start: str, end: str) -> dict[str, Any]:
    content = dict(PHASE_CONTENT[phase])
    return {
        "phase": phase,
        "scheduler_kind": content["scheduler_kind"],
        "start": start,
        "end": end,
        "content": content,
    }


def _scheduler_kind(phase: str) -> str:
    return str(PHASE_CONTENT[phase]["scheduler_kind"])
