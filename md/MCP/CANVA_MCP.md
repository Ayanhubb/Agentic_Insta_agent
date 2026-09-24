# Canva MCP

Related: [MCP architecture](MCP_ARCHITECTURE.md), [Content generation](../CONTENT/CONTENT_GENERATION.md), [Secrets](../SECURITY/SECRETS.md).

Canva is optional. `CANVA_ENABLED` defaults to false. DeepSeek and OpenAI image generation do not require it. Canva cannot publish to Instagram.

## Connection

Remote MCP: `CANVA_MCP_URL` default `https://mcp.canva.com/mcp`.

OAuth endpoints default to `https://mcp.canva.com/authorize` and `https://mcp.canva.com/token`.

App routes (`api/canva_routes.py`, prefix `/api/v1/integrations/canva`):

| Method | Path | Auth | Result |
| --- | --- | --- | --- |
| GET | `/status` | password ok | Public connection dict for this user. No tokens |
| POST | `/connect` | password ok | `{authorization_url}` |
| GET | `/callback` | password ok | Query `code`, `state`. Redirect 303 to the success URL or a short HTML page |
| POST | `/disconnect` | password ok | `{connected: false}` |

`canva_configured` is true only when enabled, `CANVA_CLIENT_ID` and `CANVA_REDIRECT_URI` are set, and either the client id is an `https://` CIMD URL or `CANVA_CLIENT_SECRET` is set.

Tokens are Fernet-encrypted in `canva_connections`. PKCE verifiers sit in `canva_oauth_states`. `TOKEN_ENCRYPTION_KEY` is required before a user can connect.

`create_app` passes a Canva client into generation only when `canva_enabled` is true. Otherwise the orchestrator uses `DisabledCanva`, which returns `action: none`.

## Remote tool names

`backend/integrations/canva/catalog.py` lists candidate names used after discovery:

`create-design`, `generate-design`, `create-design-from-candidate`, `start-editing-transaction`, `perform-editing-operations`, `commit-editing-transaction`, `cancel-editing-transaction`, `search-designs`, `export-design`, `get-export-formats`, `list-brand-kits`, `search-brand-templates`, `get-assets`.

Mapped capabilities: `create_design`, `edit_design`, `search_designs`, `get_brand_assets`, `export_design`.

There is no publish capability in that map.

## What the adapter will not do

`CanvaAdapter.apply` returns `applied: false` with reason `explicit_capability_required` (or `disabled`). It does not invent a tool call and it does not call Meta.

Design generation, editing, and export exist only as discovered remote tools behind a connected account. If Canva is disabled or the user has not completed OAuth, those actions are not performed.

| Action | Status when Canva is off or disconnected |
| --- | --- |
| Instagram publish from Canva | NOT IMPLEMENTED (no such tool) |
| Automatic design apply during generation | Not applied. `applied: false` |
| Template fill without a connected account | NOT IMPLEMENTED |

## Environment

`CANVA_ENABLED`, `CANVA_MCP_URL`, `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI`, `CANVA_TIMEOUT_SECONDS` (default 60), `CANVA_OAUTH_SCOPES`, `CANVA_OAUTH_SUCCESS_URL`, `CANVA_AUTHORIZE_URL`, `CANVA_TOKEN_URL`, `CANVA_ALLOW_UNOFFICIAL_ENDPOINT` (default false).

Do not put these values in the React app.

## Tests

`tests/test_canva_mcp.py` uses a mocked remote client.
