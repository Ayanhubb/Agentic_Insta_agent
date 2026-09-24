"""Scheduler package.

Submodules are imported lazily so approval and QA helpers can load without
pulling the publication stack (and its app import cycle) into every test.
"""

from __future__ import annotations

from typing import Any

__all__ = ["AutomationRunner", "DailyScheduler", "FestivalScheduler", "TrendIntelligenceScheduler"]


def __getattr__(name: str) -> Any:
    if name == "DailyScheduler":
        from scheduler.daily_scheduler import DailyScheduler

        return DailyScheduler
    if name == "FestivalScheduler":
        from scheduler.festival_scheduler import FestivalScheduler

        return FestivalScheduler
    if name == "AutomationRunner":
        from scheduler.scheduler import AutomationRunner

        return AutomationRunner
    if name == "TrendIntelligenceScheduler":
        from scheduler.trend_scheduler import TrendIntelligenceScheduler

        return TrendIntelligenceScheduler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
