# Festival MCP

Related: [Festival automation](../AUTOMATION/FESTIVAL_AUTOMATION.md), [MCP architecture](MCP_ARCHITECTURE.md).

Two implementations share tool names. Neither publishes.

## Allowlisted server

Module: `backend/mcp/servers/festival.py`. Server string: `festival`. Registered by `build_registry`.

Tenant campaigns are loaded only for `TenantContext.tenant_id`.

### `get_upcoming_festivals`

Purpose: festivals coming up, with that tenant's campaigns only.

Input: optional `within_days` integer 1–366. Default 60.

Output: `festivals`, `as_of`, `tenant_id`.

Data: `FestivalService` catalog (`festivals/india_festivals.py`) plus `festival_campaigns`.

### `get_festival_details`

Purpose: one festival. A `campaign_id` owned by another tenant is not returned.

Input: optional `festival_name` 1–200, optional `campaign_id` 1–64.

Output: `found`, `festival`.

Error: `MALFORMED_ARGUMENTS`.

## Scheduler adapter (not on the allowlist)

Modules: `festivals/mcp.py`, `festivals/mcp_tools.py`. Class: `FestivalMcp` / `FestivalToolServer`.

`scheduler/integrations.resolve_festival_mcp` loads `festivals.mcp.get_festival_mcp`.

Tools:

| Tool | On allowlist? |
| --- | --- |
| `get_upcoming_festivals` | Name overlaps. Different schema and handler |
| `get_festival_details` | Name overlaps |
| `get_regional_festivals` | No |
| `get_festival_campaign` | No |
| `get_business_relevance` | No |

Errors use `ok: false` with `error` such as `unknown_tool`, `unknown_festival`, `date_unavailable`, `date_not_allowed`, `invalid_argument`. Dates come from the in-memory catalog. The adapter does not accept a model-invented date as a festival date.

## Tests

`tests/test_festival_intelligence.py`, festival cases inside `tests/test_scheduler.py`.
