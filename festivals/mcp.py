"""Festival MCP adapter used by the scheduler.

Dates come from the stored India catalog. DeepSeek is not asked for dates.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from festivals.mcp_tools import FestivalToolServer


class FestivalMcp:
    def __init__(self) -> None:
        self._server = FestivalToolServer()

    def get_festival_context(
        self,
        *,
        user_id: str | None = None,
        on_date: date | datetime | None = None,
        festival_name: str | None = None,
    ) -> dict[str, Any]:
        del user_id
        when = on_date.date() if isinstance(on_date, datetime) else on_date or date.today()
        if not festival_name:
            return {"valid": True, "source": "catalog", "date": when.isoformat()}
        body = self._server.call(
            "get_festival_details",
            {"festival_name": festival_name, "year": when.year},
        )
        festival = body.get("festival") if isinstance(body, dict) else None
        if not isinstance(festival, dict) or not festival.get("date"):
            return {"valid": False, "source": "catalog", "festival_name": festival_name}
        return {
            "valid": True,
            "source": "catalog",
            "festival_name": festival.get("festival_name") or festival_name,
            "date": festival.get("date"),
            "region": festival.get("region"),
        }


def get_festival_mcp(_settings: Any | None = None) -> FestivalMcp:
    return FestivalMcp()
