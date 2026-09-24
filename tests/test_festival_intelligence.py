"""Festival intelligence: stored dates, regional filters, campaign windows, uniqueness."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from festivals.campaign_rules import counts_as_published, remaining_posts
from festivals.india_festivals import festivals_for_year, lunar_date_table
from festivals.mcp_tools import TOOL_NAMES, FestivalToolServer, tool_specs

_POLICY_PATH = Path(__file__).resolve().parents[1] / "scheduler" / "policies.py"
_policy_spec = importlib.util.spec_from_file_location("festival_scheduler_policies", _POLICY_PATH)
assert _policy_spec is not None and _policy_spec.loader is not None
_scheduler_policies = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(_scheduler_policies)

LUNAR_PATH = Path(__file__).resolve().parents[1] / "festivals" / "data" / "lunar_dates.json"


def _server() -> FestivalToolServer:
    return FestivalToolServer()


def _names(payload: dict) -> set[str]:
    return {item["festival_name"] for item in payload["festivals"]}


def test_tool_surface_returns_json() -> None:
    assert {item["name"] for item in tool_specs()} == set(TOOL_NAMES)
    raw = _server().call_json(
        "get_festival_details",
        {"festival_name": "Diwali", "year": 2026},
    )
    body = json.loads(raw)
    assert body["ok"] is True
    assert body["tool"] == "get_festival_details"
    assert body["festival"]["date"] == "2026-11-08"
    assert body["date_policy"] == "stored_dates_only"


def test_kolkata_upcoming_includes_bengal_and_national() -> None:
    body = _server().call(
        "get_upcoming_festivals",
        {"on_date": "2026-10-01", "location": "Kolkata", "within_days": 60, "include_national": True},
    )
    assert body["ok"] is True
    assert body["timezone"] == "Asia/Kolkata"
    assert body["location"]["city"] == "Kolkata"
    assert body["location"]["state"] == "West Bengal"
    names = _names(body)
    assert "Durga Puja" in names
    assert "Mahalaya" in names
    assert "Kali Puja" in names
    assert "Diwali" in names
    assert "Onam" not in names
    assert "Karva Chauth" not in names
    durga = next(item for item in body["festivals"] if item["festival_name"] == "Durga Puja")
    assert durga["date"] == "2026-10-17"
    assert "West Bengal" in durga["states"]
    assert "Kolkata" in durga["cities"]
    assert all(item["date"] for item in body["festivals"])


def test_west_bengal_regional_filter() -> None:
    body = _server().call(
        "get_regional_festivals",
        {"region": "West Bengal", "year": 2026, "include_national": False},
    )
    names = _names(body)
    assert {"Durga Puja", "Mahalaya", "Kali Puja", "Vasant Panchami"} <= names
    assert "Onam" not in names
    assert "Pongal" not in names
    assert "Diwali" not in names
    assert "Bohag Bihu" not in names
    alias = _server().call("get_festival_details", {"festival_name": "Saraswati Puja", "year": 2026})
    assert alias["festival"]["festival_name"] == "Vasant Panchami"
    assert alias["festival"]["date"] == "2026-01-23"


def test_pan_india_is_national_only() -> None:
    body = _server().call("get_regional_festivals", {"region": "pan-India", "year": 2026})
    names = _names(body)
    assert "Diwali" in names
    assert "Holi" in names
    assert "Republic Day" in names
    assert "Eid ul-Fitr" in names
    assert "Durga Puja" not in names
    assert "Onam" not in names
    assert "Pongal" not in names
    assert all(item["pan_india"] for item in body["festivals"])


def test_regional_filtering_keeps_states_apart() -> None:
    kerala = _names(_server().call("get_regional_festivals", {"region": "Kerala", "year": 2026}))
    punjab = _names(_server().call("get_regional_festivals", {"region": "Punjab", "year": 2026}))
    tamil_nadu = _names(_server().call("get_regional_festivals", {"region": "Tamil Nadu", "year": 2026}))
    assert "Onam" in kerala
    assert "Vishu" in kerala
    assert "Durga Puja" not in kerala
    assert "Lohri" in punjab
    assert "Baisakhi" in punjab
    assert "Onam" not in punjab
    assert "Pongal" in tamil_nadu
    assert "Lohri" not in tamil_nadu


def test_festival_date_comes_only_from_stored_data() -> None:
    catalog = next(item for item in festivals_for_year(2026) if item["festival_name"] == "Diwali")
    details = _server().call("get_festival_details", {"festival_name": "Diwali", "year": 2026})
    assert details["festival"]["date"] == catalog["date"] == "2026-11-08"
    assert details["festival"]["date_status"] == "confirmed"
    assert details["festival"]["date_policy"] == "stored_dates_only"

    missing = _server().call("get_festival_details", {"festival_name": "Diwali", "year": 2030})
    assert missing["ok"] is True
    assert missing["festival"]["date"] is None
    assert missing["festival"]["date_status"] == "unavailable"

    rejected = _server().call(
        "get_festival_details",
        {"festival_name": "Diwali", "year": 2026, "date": "2026-01-01"},
    )
    assert rejected["ok"] is False
    assert rejected["error"] == "date_not_allowed"
    assert rejected["date"] is None

    stored = json.loads(LUNAR_PATH.read_text(encoding="utf-8"))["dates"]["hariyali_teej"]["2026"]
    assert lunar_date_table()["hariyali_teej"][2026] == stored
    teej = _server().call("get_festival_details", {"festival_name": "Hariyali Teej", "year": 2026})
    assert teej["festival"]["date"] == stored
    assert teej["festival"]["date_source"] == "lunar_dates"
    assert "Hariyali Teej" not in {item["festival_name"] for item in festivals_for_year(2026)}

    unstored = _server().call("get_festival_campaign", {"festival_name": "Hariyali Teej", "year": 2030, "user_id": "user-1"})
    assert unstored["ok"] is False
    assert unstored["error"] == "date_unavailable"
    assert unstored["date"] is None


def test_campaign_window_pre_day_and_catch_up() -> None:
    server = _server()
    before = server.call(
        "get_festival_campaign",
        {
            "festival_name": "Diwali",
            "year": 2026,
            "user_id": "kolkata-boutique",
            "on_date": "2026-11-07",
            "business_name": "Pal Jewels",
        },
    )
    assert before["ok"] is True
    window = before["window"]
    assert window["timezone"] == "Asia/Kolkata"
    assert window["festival_date"] == "2026-11-08"
    assert window["required_posts"] == 2
    assert window["phases"]["pre-festival"]["start"] == "2026-11-07"
    assert window["phases"]["festival-day"]["start"] == "2026-11-08"
    assert window["phases"]["post-festival"]["start"] == "2026-11-09"
    assert window["phases"]["post-festival"]["end"] == "2026-11-15"
    assert window["phases"]["post-festival"]["scheduler_kind"] == "catch-up"
    assert before["active_phase"]["phase"] == "pre-festival"
    assert before["active_phase"]["content"]["festival_date"] == "2026-11-08"
    slots = before["slots"]
    assert [slot["local_date"] for slot in slots] == ["2026-11-07", "2026-11-08"]
    assert before["daily_slot_uniqueness"] is True
    assert len({slot["local_date"] for slot in slots}) == len(slots)

    day = server.call(
        "get_festival_campaign",
        {"festival_name": "Diwali", "year": 2026, "user_id": "kolkata-boutique", "on_date": "2026-11-08"},
    )
    assert day["active_phase"]["scheduler_kind"] == "festival-day"

    quiet = _server().call(
        "get_festival_campaign",
        {"festival_name": "Diwali", "year": 2026, "user_id": "other-user", "on_date": "2026-11-10"},
    )
    assert quiet["active_phase"] is None

    catch_up = _server().call(
        "get_festival_campaign",
        {
            "festival_name": "Diwali",
            "year": 2026,
            "user_id": "catch-up-user",
            "on_date": "2026-11-10",
            "publication_statuses": ["PUBLISHED", "FAILED", "AMBIGUOUS_PUBLICATION"],
        },
    )
    assert catch_up["active_phase"]["phase"] == "post-festival"
    assert catch_up["active_phase"]["scheduler_kind"] == "catch-up"
    assert catch_up["campaign"]["published_posts"] == 1
    assert catch_up["campaign"]["remaining_posts"] == 1
    assert catch_up["campaign"]["required_posts"] == 2

    too_late = _server().call(
        "get_festival_campaign",
        {
            "festival_name": "Diwali",
            "year": 2026,
            "user_id": "late-user",
            "on_date": "2026-11-16",
            "published_posts": 1,
            "generated_posts": 1,
        },
    )
    assert too_late["active_phase"] is None


def test_duplicate_campaign_keeps_one_record() -> None:
    server = _server()
    first = server.call(
        "get_festival_campaign",
        {"festival_name": "Durga Puja", "year": 2026, "user_id": "kolkata-1", "on_date": "2026-10-16"},
    )
    forged = server.call(
        "get_festival_campaign",
        {
            "festival_name": "durga puja",
            "year": 2026,
            "user_id": "kolkata-1",
            "on_date": "2026-10-17",
            "required_posts": 9,
            "date": "2020-01-01",
        },
    )
    assert first["ok"] is True
    assert first["campaign"]["created"] is True
    assert first["campaign"]["duplicate"] is False
    assert first["campaign"]["festival_date"] == "2026-10-17"
    assert forged["ok"] is False
    assert forged["error"] == "date_not_allowed"
    assert len(server.campaigns) == 1
    second = server.call(
        "get_festival_campaign",
        {"festival_name": "Durga Puja", "year": 2026, "user_id": "kolkata-1", "required_posts": 9},
    )
    assert second["campaign"]["duplicate"] is True
    assert second["campaign"]["created"] is False
    assert second["campaign"]["campaign_key"] == first["campaign"]["campaign_key"]
    assert second["campaign"]["campaign_id"] == first["campaign"]["campaign_id"]
    assert second["campaign"]["required_posts"] == 2
    assert second["campaign"]["festival_date"] == "2026-10-17"
    assert len(server.campaigns) == 1
    other = server.call(
        "get_festival_campaign",
        {"festival_name": "Durga Puja", "year": 2026, "user_id": "kolkata-2"},
    )
    assert other["campaign"]["created"] is True
    assert other["campaign"]["campaign_key"] != first["campaign"]["campaign_key"]
    assert len(server.campaigns) == 2


def test_business_relevance_for_kolkata_retail_and_food() -> None:
    server = _server()
    jewellery = {
        "business_name": "Pal Jewels",
        "business_type": "boutique",
        "business_category": "jewellery",
        "location": "Kolkata",
        "products": ["kundan necklace"],
    }
    durga = server.call(
        "get_business_relevance",
        {"festival_name": "Durga Puja", "year": 2026, "business": jewellery},
    )
    onam = server.call(
        "get_business_relevance",
        {"festival_name": "Onam", "year": 2026, "business": jewellery},
    )
    restaurant = server.call(
        "get_business_relevance",
        {
            "festival_name": "Eid ul-Fitr",
            "year": 2026,
            "business": {"business_name": "Kolkata Kitchen", "business_type": "restaurant", "location": "Kolkata, West Bengal"},
        },
    )
    assert durga["relevance"]["relevant"] is True
    assert durga["relevance"]["retail_fit"] is True
    assert durga["festival"]["date"] == "2026-10-17"
    assert onam["relevance"]["relevant"] is False
    assert onam["relevance"]["location_match"] is False
    assert restaurant["relevance"]["relevant"] is True
    assert restaurant["relevance"]["food_and_beverage_fit"] is True
    assert restaurant["relevance"]["pan_india"] is True


def test_verified_counting_matches_scheduler() -> None:
    for status in ("PUBLISHED", "FAILED", "AMBIGUOUS_PUBLICATION", "PUBLISHING"):
        assert counts_as_published(status) is _scheduler_policies.counts_as_published(status)
    assert remaining_posts(2, 1) == _scheduler_policies.remaining_festival_posts(2, 1) == 1
    assert remaining_posts(2, 0) == 2
    retail = _server().call(
        "get_upcoming_festivals",
        {"on_date": "2026-10-01", "location": "Kolkata", "within_days": 30, "category": "retail"},
    )
    assert retail["ok"] is True
    assert "Durga Puja" in _names(retail)
    assert all(item["retail"] for item in retail["festivals"])


def test_catalog_dates_stay_on_the_existing_calendar() -> None:
    items = festivals_for_year(2026)
    diwali = next(item for item in items if item["festival_name"] == "Diwali")
    assert diwali["date"] == "2026-11-08"
    assert diwali["lunar"] is True
    assert len(items) >= 30
    assert date.fromisoformat(diwali["date"]).isoformat() == "2026-11-08"
