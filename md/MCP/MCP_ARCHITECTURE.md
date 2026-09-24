# MCP architecture

Related: [MCP overview](../MCP.md), [Tenant isolation](../SECURITY/TENANT_ISOLATION.md), [Canva](CANVA_MCP.md).

## Process

`MCPClient.invoke` (`backend/mcp/client.py`):

1. `require_trusted_tenant` — the caller must pass a server-side `TenantContext`.
2. `strip_model_tenant_arguments` — a model-supplied tenant id is removed.
3. `registry.get` — name must be on `MCP_TOOL_ALLOWLIST`.
4. `validate_arguments` — object schema, required keys, types, string lengths, integer ranges, enums. `additionalProperties` is false.
5. `asyncio.wait_for(handler)` — default timeout 5 seconds.
6. `sanitize_payload` — then the client injects `tenant_id`.

## Errors (`backend/mcp/errors.py`)

| Code | When |
| --- | --- |
| `TENANT_CONTEXT_REQUIRED` | No trusted tenant |
| `UNKNOWN_TOOL` | Name not on the allowlist |
| `TOOL_NOT_ALLOWED` | Instagram publish name or agent tool name |
| `MALFORMED_ARGUMENTS` | Schema failure |
| `TIMEOUT` | Handler exceeded the client timeout |
| `ASSET_ACCESS_DENIED` | Asset id is not owned by the tenant |
| `INTERNAL_ERROR` / `MCP_ERROR` | Unexpected handler failure |

Successful payloads always include `tenant_id`.

## Discovery

`MCPRegistry.discover` returns `name`, `description`, `server`, and `input_schema`, sorted by name. `GET /api/v1/mcp/status` exposes that list to the signed-in user.

## Read/write

Every allowlisted handler reads repositories or Graph. None insert posts, none call `PublicationService`, and none accept a publish flag. `get_content_rules` returns `publishing.available: false` and `user_prompt_auto_publish: false`.

## Data plane

`RepositoryGateway` (`backend/mcp/sources.py`) opens a session and scopes queries with `tenant_id`. Festival rows come from `FestivalService` plus that tenant's `festival_campaigns`. Account Graph tools use `AccountIntelligenceService` with the tenant's stored token. Trend tools read stored observations; they do not fetch URLs.

## Tests

`tests/test_mcp.py` covers discovery and cross-tenant denial. `tests/test_mcp_account_intelligence.py` and `tests/test_trend_mcp.py` cover those servers.
