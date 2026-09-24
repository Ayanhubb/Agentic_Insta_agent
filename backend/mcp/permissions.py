"""Tool allowlist. Instagram publishing tools cannot be registered or invoked."""

from __future__ import annotations

from agent.planner import ALLOWED_TOOL_SET as INSTAGRAM_AGENT_TOOLS
from backend.mcp.errors import ToolNotAllowed, UnknownTool
from services.logging import redact_text

MCP_TOOL_ALLOWLIST = frozenset(
    {
        "get_business_profile",
        "get_brand_guidelines",
        "get_company_logo",
        "get_product",
        "get_product_image",
        "get_active_offers",
        "get_upcoming_festivals",
        "get_festival_details",
        "get_content_rules",
        "get_account_summary",
        "get_recent_media",
        "get_account_insights",
        "get_top_content",
        "get_content_performance",
        "get_publishing_history",
        "get_current_trends",
        "get_trend_evidence",
        "get_regional_trends",
        "get_festival_opportunities",
        "get_business_opportunities",
        "get_content_opportunities",
        "get_latest_trend_report",
    }
)

# Aliases a model might invent. The real publishing pipeline is INSTAGRAM_AGENT_TOOLS.
_PUBLISH_ALIASES = frozenset(
    {
        "media_publish",
        "instagram_publish",
        "publish",
        "post_to_instagram",
        "create_media",
        "publish_media",
    }
)

INSTAGRAM_PUBLISHING_TOOLS = frozenset(INSTAGRAM_AGENT_TOOLS) | _PUBLISH_ALIASES


def assert_tool_allowed(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise UnknownTool("Unknown tool.")
    tool_name = name.strip()
    if tool_name in INSTAGRAM_PUBLISHING_TOOLS:
        raise ToolNotAllowed("Instagram publishing tools are not available through MCP.")
    if tool_name not in MCP_TOOL_ALLOWLIST:
        safe_name = redact_text(tool_name)[:80]
        raise UnknownTool(f"Unknown tool '{safe_name}'.")
    return tool_name
