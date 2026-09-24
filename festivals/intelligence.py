"""Festival intelligence over stored dates.

Dates come from ``festivals_for_year`` (catalog + lunar table) or from a
lunar key / fixed Gregorian month-day in ``coverage.json``. Nothing in this
module calculates a lunar, lunisolar, or Islamic date.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from festivals.india_festivals import festivals_for_year, lunar_date_table

TIMEZONE = "Asia/Kolkata"
DATE_POLICY = "stored_dates_only"
COVERAGE_PATH = Path(__file__).resolve().parent / "data" / "coverage.json"

_CATEGORY_FLAGS = ("religious", "cultural", "retail", "food_and_beverage")


@lru_cache(maxsize=1)
def load_coverage() -> dict[str, Any]:
    payload = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    return {
        "meta": payload.get("meta") or {},
        "places": {str(key).casefold(): value for key, value in (payload.get("places") or {}).items()},
        "festivals": payload.get("festivals") or {},
        "extensions": list(payload.get("extensions") or []),
    }


def resolve_place(text: str | None) -> dict[str, Any] | None:
    """Map a city, state, macro-region, or pan-India label to a place record."""
    raw = (text or "").strip()
    if not raw:
        return None
    places = load_coverage()["places"]
    found = _place_lookup(raw, places)
    if found is not None:
        return found
    for part in raw.replace("/", ",").split(","):
        found = _place_lookup(part, places)
        if found is not None:
            return found
    folded = raw.casefold()
    for key, record in places.items():
        if key and key in folded:
            return _place_record(key, record)
    return None


def known_festival(name: str | None, *, year: int | None = None) -> dict[str, Any] | None:
    """Return one festival. ``date`` is null when that year is not stored."""
    canonical = canonical_name(name)
    if canonical is None:
        return None
    if year is None:
        year = _any_known_year(canonical)
    item = _record_for_year(canonical, year)
    if item is not None:
        return item
    meta = _meta_for(canonical)
    if meta is None:
        return None
    return _finalize(
        {
            "festival_name": canonical,
            "region": meta.get("region"),
            "description": meta.get("description") or "",
            "priority": meta.get("priority", 3),
            "lunar": bool(meta.get("lunar")),
        },
        meta,
        year=year,
        occurs=None,
        date_source=None,
    )


def festivals_in_year(year: int) -> list[dict[str, Any]]:
    """Catalog festivals plus extensions that have a stored date for ``year``."""
    items = [_enrich_catalog_row(row) for row in festivals_for_year(year)]
    seen = {str(item["festival_name"]) for item in items}
    for extension in load_coverage()["extensions"]:
        name = str(extension.get("festival_name") or "")
        if not name or name in seen:
            continue
        occurs, source = _extension_date(extension, year)
        if occurs is None:
            continue
        items.append(
            _finalize(
                {
                    "festival_name": name,
                    "region": extension.get("region"),
                    "description": extension.get("description") or "",
                    "priority": extension.get("priority", 3),
                    "lunar": bool(extension.get("lunar")),
                },
                extension,
                year=year,
                occurs=occurs,
                date_source=source,
            )
        )
    items.sort(key=lambda item: (item.get("date") or "9999-99-99", str(item["festival_name"])))
    return items


def upcoming(
    on_date: date,
    *,
    location: str | None = None,
    within_days: int = 45,
    include_national: bool = True,
    category: str | None = None,
) -> list[dict[str, Any]]:
    place = resolve_place(location) if location else None
    if location and place is None:
        return []
    horizon = max(0, int(within_days))
    selected: list[dict[str, Any]] = []
    years = {on_date.year, on_date.year + 1} if on_date.month == 12 else {on_date.year}
    for year in sorted(years):
        for item in festivals_in_year(year):
            if item.get("date") is None:
                continue
            occurs = date.fromisoformat(str(item["date"]))
            delta = (occurs - on_date).days
            if delta < 0 or delta > horizon:
                continue
            if place is not None and not matches_place(item, place, include_national=include_national):
                continue
            if category and not _has_category(item, category):
                continue
            selected.append({**item, "days_until": delta})
    selected.sort(key=lambda item: (item["date"], item["festival_name"]))
    return selected


def regional(
    region: str,
    year: int,
    *,
    include_national: bool = False,
    category: str | None = None,
) -> list[dict[str, Any]]:
    place = resolve_place(region)
    if place is None:
        return []
    items = []
    for item in festivals_in_year(year):
        if not matches_place(item, place, include_national=include_national):
            continue
        if category and not _has_category(item, category):
            continue
        items.append(item)
    return items


def matches_place(festival: dict[str, Any], place: dict[str, Any], *, include_national: bool) -> bool:
    pan_india = bool(
        festival.get("pan_india") or festival.get("scope") == "national" or festival.get("region") == "National"
    )
    if place.get("pan_india"):
        return pan_india
    if include_national and pan_india:
        return True
    state = place.get("state")
    city = place.get("city")
    states = festival.get("states") or []
    cities = festival.get("cities") or []
    if state and state in states:
        return True
    if city and city in cities:
        return True
    region = str(festival.get("region") or "")
    if state and region.casefold() == state.casefold():
        return True
    if place.get("kind") != "macro":
        return False
    macro = str(place.get("macro_region") or "")
    return festival.get("macro_region") == macro or region.casefold() == macro.casefold()


def canonical_name(name: str | None) -> str | None:
    raw = (name or "").strip()
    if not raw:
        return None
    folded = raw.casefold()
    for candidate in _all_names():
        if candidate.casefold() == folded:
            return candidate
    for candidate, meta in _all_meta().items():
        for alias in meta.get("aliases") or []:
            if str(alias).casefold() == folded:
                return candidate
    return None


def _enrich_catalog_row(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row["festival_name"])
    meta = _meta_for(name) or {}
    occurs = date.fromisoformat(str(row["date"])) if row.get("date") else None
    source = "catalog" if occurs else None
    return _finalize(row, meta, year=int(row["year"]), occurs=occurs, date_source=source)


def _record_for_year(name: str, year: int) -> dict[str, Any] | None:
    for item in festivals_in_year(year):
        if item["festival_name"] == name:
            return item
    meta = _meta_for(name)
    if meta is None:
        return None
    extension = _extension_by_name(name)
    return _finalize(
        {
            "festival_name": name,
            "region": (extension or meta).get("region") or meta.get("region"),
            "description": (extension or meta).get("description") or "",
            "priority": (extension or meta).get("priority", 3),
            "lunar": bool((extension or meta).get("lunar")),
        },
        extension or meta,
        year=year,
        occurs=None,
        date_source=None,
    )


def _finalize(
    row: dict[str, Any],
    meta: dict[str, Any],
    *,
    year: int,
    occurs: date | None,
    date_source: str | None,
) -> dict[str, Any]:
    scope = str(meta.get("scope") or _infer_scope(row))
    pan_india = bool(meta.get("pan_india") or scope == "national" or row.get("region") == "National")
    states = [str(item) for item in (meta.get("states") or _infer_states(row))]
    cities = [str(item) for item in (meta.get("cities") or [])]
    flags = {flag: bool(meta.get(flag)) for flag in _CATEGORY_FLAGS}
    categories = _categories(scope, pan_india, flags)
    return {
        "festival_name": row["festival_name"],
        "aliases": [str(item) for item in (meta.get("aliases") or [])],
        "date": occurs.isoformat() if occurs else None,
        "year": year,
        "region": row.get("region"),
        "scope": scope,
        "pan_india": pan_india,
        "states": states,
        "cities": cities,
        "macro_region": meta.get("macro_region") or _infer_macro(row, states),
        "categories": categories,
        "retail": flags["retail"],
        "food_and_beverage": flags["food_and_beverage"],
        "religious": flags["religious"],
        "cultural": flags["cultural"],
        "priority": int(row.get("priority") or meta.get("priority") or 3),
        "lunar": bool(row.get("lunar")),
        "description": row.get("description") or meta.get("description") or "",
        "date_status": "confirmed" if occurs else "unavailable",
        "date_source": date_source,
        "date_policy": DATE_POLICY,
        "timezone": TIMEZONE,
    }


def _extension_date(extension: dict[str, Any], year: int) -> tuple[date | None, str | None]:
    lunar_key = extension.get("lunar_key")
    if lunar_key:
        raw = lunar_date_table().get(str(lunar_key), {}).get(year)
        if not raw:
            return None, None
        return date.fromisoformat(str(raw)), "lunar_dates"
    if extension.get("date_basis") == "gregorian_fixed" and extension.get("month") and extension.get("day"):
        return date(year, int(extension["month"]), int(extension["day"])), "gregorian_fixed"
    return None, None


def _categories(scope: str, pan_india: bool, flags: dict[str, bool]) -> list[str]:
    categories: list[str] = []
    if pan_india or scope == "national":
        categories.append("national")
    else:
        categories.append("regional")
        if scope == "state":
            categories.append("state")
    for flag in _CATEGORY_FLAGS:
        if flags[flag]:
            label = "food_and_beverage" if flag == "food_and_beverage" else flag
            categories.append(label)
    return categories


def _has_category(festival: dict[str, Any], category: str) -> bool:
    folded = category.strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "fb": "food_and_beverage",
        "food": "food_and_beverage",
        "f_and_b": "food_and_beverage",
        "state": "state",
        "regional": "regional",
    }
    folded = aliases.get(folded, folded)
    return folded in {str(item).casefold() for item in festival.get("categories") or []}


def _infer_scope(row: dict[str, Any]) -> str:
    region = str(row.get("region") or "")
    if region == "National":
        return "national"
    if region in {"West Bengal", "Kerala", "Tamil Nadu", "Punjab", "Assam"}:
        return "state"
    return "regional"


def _infer_states(row: dict[str, Any]) -> list[str]:
    region = str(row.get("region") or "")
    if region in {"West Bengal", "Kerala", "Tamil Nadu", "Punjab", "Assam", "Odisha", "Bihar", "Maharashtra"}:
        return [region]
    return []


def _infer_macro(row: dict[str, Any], states: list[str]) -> str | None:
    region = str(row.get("region") or "")
    if region in {"East", "West", "North", "South"}:
        return region
    coverage_places = load_coverage()["places"]
    for state in states:
        place = coverage_places.get(state.casefold())
        if place and place.get("macro_region"):
            return str(place["macro_region"])
    return None


def _meta_for(name: str) -> dict[str, Any] | None:
    return _all_meta().get(name)


def _all_meta() -> dict[str, dict[str, Any]]:
    coverage = load_coverage()
    meta = {str(name): dict(value) for name, value in coverage["festivals"].items()}
    for extension in coverage["extensions"]:
        name = str(extension.get("festival_name") or "")
        if name and name not in meta:
            meta[name] = extension
        elif name:
            merged = dict(meta[name])
            merged.update(extension)
            meta[name] = merged
    return meta


def _all_names() -> list[str]:
    names = [str(row["festival_name"]) for row in festivals_for_year(2026)]
    for extension in load_coverage()["extensions"]:
        name = str(extension.get("festival_name") or "")
        if name and name not in names:
            names.append(name)
    for name in load_coverage()["festivals"]:
        if name not in names:
            names.append(str(name))
    return names


def _extension_by_name(name: str) -> dict[str, Any] | None:
    for extension in load_coverage()["extensions"]:
        if str(extension.get("festival_name") or "") == name:
            return extension
    return None


def _any_known_year(name: str) -> int:
    for year in (2026, 2025, 2027, 2028):
        if _record_for_year(name, year) and _record_for_year(name, year).get("date"):
            return year
    return 2026


def _place_lookup(text: str, places: dict[str, Any]) -> dict[str, Any] | None:
    key = " ".join(text.strip().casefold().split())
    record = places.get(key)
    if record is None:
        return None
    return _place_record(key, record)


def _place_record(key: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": key,
        "kind": record.get("kind"),
        "city": record.get("city"),
        "state": record.get("state"),
        "macro_region": record.get("macro_region"),
        "pan_india": bool(record.get("pan_india")),
    }
