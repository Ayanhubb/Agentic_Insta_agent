"""Live MCP reads of authorized Instagram account intelligence.

Tenant identity comes from TenantContext. Model arguments cannot select an
account. These tools do not publish and they do not return access tokens or
raw Graph payloads.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.mcp.errors import MCPError
from backend.mcp.registry import MCPTool, object_schema
from backend.mcp.sources import RepositoryGateway
from backend.mcp.tenant_isolation import TenantContext
from config import Settings, get_settings
from db.exceptions import TokenEncryptionError
from models.errors import AppError
from services.instagram_intelligence import AccountIntelligenceService

_LIMIT = {"limit": {"type": "integer", "minimum": 1, "maximum": 25}}


def account_intelligence_tools(
    gateway: RepositoryGateway,
    *,
    settings: Settings | None = None,
    reader: Any | None = None,
) -> list[MCPTool]:
    async def _run(tenant: TenantContext, method: str, **kwargs: Any) -> dict[str, Any]:
        active_settings = settings or get_settings()
        session = gateway._session_factory()
        try:
            service = AccountIntelligenceService(active_settings, session, reader=reader)
            payload = await getattr(service, method)(tenant.tenant_id, **kwargs)
            session.commit()
            context = payload.get("trend_context") or _insights_context(payload)
            if method == "posts" and isinstance(context, dict):
                context.pop("username", None)
            return {
                "found": True,
                "source": "instagram_account_intelligence",
                "trend_context": context,
            }
        except AppError as exc:
            session.rollback()
            return _unavailable(method, exc.code.value)
        except TokenEncryptionError:
            session.rollback()
            return _unavailable(method, "AUTHENTICATION_ERROR")
        except httpx.TimeoutException:
            session.rollback()
            return _unavailable(method, "TIMEOUT")
        except httpx.HTTPError:
            session.rollback()
            return _unavailable(method, "API_ERROR")
        except MCPError:
            session.rollback()
            raise
        finally:
            session.close()

    async def get_account_summary(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        return await _run(tenant, "account")

    async def get_recent_media(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        return await _run(tenant, "posts", limit=int(arguments.get("limit", 25)))

    async def get_account_insights(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        return await _run(tenant, "insights")

    async def get_top_content(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        return await _run(tenant, "top_content")

    async def get_content_performance(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        return await _run(tenant, "account")

    async def get_publishing_history(tenant: TenantContext, arguments: dict[str, Any]) -> dict[str, Any]:
        history = gateway.publishing_history(tenant.tenant_id, limit=int(arguments.get("limit", 10)))
        if history is None:
            return _unavailable("publishing_history", "INSTAGRAM_NOT_CONNECTED")
        return {
            "found": bool(history["posts"]),
            "source": "instagram_posts",
            "live_graph": False,
            "account_status": history["account_status"],
            "instagram_account_id": history["instagram_account_id"],
            "truncated": history["truncated"],
            "posts": history["posts"],
        }

    specs = (
        (
            "get_account_summary",
            "Normalized performance for the authenticated tenant's connected Instagram account.",
            object_schema(),
            get_account_summary,
        ),
        (
            "get_recent_media",
            "Normalized recent-media performance for the authenticated tenant. Raw captions are omitted.",
            object_schema(_LIMIT),
            get_recent_media,
        ),
        (
            "get_account_insights",
            "Authorized insight metrics for the authenticated tenant, including metrics Meta did not provide.",
            object_schema(),
            get_account_insights,
        ),
        (
            "get_top_content",
            "Highest and lowest engagement in the authenticated tenant's returned sample.",
            object_schema(),
            get_top_content,
        ),
        (
            "get_content_performance",
            "Sample comparisons for the authenticated tenant. This tool does not publish.",
            object_schema(),
            get_content_performance,
        ),
        (
            "get_publishing_history",
            "Stored publication records for the authenticated tenant. This tool does not publish.",
            object_schema(_LIMIT),
            get_publishing_history,
        ),
    )
    return [
        MCPTool(
            name=name,
            description=description,
            input_schema=schema,
            server="account",
            handler=handler,
        )
        for name, description, schema, handler in specs
    ]


def insights_for_analyst(trend_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Metric readings safe to place in a DeepSeek trend packet.

    Unavailable metrics are omitted. Captions, biography, and tokens are not fields here.
    """
    sample = trend_context.get("sample_size")
    readings: list[dict[str, Any]] = []
    for item in trend_context.get("metrics") or []:
        if not isinstance(item, dict) or item.get("status") != "available":
            continue
        metric = str(item.get("metric") or "").strip()
        value = item.get("value")
        if not metric or value is None or isinstance(value, bool):
            continue
        if not isinstance(value, (int, float, str)):
            continue
        row: dict[str, Any] = {
            "id": f"ig:{metric}",
            "metric": metric,
            "value": value,
            "statement": f"The authorized Instagram sample recorded {metric} as {value}.",
        }
        if isinstance(sample, int):
            row["sample_size"] = sample
        readings.append(row)
    return readings


def _unavailable(method: str, reason: str) -> dict[str, Any]:
    return {
        "found": False,
        "source": "instagram_account_intelligence",
        "metric": method,
        "status": "unavailable",
        "reason": reason,
    }


def _insights_context(payload: dict[str, Any]) -> dict[str, Any]:
    insights = payload.get("insights") or []
    return {
        "source": "instagram_account_intelligence",
        "captured_at": payload.get("captured_at"),
        "metrics": [
            {
                "metric": item.get("metric"),
                "status": item.get("status"),
                "value": item.get("value"),
                "reason": item.get("reason"),
            }
            for item in insights
            if isinstance(item, dict)
        ],
    }
