# Trend MCP

Related: [MCP architecture](MCP_ARCHITECTURE.md), [Trend research](../INTELLIGENCE/TREND_RESEARCH.md), [Content opportunities](../INTELLIGENCE/CONTENT_OPPORTUNITIES.md).

Server string: `trend`. Module: `backend/mcp/servers/trend.py`.

These tools read stored rows. They do not browse, do not call DeepSeek, and cannot publish.

Authentication is the trusted tenant on `MCPClient.invoke`. The model cannot pass `user_id`.

Shared filter object `_FILTERS` (all optional unless noted): `limit` integer 1–25, `date_range` string, `industry`, `region`, `festival`, `content_type`.

## Tools

### `get_current_trends`

Purpose: fresh normalized observations. Stale rows are excluded.

Input: `_FILTERS`. Required: none.

Output: `observations` (evidence stripped), `truncated`, `stale_excluded`, `as_of`, `source`, `limit`, `found`, plus `tenant_id`.

Data: `trend_observations`, with `trend_sources` and `trend_evidence` used internally.

Read only. Errors: `MALFORMED_ARGUMENTS` on a bad filter.

### `get_regional_trends`

Same output as `get_current_trends`. Required: `region`.

### `get_trend_evidence`

Input: `observation_id` string 1–64, required.

Output: `found`, `observation`, `source=trend_observations`.

Another tenant's id is not returned.

### `get_latest_trend_report`

Input: none.

Output: `found`, `stale`, `report`, `source=trend_reports`.

### `get_festival_opportunities`

Input: `_FILTERS`.

Output: `found`, `source=festival_service`, `limit`, `truncated`, `opportunities`.

Data: festival catalog plus this tenant's campaign flag only. Not a full foreign campaign row.

### `get_business_opportunities`

Input: optional `limit`.

Output: `found`, `source=products`, `limit`, `truncated`, `opportunities`.

Data: `business_profiles`, `brand_profiles`, `brand_guidelines`, `products`. These are **INFERRED** gaps (missing offer, missing brand fields), not live market evidence.

### `get_content_opportunities`

Input: `_FILTERS`.

Output: `found`, `source=instagram_posts`, `limit`, `truncated`, `opportunities`.

Data: aggregates of this tenant's `instagram_posts`. A content type with zero verified publishes in range is reported as a gap. That is **INFERRED** from stored posts, not from a new Graph call inside this tool.

## Tests

`tests/test_trend_mcp.py`.
