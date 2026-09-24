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

`create_app` passes a Canva client into generation only when `canva_enabled` is true. Startup does not open a Canva session. Otherwise the orchestrator uses `DisabledCanva`.

When a request asks for Canva and the account is not connected, the workflow returns `CANVA_NOT_CONNECTED` and does not generate an image. An expired token returns `CANVA_AUTHORIZATION_FAILED`. Neither path publishes, and neither path is a process crash.

## Remote tool names

`backend/integrations/canva/catalog.py` lists candidate names used after discovery:

`create-design`, `generate-design`, `create-design-from-candidate`, `start-editing-transaction`, `perform-editing-operations`, `commit-editing-transaction`, `cancel-editing-transaction`, `search-designs`, `export-design`, `get-export-formats`, `list-brand-kits`, `search-brand-templates`, `get-assets`.

Mapped capabilities: `create_design`, `edit_design`, `search_designs`, `get_brand_assets`, `export_design`.

There is no publish capability in that map.

## Content workflow

The creative plan chooses the renderer.

| `canva_action` | Renderer |
| --- | --- |
| `none` | OpenAI image generation |
| `apply_template` or `use_reference` | `CanvaAdapter.produce` on this user's connection |

`produce` lists only that account's templates and assets, creates a design from the approved brief, and exports a PNG. The bytes then use the same vision QA and `PENDING_APPROVAL` path as an OpenAI image. Canva does not receive Meta permissions and does not call `media_publish`.

There is no logo upload. If the connected account has no brand template or asset yet, the plan stays on `none` and OpenAI generates the image. Later uploads are picked up by the same asset lookup.

`CanvaAdapter.apply` is only the scheduler observation hook. It returns `applied: false`. It does not create a second design and it does not call Meta.

The access token stays inside the adapter session. `query` and `produce` results do not include it, and the export download does not send it.

| Action | Status when Canva is off or disconnected |
| --- | --- |
| Instagram publish from Canva | Not available. Publish tool names are refused |
| Design export during generation | `CANVA_NOT_CONNECTED` or `CANVA_DISABLED` |
| Template fill without a connected account | Not performed |

## Environment

`CANVA_ENABLED`, `CANVA_MCP_URL`, `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI`, `CANVA_TIMEOUT_SECONDS` (default 60), `CANVA_OAUTH_SCOPES`, `CANVA_OAUTH_SUCCESS_URL`, `CANVA_AUTHORIZE_URL`, `CANVA_TOKEN_URL`, `CANVA_ALLOW_UNOFFICIAL_ENDPOINT` (default false).

Do not put these values in the React app.

## Tests

`tests/test_canva_mcp.py` and `tests/test_canva_execution.py` use a mocked remote client. They cover a connected account, a missing connection, an invalid token, template lookup, asset retrieval, design export, tenant isolation, QA, approval, and the refusal to publish.
