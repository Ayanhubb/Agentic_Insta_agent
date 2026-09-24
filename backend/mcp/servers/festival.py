"""Festival tools. Dates come from FestivalService; campaigns stay owner-scoped."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from backend.mcp.errors import MalformedArguments
from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext


def _on_or_after(items: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    upcoming = [item for item in items if date.fromisoformat(str(item["date"])) >= today]
    pool = upcoming or items
    pool.sort(key=lambda item: str(item["date"]))
    return pool[0] if pool else None


def festival_tools(gateway: RepositoryGateway) -> list[MCPTool]:
    async def get_upcoming_festivals(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        within_days = int(arguments.get("within_days", 60))
        today = gateway.today(gateway.timezone_name(tenant.tenant_id))
        end = today + timedelta(days=within_days)
        catalog: list[dict[str, Any]] = []
        for year in range(today.year, end.year + 1):
            catalog.extend(gateway.festival_catalog(year))
        campaigns = {
            (str(row["festival_name"]).casefold(), int(row["year"])): row
            for row in gateway.campaigns(tenant.tenant_id)
        }
        festivals: list[dict[str, Any]] = []
        for item in catalog:
            occurs = date.fromisoformat(str(item["date"]))
            if occurs < today or occurs > end:
                continue
            festivals.append(
                {
                    "festival_name": item["festival_name"],
                    "date": item["date"],
                    "year": item["year"],
                    "region": item.get("region"),
                    "description": item.get("description"),
                    "campaign": campaigns.get((str(item["festival_name"]).casefold(), int(item["year"]))),
                }
            )
        festivals.sort(key=lambda row: (str(row["date"]), str(row["festival_name"])))
        return {"festivals": festivals, "as_of": today.isoformat()}

    async def get_festival_details(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        festival_name = arguments.get("festival_name")
        campaign_id = arguments.get("campaign_id")
        if festival_name is not None:
            festival_name = festival_name.strip()
            if not festival_name:
                raise MalformedArguments("Argument 'festival_name' is too short.")
        if not festival_name and not campaign_id:
            raise MalformedArguments("Missing required argument 'festival_name' or 'campaign_id'.")
        today = gateway.today(gateway.timezone_name(tenant.tenant_id))
        if campaign_id:
            campaign = gateway.owned_campaign(tenant.tenant_id, campaign_id)
            if campaign is None:
                return {"found": False, "festival": None}
            catalog = gateway.festival_catalog(int(campaign["year"]))
            match = next(
                (
                    item
                    for item in catalog
                    if str(item["festival_name"]).casefold() == str(campaign["festival_name"]).casefold()
                ),
                None,
            )
            return {"found": True, "festival": _detail(match, campaign)}
        matches: list[dict[str, Any]] = []
        for year in (today.year, today.year + 1):
            for item in gateway.festival_catalog(year):
                if str(item["festival_name"]).casefold() == festival_name.casefold():
                    matches.append(item)
        owned = [
            row
            for row in gateway.campaigns(tenant.tenant_id)
            if str(row["festival_name"]).casefold() == festival_name.casefold()
        ]
        chosen = _on_or_after(matches, today)
        if chosen is None and not owned:
            return {"found": False, "festival": None}
        campaign = None
        if chosen is not None:
            campaign = next((row for row in owned if int(row["year"]) == int(chosen["year"])), None)
        elif owned:
            campaign = owned[0]
            chosen = None
        return {"found": True, "festival": _detail(chosen, campaign)}

    return [
        MCPTool(
            name="get_upcoming_festivals",
            description="List festivals coming up for this tenant, with that tenant's campaigns only.",
            input_schema=object_schema(
                {"within_days": {"type": "integer", "minimum": 1, "maximum": 366}},
            ),
            server="festival",
            handler=get_upcoming_festivals,
        ),
        MCPTool(
            name="get_festival_details",
            description="Load one festival. Campaign ids belonging to another tenant are not returned.",
            input_schema=object_schema(
                {
                    "festival_name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "campaign_id": {"type": "string", "minLength": 1, "maxLength": 64},
                }
            ),
            server="festival",
            handler=get_festival_details,
        ),
    ]


def _detail(catalog_item: dict[str, Any] | None, campaign: dict[str, Any] | None) -> dict[str, Any]:
    if catalog_item is None and campaign is not None:
        return {
            "festival_name": campaign["festival_name"],
            "date": campaign["festival_date"],
            "year": campaign["year"],
            "region": None,
            "description": None,
            "campaign": campaign,
        }
    assert catalog_item is not None
    return {
        "festival_name": catalog_item["festival_name"],
        "date": catalog_item["date"],
        "year": catalog_item["year"],
        "region": catalog_item.get("region"),
        "description": catalog_item.get("description"),
        "campaign": campaign,
    }
