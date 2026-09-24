# Canva MCP

Canva is an optional design capability. The FastAPI backend talks to Canva's official remote MCP server. This repository does not ship a Canva MCP server.

When Canva is disabled, disconnected, or timing out, DeepSeek planning and OpenAI image generation continue to work. Do not set `IMAGE_PROVIDER=canva`.

## Official server

| Purpose | URL |
| --- | --- |
| MCP endpoint | `https://mcp.canva.com/mcp` |
| Authorization | `https://mcp.canva.com/authorize` |
| Token | `https://mcp.canva.com/token` |
| Dynamic client registration | `https://mcp.canva.com/register` |

Transport is Streamable HTTP. Each user authorizes their own Canva account. Canva does not offer an organization-level service account. Documentation: [Canva MCP](https://www.canva.dev/docs/mcp/).

The adapter calls `tools/list` on that server and only invokes tool names present in the response. Capability methods exist only when the corresponding discovered tools are present:

| Method | Discovered tools required |
| --- | --- |
| `create_design()` | `create-design`, or both `generate-design` and `create-design-from-candidate` |
| `edit_design()` | `start-editing-transaction`, `perform-editing-operations`, `commit-editing-transaction` |
| `search_designs()` | `search-designs` |
| `get_brand_assets()` | `list-brand-kits`, `search-brand-templates`, or `get-assets` |
| `export_design()` | `export-design` |

`generate-design` returns candidates. The adapter does not pick one. Saving a design requires the user's `candidate_id` and job id, then `create-design-from-candidate` when that tool was discovered. Signed export URLs expire. They are not stored.

## Setup

1. Create an app in the [Canva Developer Portal](https://www.canva.dev/docs/mcp/) and turn on Canva MCP, or register a client against `https://mcp.canva.com/register`.
2. Set the redirect URI to the backend callback, for local development:

   `http://127.0.0.1:8000/api/v1/integrations/canva/callback`

3. Put the client id and client secret in the backend environment only. A CIMD client id is an `https://` URL and does not use a client secret.
4. Set `TOKEN_ENCRYPTION_KEY`. Canva access and refresh tokens are encrypted with the same Fernet key as Instagram tokens. Plaintext tokens are not stored.
5. Enable the integration:

```bash
CANVA_ENABLED=true
CANVA_CLIENT_ID=
CANVA_CLIENT_SECRET=
CANVA_REDIRECT_URI=http://127.0.0.1:8000/api/v1/integrations/canva/callback
CANVA_TIMEOUT_SECONDS=60
```

`CANVA_MCP_URL` defaults to `https://mcp.canva.com/mcp`. Other hosts are rejected unless `CANVA_ALLOW_UNOFFICIAL_ENDPOINT=true`.

Leave `CANVA_ENABLED` unset to keep Canva off. The React app must not receive `CANVA_CLIENT_SECRET`, access tokens, or refresh tokens. There is no `VITE_CANVA_*` variable.

## Per-user connection

Authenticated API routes:

| Method | Path | Response |
| --- | --- | --- |
| `GET` | `/api/v1/integrations/canva/status` | `enabled`, `configured`, `connected`, `capabilities`, `tools` |
| `POST` | `/api/v1/integrations/canva/connect` | `authorization_url` only |
| `GET` | `/api/v1/integrations/canva/callback` | HTML or a redirect with `canva=connected` |
| `POST` | `/api/v1/integrations/canva/disconnect` | `connected: false` |

The authorization URL uses PKCE (`S256`). The code verifier stays encrypted in `canva_oauth_states` until the signed-in user completes the callback. A callback from a different user is rejected and does not exchange the code.

Connections are stored in `canva_connections` and loaded only for the matching tenant id and user id. Until a separate organization table exists, both ids are the authenticated user id. Request bodies cannot choose a tenant.

OAuth state and tokens are not written to logs. Log lines record the tool name, status, duration, and user id.

## Timeouts and authorization failures

Each MCP and token request uses `CANVA_TIMEOUT_SECONDS` (default 60) and then raises `CANVA_TIMEOUT`. The call is not retried.

HTTP 401 or an MCP unauthorized error raises `CANVA_AUTHORIZATION_FAILED`. An access token within 60 seconds of expiry is refreshed once with the stored refresh token. A failed refresh marks the connection `authorization_failed`. The user connects again from the API.

Canva does not publish to Instagram. The Instagram Agent remains the only Meta publisher.
