# Security

Related: [Secrets](SECRETS.md), [Tenant isolation](TENANT_ISOLATION.md), [Authentication](../API/AUTHENTICATION.md), [MCP architecture](../MCP/MCP_ARCHITECTURE.md).

## Authentication

JWT HS256 (`auth/tokens.py`), cookie `access_token` or Bearer, server-side `sessions` row. Passwords are bcrypt. Details: [Authentication](../API/AUTHENTICATION.md).

## Encryption

`db/crypto.py` `TokenEncryptor` uses Fernet and `TOKEN_ENCRYPTION_KEY`. It wraps Instagram access tokens, Canva access and refresh tokens, and Canva PKCE verifiers. Without the key, connect routes cannot store a token.

## Logging

`services/logging.py` replaces secret-like substrings with `[REDACTED]` via `redact_text`. `AppError` messages pass through that redaction in the HTTP handler. Optional integration failures in `scheduler/integrations.py` log the module name and not the exception text, so a provider key in an error string is less likely to be written.

## Publishing boundary

MCP, DeepSeek, OpenAI, and Canva cannot call `media_publish`. The allowlist check runs before any handler. The Instagram Agent is the only registered publisher. See [Architecture](../ARCHITECTURE.md).

## HTTP

CORS origins come from `CORS_ORIGINS` and allow credentials. Graph hosts are allowlisted. `allow_insecure_graph_http` defaults to false.

Public media URLs are unauthenticated by design. They are not a directory listing. Generated and brand bytes require a session.

## Gaps

| Item | Status |
| --- | --- |
| Global `META_ACCESS_TOKEN` fallback | PARTIAL. One process token can publish when a user has no connected account |
| `JWT_SECRET` default `dev-change-me` when unset | PARTIAL. Replace before any shared deployment |
| Cookie `Secure` default false | Expected for local HTTP. Set `JWT_COOKIE_SECURE=true` behind HTTPS |
| Instagram OAuth | NOT IMPLEMENTED. The user pastes a token |
