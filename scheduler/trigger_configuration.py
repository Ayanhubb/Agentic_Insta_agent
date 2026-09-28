"""Instagram daily and festival runs as trigger configuration.

DailyScheduler and FestivalScheduler still execute those jobs. This module
only describes them for the generic scheduler. Festival dates are copied from
festivals_for_year, which reads the catalog and lunar_dates.json.
"""

from __future__ import annotations

import re
from datetime import date

from agent_platform.triggers.types import (
    CalendarDate,
    ScheduleKind,
    TriggerDefinition,
    TriggerKind,
)
from festivals.india_festivals import festivals_for_year

INSTAGRAM_AGENT_KEY = "instagram_marketing"
DAILY_TRIGGER_ID = "instagram_daily"
FESTIVAL_TRIGGER_ID = "instagram_festival"
DAILY_TRIGGER_KEY = "scheduler.daily"
FESTIVAL_TRIGGER_KEY = "scheduler.festival"
PHASE_TO_CAMPAIGN_KIND = {"lead": "pre-festival", "on": "festival-day"}


def instagram_trigger_definitions(
    *,
    tenant_id: str,
    year: int,
    timezone_name: str = "Asia/Kolkata",
    daily_enabled: bool = False,
    daily_post_time: str | None = None,
    festival_enabled: bool = False,
    pre_festival_days: int = 1,
    allow_same_day_festival_posts: bool = False,
    festival_local_time: str = "10:00",
) -> tuple[TriggerDefinition, TriggerDefinition]:
    daily = TriggerDefinition(
        trigger_id=DAILY_TRIGGER_ID,
        tenant_id=tenant_id,
        agent_key=INSTAGRAM_AGENT_KEY,
        trigger_key=DAILY_TRIGGER_KEY,
        kind=TriggerKind.SCHEDULE,
        enabled=daily_enabled,
        timezone=timezone_name,
        schedule_kind=ScheduleKind.DAILY,
        local_time=_hhmm(daily_post_time),
        static_payload={"mode": "daily", "post_type": "DAILY_RETAIL_POST"},
    )
    festival = TriggerDefinition(
        trigger_id=FESTIVAL_TRIGGER_ID,
        tenant_id=tenant_id,
        agent_key=INSTAGRAM_AGENT_KEY,
        trigger_key=FESTIVAL_TRIGGER_KEY,
        kind=TriggerKind.SCHEDULE,
        enabled=festival_enabled,
        timezone=timezone_name,
        schedule_kind=ScheduleKind.CALENDAR,
        local_time=_hhmm(festival_local_time),
        lead_days=pre_festival_days,
        allow_same_day=allow_same_day_festival_posts,
        occurrences=_calendar(year),
        lead_phase="pre-festival",
        on_phase="festival-day",
        static_payload={"mode": "festival", "post_type": "FESTIVAL"},
    )
    return daily, festival


def _calendar(year: int) -> tuple[CalendarDate, ...]:
    used: set[str] = set()
    rows: list[CalendarDate] = []
    for item in festivals_for_year(year):
        label = str(item["festival_name"])
        rows.append(
            CalendarDate(
                occurrence_key=_unique_slug(label, used),
                occurs_on=date.fromisoformat(str(item["date"])),
                label=label[:120],
            )
        )
    return tuple(rows)


def _unique_slug(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "date"
    base = base[:32]
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"_{index}"
        keep = 32 - len(suffix)
        candidate = f"{base[:keep]}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def _hhmm(value: str | None) -> str:
    raw = (value or "10:00").strip() or "10:00"
    parts = raw.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return f"{hour:02d}:{minute:02d}"
