"""Data-driven Indian festival calendar. Lunar dates are explicit so they can be updated yearly."""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

# Maps catalog names to festivals/data/lunar_dates.json keys.
# Update that JSON when a new year's civil dates are published.
LUNAR_KEYS: dict[str, str] = {
    "Maha Shivaratri": "maha_shivaratri",
    "Holi": "holi",
    "Ugadi / Gudi Padwa": "ugadi",
    "Ram Navami": "ram_navami",
    "Mahavir Jayanti": "mahavir_jayanti",
    "Good Friday": "good_friday",
    "Eid ul-Fitr": "eid_ul_fitr",
    "Akshaya Tritiya": "akshaya_tritiya",
    "Buddha Purnima": "buddha_purnima",
    "Eid ul-Adha": "eid_ul_adha",
    "Rath Yatra": "rath_yatra",
    "Raksha Bandhan": "raksha_bandhan",
    "Janmashtami": "janmashtami",
    "Ganesh Chaturthi": "ganesh_chaturthi",
    "Onam": "onam",
    "Navratri / Durga Puja start": "navratri_begins",
    "Dussehra / Vijayadashami": "dussehra",
    "Karva Chauth": "karva_chauth",
    "Diwali": "diwali",
    "Govardhan Puja / Annakut": "govardhan_puja",
    "Bhai Dooj": "bhai_dooj",
    "Chhath Puja": "chhath_puja",
    "Guru Nanak Jayanti": "guru_nanak_jayanti",
    "Lohri": "lohri",
    "Pongal": "pongal",
    "Vasant Panchami": "vasant_panchami",
    "Holika Dahan": "holika_dahan",
    "Easter": "easter",
    "Muharram": "muharram",
    "Guru Purnima": "guru_purnima",
    "Mahalaya": "mahalaya",
    "Durga Puja": "durga_puja",
    "Kali Puja": "kali_puja",
    "Karthigai Deepam": "karthigai_deepam",
}


@lru_cache(maxsize=1)
def lunar_date_table() -> dict[str, dict[int, str]]:
    path = Path(__file__).resolve().parent / "data" / "lunar_dates.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, dict[int, str]] = {}
    for key, years in (payload.get("dates") or {}).items():
        table[str(key)] = {int(year): str(value) for year, value in years.items()}
    return table


# Dates that depend on the lunar calendar are listed per year rather than computed.
# Update this table annually; do not pretend every festival is solar-fixed.
FESTIVALS: list[dict[str, Any]] = [
    {"festival_name": "New Year", "month": 1, "day": 1, "region": "National", "priority": 3, "lunar": False, "description": "Gregorian new year."},
    {"festival_name": "Makar Sankranti / Pongal", "month": 1, "day": 14, "region": "National", "priority": 2, "lunar": False, "description": "Harvest festival."},
    {"festival_name": "Republic Day", "month": 1, "day": 26, "region": "National", "priority": 1, "lunar": False, "description": "National civic holiday."},
    {"festival_name": "Maha Shivaratri", "dates": {2025: "2025-02-26", 2026: "2026-02-15", 2027: "2027-03-06"}, "region": "National", "priority": 2, "lunar": True, "description": "Shaivite festival."},
    {"festival_name": "Holi", "dates": {2025: "2025-03-14", 2026: "2026-03-03", 2027: "2027-03-22"}, "region": "National", "priority": 1, "lunar": True, "description": "Festival of colours."},
    {"festival_name": "Ugadi / Gudi Padwa", "dates": {2025: "2025-03-30", 2026: "2026-03-19", 2027: "2027-04-07"}, "region": "South / West", "priority": 2, "lunar": True, "description": "Regional new year."},
    {"festival_name": "Ram Navami", "dates": {2025: "2025-04-06", 2026: "2026-03-26", 2027: "2027-04-15"}, "region": "National", "priority": 2, "lunar": True, "description": "Birth of Rama."},
    {"festival_name": "Mahavir Jayanti", "dates": {2025: "2025-04-10", 2026: "2026-03-31", 2027: "2027-04-21"}, "region": "National", "priority": 3, "lunar": True, "description": "Jain festival."},
    {"festival_name": "Good Friday", "dates": {2025: "2025-04-18", 2026: "2026-04-03", 2027: "2027-03-26"}, "region": "National", "priority": 3, "lunar": True, "description": "Christian observance."},
    {"festival_name": "Eid ul-Fitr", "dates": {2025: "2025-03-31", 2026: "2026-03-20", 2027: "2027-03-10"}, "region": "National", "priority": 1, "lunar": True, "description": "End of Ramadan. Confirm locally."},
    {"festival_name": "Akshaya Tritiya", "dates": {2025: "2025-04-30", 2026: "2026-04-19", 2027: "2027-05-09"}, "region": "National", "priority": 2, "lunar": True, "description": "Auspicious shopping day."},
    {"festival_name": "Buddha Purnima", "dates": {2025: "2025-05-12", 2026: "2026-05-01", 2027: "2027-05-20"}, "region": "National", "priority": 3, "lunar": True, "description": "Birth of the Buddha."},
    {"festival_name": "Eid ul-Adha", "dates": {2025: "2025-06-07", 2026: "2026-05-27", 2027: "2027-05-17"}, "region": "National", "priority": 1, "lunar": True, "description": "Festival of sacrifice. Confirm locally."},
    {"festival_name": "Rath Yatra", "dates": {2025: "2025-06-27", 2026: "2026-07-16", 2027: "2027-07-05"}, "region": "East", "priority": 3, "lunar": True, "description": "Jagannath chariot festival."},
    {"festival_name": "Independence Day", "month": 8, "day": 15, "region": "National", "priority": 1, "lunar": False, "description": "National civic holiday."},
    {"festival_name": "Raksha Bandhan", "dates": {2025: "2025-08-09", 2026: "2026-08-28", 2027: "2027-08-17"}, "region": "National", "priority": 2, "lunar": True, "description": "Sibling bond festival."},
    {"festival_name": "Janmashtami", "dates": {2025: "2025-08-16", 2026: "2026-09-04", 2027: "2027-08-25"}, "region": "National", "priority": 1, "lunar": True, "description": "Birth of Krishna."},
    {"festival_name": "Ganesh Chaturthi", "dates": {2025: "2025-08-27", 2026: "2026-09-14", 2027: "2027-09-04"}, "region": "West", "priority": 1, "lunar": True, "description": "Ganesha festival."},
    {"festival_name": "Onam", "dates": {2025: "2025-09-05", 2026: "2026-08-26", 2027: "2027-09-15"}, "region": "Kerala", "priority": 2, "lunar": True, "description": "Kerala harvest festival."},
    {"festival_name": "Navratri / Durga Puja start", "dates": {2025: "2025-09-22", 2026: "2026-10-11", 2027: "2027-10-01"}, "region": "National", "priority": 1, "lunar": True, "description": "Nine nights of Durga."},
    {"festival_name": "Dussehra / Vijayadashami", "dates": {2025: "2025-10-02", 2026: "2026-10-21", 2027: "2027-10-10"}, "region": "National", "priority": 1, "lunar": True, "description": "Victory of good over evil."},
    {"festival_name": "Gandhi Jayanti", "month": 10, "day": 2, "region": "National", "priority": 2, "lunar": False, "description": "Birth anniversary of Mahatma Gandhi."},
    {"festival_name": "Karva Chauth", "dates": {2025: "2025-10-10", 2026: "2026-10-29", 2027: "2027-10-18"}, "region": "North", "priority": 3, "lunar": True, "description": "North Indian fasting festival."},
    {"festival_name": "Diwali", "dates": {2025: "2025-10-20", 2026: "2026-11-08", 2027: "2027-10-29"}, "region": "National", "priority": 1, "lunar": True, "description": "Festival of lights."},
    {"festival_name": "Govardhan Puja / Annakut", "dates": {2025: "2025-10-22", 2026: "2026-11-09", 2027: "2027-10-30"}, "region": "National", "priority": 2, "lunar": True, "description": "Day after Diwali."},
    {"festival_name": "Bhai Dooj", "dates": {2025: "2025-10-23", 2026: "2026-11-10", 2027: "2027-10-31"}, "region": "National", "priority": 3, "lunar": True, "description": "Sibling festival after Diwali."},
    {"festival_name": "Chhath Puja", "dates": {2025: "2025-10-27", 2026: "2026-11-15", 2027: "2027-11-04"}, "region": "East", "priority": 2, "lunar": True, "description": "Sun worship festival."},
    {"festival_name": "Guru Nanak Jayanti", "dates": {2025: "2025-11-05", 2026: "2026-11-24", 2027: "2027-11-14"}, "region": "National", "priority": 2, "lunar": True, "description": "Sikh festival."},
    {"festival_name": "Lohri", "dates": {2025: "2025-01-13", 2026: "2026-01-13", 2027: "2027-01-13", 2028: "2028-01-13"}, "region": "Punjab", "priority": 2, "lunar": False, "description": "Punjabi harvest festival."},
    {"festival_name": "Pongal", "dates": {2025: "2025-01-14", 2026: "2026-01-14", 2027: "2027-01-14", 2028: "2028-01-15"}, "region": "Tamil Nadu", "priority": 2, "lunar": False, "description": "Tamil harvest festival."},
    {"festival_name": "Vasant Panchami", "dates": {2025: "2025-02-02", 2026: "2026-01-23", 2027: "2027-02-12", 2028: "2028-02-02"}, "region": "North", "priority": 3, "lunar": True, "description": "Saraswati and spring festival."},
    {"festival_name": "Holika Dahan", "dates": {2025: "2025-03-13", 2026: "2026-03-03", 2027: "2027-03-21", 2028: "2028-03-10"}, "region": "National", "priority": 2, "lunar": True, "description": "Bonfire night before Holi."},
    {"festival_name": "Baisakhi", "month": 4, "day": 13, "region": "Punjab", "priority": 2, "lunar": False, "description": "Punjabi harvest and Sikh New Year."},
    {"festival_name": "Vishu", "month": 4, "day": 14, "region": "Kerala", "priority": 3, "lunar": False, "description": "Malayali New Year."},
    {"festival_name": "Bohag Bihu", "month": 4, "day": 14, "region": "Assam", "priority": 3, "lunar": False, "description": "Assamese New Year."},
    {"festival_name": "Easter", "dates": {2025: "2025-04-20", 2026: "2026-04-05", 2027: "2027-03-28", 2028: "2028-04-16"}, "region": "National", "priority": 3, "lunar": True, "description": "Christian movable feast. Update annually."},
    {"festival_name": "Labour Day", "month": 5, "day": 1, "region": "National", "priority": 3, "lunar": False, "description": "International Workers' Day."},
    {"festival_name": "Muharram", "dates": {2025: "2025-07-06", 2026: "2026-06-26", 2027: "2027-06-16", 2028: "2028-06-04"}, "region": "National", "priority": 2, "lunar": True, "description": "Islamic observance. Confirm locally by moon sighting."},
    {"festival_name": "Guru Purnima", "dates": {2025: "2025-07-10", 2026: "2026-07-29", 2027: "2027-07-18", 2028: "2028-07-06"}, "region": "National", "priority": 3, "lunar": True, "description": "Day of honouring teachers."},
    {"festival_name": "Teachers' Day", "month": 9, "day": 5, "region": "National", "priority": 3, "lunar": False, "description": "Birth anniversary of Dr. Sarvepalli Radhakrishnan."},
    {"festival_name": "Mahalaya", "dates": {2025: "2025-09-21", 2026: "2026-10-10", 2027: "2027-09-29", 2028: "2028-09-18"}, "region": "West Bengal", "priority": 2, "lunar": True, "description": "Start of Durga Puja observances in Bengal."},
    {"festival_name": "Durga Puja", "dates": {2025: "2025-09-30", 2026: "2026-10-17", 2027: "2027-10-07", 2028: "2028-09-26"}, "region": "West Bengal", "priority": 1, "lunar": True, "description": "Maha Ashtami of Durga Puja."},
    {"festival_name": "Kali Puja", "dates": {2025: "2025-10-20", 2026: "2026-11-08", 2027: "2027-10-29", 2028: "2028-10-17"}, "region": "West Bengal", "priority": 2, "lunar": True, "description": "Bengali festival coinciding with Diwali."},
    {"festival_name": "Karthigai Deepam", "dates": {2025: "2025-12-04", 2026: "2026-11-24", 2027: "2027-12-13", 2028: "2028-12-01"}, "region": "Tamil Nadu", "priority": 3, "lunar": True, "description": "Tamil festival of lights."},
    {"festival_name": "Constitution Day", "month": 11, "day": 26, "region": "National", "priority": 3, "lunar": False, "description": "Adoption of the Constitution of India."},
    {"festival_name": "Children's Day", "month": 11, "day": 14, "region": "National", "priority": 3, "lunar": False, "description": "Birth anniversary of Jawaharlal Nehru."},
    {"festival_name": "Christmas", "month": 12, "day": 25, "region": "National", "priority": 1, "lunar": False, "description": "Christian festival."},
]


def occurrence_for_year(entry: dict[str, Any], year: int) -> date | None:
    lunar_key = entry.get("lunar_key") or LUNAR_KEYS.get(str(entry.get("festival_name") or ""))
    if lunar_key:
        override = lunar_date_table().get(str(lunar_key), {}).get(year)
        if override:
            return date.fromisoformat(override)
    dates = entry.get("dates") or {}
    if year in dates:
        return date.fromisoformat(str(dates[year]))
    if entry.get("month") and entry.get("day"):
        return date(year, int(entry["month"]), int(entry["day"]))
    return None


def festivals_for_year(year: int, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in FESTIVALS:
        occurs = occurrence_for_year(entry, year)
        if occurs is None:
            continue
        items.append(
            {
                "festival_name": entry["festival_name"],
                "date": occurs.isoformat(),
                "year": year,
                "region": entry.get("region"),
                "description": entry.get("description"),
                "enabled": True,
                "priority": entry.get("priority", 3),
                "lunar": bool(entry.get("lunar")),
                "date_status": "confirmed",
                "date_note": (
                    "Lunar/Islamic/movable date. Update this table annually."
                    if entry.get("lunar")
                    else ""
                ),
            }
        )
    items.sort(key=lambda item: (item["date"], item["festival_name"]))
    return items
