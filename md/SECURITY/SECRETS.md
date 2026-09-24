# Secrets

Related: [Security](SECURITY.md). No secret values belong in this file.

## Where secrets live

| Secret | Environment | Stored in the database? | Sent to the browser? |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | yes. **IMPLEMENTED — NOT CONFIGURED** (empty). Image generation and editing only | no | no |
| `DEEPSEEK_API_KEY` | yes. **IMPLEMENTED — NOT CONFIGURED** (empty). Reasoning, trend analysis, and content planning only | no | no |
| `JWT_SECRET` (`SECRET_KEY` alias) | yes | no | no. Signs tokens |
| `TOKEN_ENCRYPTION_KEY` (`ACCOUNT_TOKEN_FERNET_KEY` alias) | yes | no | no |
| `META_ACCESS_TOKEN` (`INSTAGRAM_ACCESS_TOKEN` alias) | yes, development-only. Production publishing ignores it. Also set `INSTAGRAM_ACCOUNT_ID`. Requires `APP_ENV=development` (or `dev` / `local`) and `INSTAGRAM_LEGACY_ENV_FALLBACK=true` | no. Per-user tokens are encrypted columns and are the production credential | no |
| `CANVA_CLIENT_SECRET` | yes | no | no |
| Canva user tokens | no | `canva_connections`, encrypted | no |
| Instagram user token | posted once to connect | `instagram_accounts.access_token_encrypted` | stripped from responses |
| `DEFAULT_ADMIN_PASSWORD` | yes | only as a bcrypt hash | no |

`GET /api/v1/ai/status` returns booleans such as `openai_configured`. It does not return keys. `Settings.public_canva_status` returns host and flags only.

## Rules already in `.env.example`

Do not commit `.env`. Do not prefix provider keys with `VITE_` or `NEXT_PUBLIC_`. Do not put Canva client credentials in React.

## Redaction

`redact_text` in `services/logging.py` is applied to API error messages. MCP unknown-tool errors redact the attempted name before it is interpolated.

## Rotation

Changing `TOKEN_ENCRYPTION_KEY` makes existing Instagram and Canva ciphertext unreadable. Users must reconnect. Changing `JWT_SECRET` invalidates signatures; existing session rows can still be revoked explicitly.
