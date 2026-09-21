"""Injectable clock used by the scheduler so tests can freeze time."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class Clock:
    def now(self, tz_name: str | None = None) -> datetime:
        if tz_name:
            return datetime.now(ZoneInfo(tz_name))
        return datetime.now(timezone.utc)


class FrozenClock(Clock):
    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        self._moment = moment

    def now(self, tz_name: str | None = None) -> datetime:
        if tz_name:
            return self._moment.astimezone(ZoneInfo(tz_name))
        return self._moment

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        self._moment = moment
