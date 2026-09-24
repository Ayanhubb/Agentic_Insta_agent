# MCP

In-process MCP lives in `backend/mcp/`. It is an allowlisted tool registry, not a separate network server. The optional Canva integration is a remote MCP at `https://mcp.canva.com/mcp` and is not on this allowlist.

Related: [MCP architecture](MCP/MCP_ARCHITECTURE.md), [Trend](MCP/TREND_MCP.md), [Instagram account tools](MCP/INSTAGRAM_MCP.md), [Product](MCP/PRODUCT_MCP.md), [Brand and assets](MCP/BRAND_ASSET_MCP.md), [Festival](MCP/FESTIVAL_MCP.md), [Canva](MCP/CANVA_MCP.md).

## Publishing boundary

```text
MCP        = no direct Instagram publishing
DeepSeek   = no direct Instagram publishing
OpenAI     = no direct Instagram publishing
Canva      = no direct Instagram publishing
Instagram Agent = only publishing authority
```

`assert_tool_allowed` in `backend/mcp/permissions.py` rejects `publish`, `media_publish`, `instagram_publish`, `post_to_instagram`, `create_media`, `publish_media`, and the six Instagram Agent tool names with `ToolNotAllowed`: "Instagram publishing tools are not available through MCP."

## Allowlist (22 tools)

Registered by `build_registry` in `backend/mcp/servers/__init__.py`:

| Server | Tools |
| --- | --- |
| `business` | `get_business_profile` |
| `brand` | `get_brand_guidelines` |
| `asset` | `get_company_logo` |
| `product` | `get_product`, `get_product_image`, `get_active_offers` |
| `festival` | `get_upcoming_festivals`, `get_festival_details` |
| `content` | `get_content_rules` |
| `account` | `get_account_summary`, `get_recent_media`, `get_account_insights`, `get_top_content`, `get_content_performance`, `get_publishing_history` |
| `trend` | `get_current_trends`, `get_trend_evidence`, `get_regional_trends`, `get_festival_opportunities`, `get_business_opportunities`, `get_content_opportunities`, `get_latest_trend_report` |

All of these are read-only. Tenant id comes from `TenantContext`, not from model arguments (`backend/mcp/tenant_isolation.py`).

## Second festival tool set

`festivals/mcp.py` and `festivals/mcp_tools.py` are a scheduler adapter (`FestivalMcp`). They are not registered on `MCP_TOOL_ALLOWLIST`. Extra tools there: `get_regional_festivals`, `get_festival_campaign`, `get_business_relevance`. They also do not publish.

## Status route

`GET /api/v1/mcp/status` (authenticated) returns whether MCP is enabled, whether Canva is enabled and connected for the user, and the discovered tool list.
