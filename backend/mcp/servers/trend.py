"""Trend MCP tools.

These tools read stored trend research, festivals, and brand or product records.
Authorized Instagram account intelligence is registered from ``account.py``.
These tools do not publish and they do not accept a tenant id from the model.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from backend.mcp.errors import MalformedArguments
from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext

_DATE_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$")
_INDUSTRY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_REGION = re.compile(r"^[A-Z]{2}(?:-[A-Z0-9]{2,12}){0,2}$")
_REGION_ALIASES = {
    "WEST BENGAL": "IN-WB",
    "KOLKATA": "IN-WB-KOLKATA",
    "CALCUTTA": "IN-WB-KOLKATA",
}
_CONTENT_TYPE = re.compile(r"^[A-Z0-9_]{1,32}$")
_DEFAULT_LIMIT = 10

_FILTERS: dict[str, dict[str, Any]] = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 25},
    "date_range": {"type": "string", "minLength": 1, "maxLength": 21},
    "industry": {"type": "string", "minLength": 1, "maxLength": 32},
    "region": {"type": "string", "minLength": 2, "maxLength": 16},
    "festival": {"type": "string", "minLength": 1, "maxLength": 120},
    "content_type": {"type": "string", "minLength": 1, "maxLength": 32},
}


def trend_tools(gateway: RepositoryGateway) -> list[MCPTool]:
    async def get_current_trends(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return _trends(gateway, tenant, arguments, fresh_only=True)

    async def get_regional_trends(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if "region" not in arguments:
            raise MalformedArguments("Missing required argument 'region'.")
        return _trends(gateway, tenant, arguments, fresh_only=True)

    async def get_trend_evidence(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        row = gateway.trend_evidence(tenant.tenant_id, arguments["observation_id"])
        if row is None:
            return {"found": False, "observation": None, "source": "trend_observations"}
        return {"found": True, "observation": row, "source": "trend_observations"}

    async def get_latest_trend_report(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        report = gateway.latest_trend_report(tenant.tenant_id)
        if report is None:
            return {"found": False, "stale": False, "report": None, "source": "trend_reports"}
        return {"found": True, "stale": report["stale"], "report": report, "source": "trend_reports"}

    async def get_festival_opportunities(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        limit, start_dt, end_dt = _window(arguments)
        today = gateway.today(gateway.timezone_name(tenant.tenant_id))
        start = start_dt.date() if start_dt else today
        end = (end_dt - timedelta(days=1)).date() if end_dt else today + timedelta(days=60)
        campaigns = {
            (str(row["festival_name"]).casefold(), int(row["year"])): row["id"]
            for row in gateway.campaigns(tenant.tenant_id)
        }
        opportunities: list[dict[str, Any]] = []
        for year in range(start.year, end.year + 1):
            for item in gateway.festival_catalog(year):
                occurs = date.fromisoformat(str(item["date"]))
                if occurs < start or occurs > end:
                    continue
                owned = (str(item["festival_name"]).casefold(), int(item["year"])) in campaigns
                opportunities.append(
                    {
                        "kind": "festival",
                        "title": item["festival_name"],
                        "date": item["date"],
                        "region": item.get("region"),
                        "has_campaign": owned,
                        "reason": "Upcoming festival from the festival catalog.",
                    }
                )
        opportunities.sort(key=lambda row: (str(row["date"]), str(row["title"])))
        return {
            "found": bool(opportunities),
            "source": "festival_service",
            "limit": limit,
            "truncated": len(opportunities) > limit,
            "opportunities": opportunities[:limit],
        }

    async def get_business_opportunities(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = _limit(arguments)
        brand = gateway.brand_snapshot(tenant.tenant_id)
        products = gateway.active_products(tenant.tenant_id, limit=limit)
        opportunities: list[dict[str, Any]] = []
        if brand["business_name"] and not brand["has_guidelines"] and not brand["brand_style"]:
            opportunities.append(
                {
                    "kind": "brand",
                    "title": brand["company_name"] or brand["business_name"],
                    "reason": "No brand guidelines are stored.",
                }
            )
        for product in products:
            if product["has_offer"]:
                reason = "Active offer can be featured."
                kind = "offer"
            else:
                reason = "Active product has no offer."
                kind = "product"
            opportunities.append({"kind": kind, "title": product["name"], "sku": product["sku"], "reason": reason})
        return {
            "found": bool(opportunities),
            "source": "products",
            "limit": limit,
            "truncated": len(opportunities) > limit,
            "opportunities": opportunities[:limit],
        }

    async def get_content_opportunities(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        limit, start, end = _window(arguments)
        performance = gateway.content_performance(tenant.tenant_id, start=start, end=end)
        published_types = {item["post_type"] for item in performance["by_type"]}
        opportunities: list[dict[str, Any]] = []
        if performance["published"] == 0:
            opportunities.append(
                {
                    "kind": "gap",
                    "title": "No published posts",
                    "reason": "No verified Instagram posts are stored for this range.",
                }
            )
        for post_type, title in (
            ("FESTIVAL", "Festival post"),
            ("DAILY_RETAIL_POST", "Daily retail post"),
        ):
            if post_type not in published_types:
                opportunities.append(
                    {
                        "kind": "content_type",
                        "title": title,
                        "content_type": post_type,
                        "reason": "This content type has no verified publish in range.",
                    }
                )
        return {
            "found": bool(opportunities),
            "source": "instagram_posts",
            "limit": limit,
            "truncated": len(opportunities) > limit,
            "opportunities": opportunities[:limit],
        }

    return [
        MCPTool(
            name="get_current_trends",
            description=(
                "Fresh normalized trend observations already collected for this tenant. "
                "Stale research rows are excluded. This tool does not browse or fetch sources."
            ),
            input_schema=object_schema(_FILTERS),
            server="trend",
            handler=get_current_trends,
        ),
        MCPTool(
            name="get_trend_evidence",
            description="Short evidence for one observation owned by this tenant.",
            input_schema=object_schema(
                {"observation_id": {"type": "string", "minLength": 1, "maxLength": 64}},
                required=["observation_id"],
            ),
            server="trend",
            handler=get_trend_evidence,
        ),
        MCPTool(
            name="get_regional_trends",
            description="Fresh trend observations for one region.",
            input_schema=object_schema(_FILTERS, required=["region"]),
            server="trend",
            handler=get_regional_trends,
        ),
        MCPTool(
            name="get_festival_opportunities",
            description="Upcoming festivals from the festival catalog, with this tenant's campaign flag only.",
            input_schema=object_schema(_FILTERS),
            server="trend",
            handler=get_festival_opportunities,
        ),
        MCPTool(
            name="get_business_opportunities",
            description="Compact product, offer, and brand gaps for this tenant.",
            input_schema=object_schema({"limit": _FILTERS["limit"]}),
            server="trend",
            handler=get_business_opportunities,
        ),
        MCPTool(
            name="get_content_opportunities",
            description="Content gaps derived from stored Instagram posts.",
            input_schema=object_schema(_FILTERS),
            server="trend",
            handler=get_content_opportunities,
        ),
        MCPTool(
            name="get_latest_trend_report",
            description="Latest stored trend report for this tenant, marked when it is stale.",
            input_schema=object_schema(),
            server="trend",
            handler=get_latest_trend_report,
        ),
    ]


def _trends(
    gateway: RepositoryGateway,
    tenant: TenantContext,
    arguments: dict[str, Any],
    *,
    fresh_only: bool,
) -> dict[str, Any]:
    limit, start, end = _window(arguments)
    payload = gateway.trend_observations(
        tenant.tenant_id,
        industry=_industry(arguments.get("industry")),
        region=_region(arguments.get("region")),
        festival=_festival(arguments.get("festival")),
        content_type=_content_type(arguments.get("content_type")),
        start=start,
        end=end,
        limit=limit,
        fresh_only=fresh_only,
    )
    payload["limit"] = limit
    payload["found"] = bool(payload["observations"])
    for item in payload["observations"]:
        item.pop("evidence", None)
    return payload


def _window(arguments: dict[str, Any]) -> tuple[int, datetime | None, datetime | None]:
    start_date, end_date = _date_range(arguments.get("date_range"))
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc) if start_date else None
    end = None
    if end_date is not None:
        end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
    return _limit(arguments), start, end


def _limit(arguments: dict[str, Any]) -> int:
    return int(arguments.get("limit", _DEFAULT_LIMIT))


def _date_range(value: str | None) -> tuple[date | None, date | None]:
    if value is None:
        return None, None
    match = _DATE_RANGE.fullmatch(value.strip())
    if match is None:
        raise MalformedArguments("Argument 'date_range' is not an allowed value.")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError as exc:
        raise MalformedArguments("Argument 'date_range' is not an allowed value.") from exc
    if end < start or (end - start).days > 366:
        raise MalformedArguments("Argument 'date_range' is out of range.")
    return start, end


def _industry(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().casefold()
    if not _INDUSTRY.fullmatch(text):
        raise MalformedArguments("Argument 'industry' is not an allowed value.")
    return text


def _region(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.strip().upper().split())
    text = _REGION_ALIASES.get(text, text)
    if not _REGION.fullmatch(text):
        raise MalformedArguments("Argument 'region' is not an allowed value.")
    return text


def _festival(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise MalformedArguments("Argument 'festival' is too short.")
    return text


def _content_type(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().upper()
    if not _CONTENT_TYPE.fullmatch(text):
        raise MalformedArguments("Argument 'content_type' is not an allowed value.")
    return text
