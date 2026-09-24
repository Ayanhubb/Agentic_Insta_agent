"""Festival MCP tools. Every tool returns a JSON object and never invents a date."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from festivals.campaign_rules import (
    DEFAULT_PRE_FESTIVAL_DAYS,
    DEFAULT_REQUIRED_POSTS,
    CampaignRegistry,
    active_phase,
    campaign_window,
    content_brief,
    required_slots,
    verified_published_count,
)
from festivals.intelligence import (
    DATE_POLICY,
    TIMEZONE,
    festivals_in_year,
    known_festival,
    matches_place,
    regional,
    resolve_place,
    upcoming,
)

TOOL_NAMES = (
    "get_upcoming_festivals",
    "get_festival_details",
    "get_regional_festivals",
    "get_festival_campaign",
    "get_business_relevance",
)

_DATE_OVERRIDE_KEYS = ("festival_date", "date", "occurs_on", "lunar_date")


class FestivalToolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_RETAIL_HINTS = (
    "retail",
    "jewel",
    "boutique",
    "apparel",
    "fashion",
    "clothing",
    "saree",
    "silk",
    "store",
    "shop",
    "gift",
)
_FOOD_HINTS = (
    "restaurant",
    "cafe",
    "café",
    "food",
    "bakery",
    "sweet",
    "mithai",
    "catering",
    "beverage",
    "dining",
    "kitchen",
    "bar",
)


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_upcoming_festivals",
            "description": "List stored festivals on or after a local date for a city, state, or pan-India audience. Dates are read from the festival database.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "on_date": {"type": "string", "description": "Local civil date the business is asking from, YYYY-MM-DD."},
                    "location": {"type": "string", "description": "City, state, or pan-India label such as Kolkata, West Bengal, or India."},
                    "within_days": {"type": "integer", "default": 45},
                    "include_national": {"type": "boolean", "default": True},
                    "category": {"type": "string", "description": "Optional category: national, regional, retail, food_and_beverage, religious, cultural."},
                },
                "required": ["on_date"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_festival_details",
            "description": "Return one festival from the database. If that year has no stored civil date, date is null.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "festival_name": {"type": "string"},
                    "year": {"type": "integer"},
                },
                "required": ["festival_name", "year"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_regional_festivals",
            "description": "Filter stored festivals by city, state, or macro-region. Pan-India returns national festivals only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "region": {"type": "string"},
                    "year": {"type": "integer"},
                    "include_national": {"type": "boolean", "default": False},
                    "category": {"type": "string"},
                },
                "required": ["region", "year"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_festival_campaign",
            "description": "Build the pre-festival, festival-day, and post-festival window for one stored festival. Reuses the campaign for the same user, name, and year.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "festival_name": {"type": "string"},
                    "year": {"type": "integer"},
                    "user_id": {"type": "string"},
                    "on_date": {"type": "string"},
                    "required_posts": {"type": "integer", "default": 2},
                    "pre_festival_days": {"type": "integer", "default": 1},
                    "allow_same_day": {"type": "boolean", "default": False},
                    "published_posts": {"type": "integer"},
                    "generated_posts": {"type": "integer"},
                    "publication_statuses": {"type": "array", "items": {"type": "string"}},
                    "business_name": {"type": "string"},
                },
                "required": ["festival_name", "year", "user_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_business_relevance",
            "description": "Score a stored festival for a business location and retail or food-and-beverage fit. Does not invent a date.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "festival_name": {"type": "string"},
                    "year": {"type": "integer"},
                    "business": {"type": "object"},
                },
                "required": ["festival_name", "year", "business"],
                "additionalProperties": False,
            },
        },
    ]


class FestivalToolServer:
    def __init__(self, campaigns: CampaignRegistry | None = None) -> None:
        self.campaigns = campaigns or CampaignRegistry()

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(arguments or {})
        rejected = _date_overrides(payload)
        if rejected:
            return _error(
                name,
                "date_not_allowed",
                "Festival dates cannot be supplied. They are read from the festival database.",
                rejected=rejected,
            )
        if name not in TOOL_NAMES:
            return _error(name, "unknown_tool", f"Unknown festival tool '{name}'.")
        try:
            if name == "get_upcoming_festivals":
                body = _upcoming(payload)
            elif name == "get_festival_details":
                body = _details(payload)
            elif name == "get_regional_festivals":
                body = _regional(payload)
            elif name == "get_festival_campaign":
                body = _campaign(self.campaigns, payload)
            else:
                body = _relevance(payload)
        except FestivalToolError as exc:
            return _error(name, exc.code, str(exc))
        except ValueError as exc:
            return _error(name, "invalid_argument", str(exc))
        return {"ok": True, "tool": name, "timezone": TIMEZONE, "date_policy": DATE_POLICY, **body}

    def call_json(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        return json.dumps(self.call(name, arguments), ensure_ascii=False, sort_keys=True)


def _upcoming(arguments: dict[str, Any]) -> dict[str, Any]:
    on_date = _require_date(arguments, "on_date")
    location = arguments.get("location")
    place = resolve_place(str(location)) if location else None
    if location and place is None:
        raise ValueError(f"Unknown place '{location}'.")
    within_days = int(arguments.get("within_days") or 45)
    include_national = bool(arguments.get("include_national", True))
    category = arguments.get("category")
    items = upcoming(
        on_date,
        location=str(location) if location else None,
        within_days=within_days,
        include_national=include_national,
        category=str(category) if category else None,
    )
    return {
        "on_date": on_date.isoformat(),
        "location": place,
        "within_days": within_days,
        "include_national": include_national,
        "count": len(items),
        "festivals": items,
    }


def _details(arguments: dict[str, Any]) -> dict[str, Any]:
    year = _require_year(arguments)
    festival = known_festival(str(arguments.get("festival_name") or ""), year=year)
    if festival is None:
        raise FestivalToolError("unknown_festival", f"Unknown festival '{arguments.get('festival_name')}'.")
    return {"festival": festival}


def _regional(arguments: dict[str, Any]) -> dict[str, Any]:
    year = _require_year(arguments)
    region = str(arguments.get("region") or "").strip()
    place = resolve_place(region)
    if place is None:
        raise ValueError(f"Unknown place '{region}'.")
    include_national = bool(arguments.get("include_national", False))
    category = arguments.get("category")
    items = regional(
        region,
        year,
        include_national=include_national,
        category=str(category) if category else None,
    )
    return {
        "region": place,
        "year": year,
        "include_national": include_national,
        "count": len(items),
        "festivals": items,
    }


def _campaign(registry: CampaignRegistry, arguments: dict[str, Any]) -> dict[str, Any]:
    year = _require_year(arguments)
    user_id = str(arguments.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("user_id is required.")
    festival = known_festival(str(arguments.get("festival_name") or ""), year=year)
    if festival is None:
        raise FestivalToolError("unknown_festival", f"Unknown festival '{arguments.get('festival_name')}'.")
    if not festival.get("date"):
        raise FestivalToolError(
            "date_unavailable",
            f"No stored civil date for {festival['festival_name']} in {year}. Dates are not calculated.",
        )
    festival_date = date.fromisoformat(str(festival["date"]))
    required = int(arguments.get("required_posts") or DEFAULT_REQUIRED_POSTS)
    pre_days = int(arguments.get("pre_festival_days") if arguments.get("pre_festival_days") is not None else DEFAULT_PRE_FESTIVAL_DAYS)
    allow_same_day = bool(arguments.get("allow_same_day", False))
    statuses = arguments.get("publication_statuses")
    published = verified_published_count(
        list(statuses) if isinstance(statuses, list) else None,
        arguments.get("published_posts"),
    )
    generated = int(arguments.get("generated_posts") or 0)
    opened = registry.open(
        user_id=user_id,
        festival_name=str(festival["festival_name"]),
        year=year,
        festival_date=festival_date,
        required_posts=required,
        published_posts=published,
        generated_posts=generated,
    )
    window = campaign_window(
        festival_date,
        pre_festival_days=pre_days,
        required_posts=int(opened["required_posts"]),
        allow_same_day=allow_same_day,
    )
    slots = required_slots(
        festival_date,
        required_posts=int(opened["required_posts"]),
        pre_festival_days=pre_days,
        allow_same_day=allow_same_day,
    )
    local_dates = [slot["local_date"] for slot in slots]
    if len(local_dates) != len(set(local_dates)):
        raise ValueError("Campaign slots must be unique per local date.")
    started = published > 0 or generated > 0
    phase = None
    briefs = []
    on_raw = arguments.get("on_date")
    if on_raw:
        today = _require_date(arguments, "on_date")
        phase = active_phase(
            festival_date,
            today,
            pre_festival_days=pre_days,
            started=started,
            remaining=int(opened["remaining_posts"]),
            allow_same_day=allow_same_day,
        )
        if phase is not None:
            phase["content"] = content_brief(
                phase["phase"],
                str(festival["festival_name"]),
                festival_date.isoformat(),
                arguments.get("business_name"),
            )
    for slot in slots:
        slot["content"] = content_brief(
            slot["phase"],
            str(festival["festival_name"]),
            festival_date.isoformat(),
            arguments.get("business_name"),
        )
        briefs.append(slot["content"])
    return {
        "campaign": opened,
        "festival": festival,
        "window": window,
        "slots": slots,
        "active_phase": phase,
        "content": {
            "pre-festival": content_brief("pre-festival", str(festival["festival_name"]), festival_date.isoformat(), arguments.get("business_name")),
            "festival-day": content_brief("festival-day", str(festival["festival_name"]), festival_date.isoformat(), arguments.get("business_name")),
            "post-festival": content_brief("post-festival", str(festival["festival_name"]), festival_date.isoformat(), arguments.get("business_name")),
        },
        "daily_slot_uniqueness": len(local_dates) == len(set(local_dates)),
        "started": started,
    }


def _relevance(arguments: dict[str, Any]) -> dict[str, Any]:
    year = _require_year(arguments)
    festival = known_festival(str(arguments.get("festival_name") or ""), year=year)
    if festival is None:
        raise FestivalToolError("unknown_festival", f"Unknown festival '{arguments.get('festival_name')}'.")
    business = arguments.get("business") or {}
    if not isinstance(business, dict):
        raise FestivalToolError("invalid_argument", "business must be an object.")
    location = str(business.get("location") or "")
    place = resolve_place(location) if location else None
    if location and place is None:
        raise ValueError(f"Unknown place '{location}'.")
    blob = " ".join(
        [
            str(business.get("business_type") or ""),
            str(business.get("business_category") or ""),
            str(business.get("description") or ""),
            " ".join(business.get("products") or []) if isinstance(business.get("products"), list) else str(business.get("products") or ""),
            " ".join(business.get("services") or []) if isinstance(business.get("services"), list) else str(business.get("services") or ""),
        ]
    ).casefold()
    wants_retail = any(hint in blob for hint in _RETAIL_HINTS)
    wants_food = any(hint in blob for hint in _FOOD_HINTS)
    location_match = bool(place and matches_place(festival, place, include_national=True))
    retail_fit = bool(wants_retail and festival.get("retail"))
    food_fit = bool(wants_food and festival.get("food_and_beverage"))
    reasons: list[dict[str, str]] = []
    score = 0
    if location_match:
        score += 3
        reasons.append({"code": "location_match", "detail": location})
    elif place is not None:
        reasons.append({"code": "location_mismatch", "detail": location})
    if retail_fit:
        score += 3
        reasons.append({"code": "retail_fit"})
    if food_fit:
        score += 3
        reasons.append({"code": "food_and_beverage_fit"})
    if festival.get("pan_india") and location_match:
        reasons.append({"code": "pan_india"})
    category_fit = retail_fit or food_fit
    if not wants_retail and not wants_food and location_match and int(festival.get("priority") or 3) <= 2:
        category_fit = True
        score += 2
        reasons.append({"code": "major_local_festival"})
    relevant = bool(location_match and category_fit and festival.get("date"))
    return {
        "festival": festival,
        "business": {
            "business_name": business.get("business_name"),
            "business_type": business.get("business_type"),
            "business_category": business.get("business_category"),
            "location": location or None,
            "place": place,
        },
        "relevance": {
            "relevant": relevant,
            "score": score,
            "location_match": location_match,
            "retail_fit": retail_fit,
            "food_and_beverage_fit": food_fit,
            "pan_india": bool(festival.get("pan_india")),
            "reasons": reasons,
        },
        "campaign_recommended": relevant,
    }


def _date_overrides(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: arguments[key] for key in _DATE_OVERRIDE_KEYS if key in arguments}


def _require_year(arguments: dict[str, Any]) -> int:
    if "year" not in arguments or arguments["year"] in (None, ""):
        raise ValueError("year is required.")
    year = int(arguments["year"])
    if year < 1900 or year > 2200:
        raise ValueError("year is out of range.")
    return year


def _require_date(arguments: dict[str, Any], key: str) -> date:
    raw = arguments.get(key)
    if raw is None or str(raw).strip() == "":
        raise ValueError(f"{key} is required.")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError as exc:
        raise ValueError(f"{key} must be YYYY-MM-DD.") from exc


def _error(tool: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    body = {
        "ok": False,
        "tool": tool,
        "error": code,
        "message": message,
        "timezone": TIMEZONE,
        "date_policy": DATE_POLICY,
        "date": None,
    }
    body.update(extra)
    return body


def list_stored_year(year: int) -> list[dict[str, Any]]:
    """Helper for tests and callers that want the enriched year list as JSON rows."""
    return festivals_in_year(year)
