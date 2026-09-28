# Platform security

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Security](SECURITY/SECURITY.md), [Secrets](SECURITY/SECRETS.md), [Tenant isolation](SECURITY/TENANT_ISOLATION.md), [Authentication](API/AUTHENTICATION.md), [MCP](AGENT_PLATFORM_MCP.md).

The platform adds no new trust boundary. The control plane remains the only place that authenticates users, decrypts tokens, and allows a side effect. Models, MCP handlers, Canva, the scheduler, and React stay unable to publish to Meta.

## Authentication

**IMPLEMENTED.**

- JWT HS256 (`auth/tokens.py`), cookie `access_token` or `Authorization: Bearer`.
- Server-side `sessions` row. The `jti` must be unexpired and not revoked. Logout revokes the row.
- Passwords are bcrypt. Cost `BCRYPT_ROUNDS` default 12, clamped 4–16. Minimum length 8.
- Expiry `JWT_EXPIRE_MINUTES` default 480. `JWT_COOKIE_SECURE` default false (local HTTP).
- Dependencies: `get_current_user`, `require_active_user`, `require_password_ok`, `require_admin`.
- Bootstrap admin when `DEFAULT_ADMIN_EMAIL` and `DEFAULT_ADMIN_PASSWORD` are set. `must_change_password` blocks `require_password_ok` until `POST /api/v1/auth/change-password`.
- `GET /api/v1/health` is public. `GET /api/v1/media/{filename}` is public so Meta can fetch a prepared image. Generated images and brand files are not on that path.
- CORS origins come from `CORS_ORIGINS` and allow credentials.

**PROPOSED.** Agent configuration routes, when they exist, use `require_password_ok` and the session user as owner. Admin listing stays `require_admin`. The platform does not add an API key auth scheme. None exists in the tree.

Known gaps that stay gaps until someone changes them on purpose:

| Item | Status |
| --- | --- |
| `JWT_SECRET` default `dev-change-me` when unset | **PARTIAL.** Replace before any shared deployment |
| Cookie `Secure` default false | Expected for local HTTP. Set `JWT_COOKIE_SECURE=true` behind HTTPS |
| Instagram OAuth | **NOT IMPLEMENTED.** The user pastes a token on `POST /api/v1/instagram/connect` |

## Tenant isolation

**IMPLEMENTED.** Tenant id is `users.id`. Routes do not trust a user id in the JSON body. Repository reads use `get_owned` or `list_for_user`. A miss is 404, including another user's task or image.

MCP builds `TenantContext` with source `authenticated`, `scheduler`, or `system`. Model-supplied tenant keys are discarded and must not be used as the tenant id.

Asset tools raise `ASSET_ACCESS_DENIED` when the id exists for a different user. Festival detail omits another tenant's `campaign_id`. Trend evidence requires the observation's `user_id` to match. File helpers `resolve_owned_media` check the owner before opening a path.

Publish resolves `instagram_accounts` for the authenticated user only. A requested account id owned by someone else returns `PERMISSION_ERROR` and does not read that token.

Uniqueness the studio relies on:

- Daily posts: `(user_id, instagram_account_id, scheduled_date)` for published `DAILY_RETAIL_POST` rows.
- Festival campaigns: `(user_id, festival_name, year)`.

**PROPOSED.** Every new agent table includes `user_id` and is queried through the same repository pattern. System agent definitions have no `user_id`. They are process catalog entries, not a tenant's private copy of the Instagram publisher. User-owned configuration rows, when added, are readable only by that user.

There is no organization or team model. Do not add one in the platform migration.

## Secrets

**IMPLEMENTED** placement:

| Secret | Where | Sent to the model or browser? |
| --- | --- | --- |
| `OPENAI_API_KEY` | Environment. Not a column | No |
| `DEEPSEEK_API_KEY` | Environment. Not a column | No |
| `JWT_SECRET` | Environment | No |
| `TOKEN_ENCRYPTION_KEY` | Environment | No |
| `CANVA_CLIENT_SECRET` | Environment | No |
| Canva user tokens | `canva_connections`, Fernet | No |
| Instagram user token | `instagram_accounts.access_token_encrypted`, Fernet | No. Connect accepts it once. Responses use `strip_secrets` |
| `META_ACCESS_TOKEN` | Environment, development-only fallback | No. Not a production publisher |
| `DEFAULT_ADMIN_PASSWORD` | Environment, stored only as bcrypt | No |

`TokenEncryptor` in `db/crypto.py` also wraps Canva PKCE verifiers. Without `TOKEN_ENCRYPTION_KEY`, connect routes cannot store a token.

`GET /api/v1/ai/status` returns booleans such as `openai_configured`. `Settings.public_canva_status` returns host and flags only.

`redact_text` is applied to API error messages. MCP unknown-tool errors redact the attempted name before it is interpolated.

Changing `TOKEN_ENCRYPTION_KEY` makes existing Instagram and Canva ciphertext unreadable. Users must reconnect. Changing `JWT_SECRET` invalidates signatures. Session rows can still be revoked.

**PROPOSED.** Agent definitions, workflow definitions, prompts logged by the dispatcher, and MCP results follow the same table. A new column must not store a provider key or a Graph token in plaintext. Provider keys stay in the environment.

The model prompt builders must keep excluding token fields. `creative_validation` already rejects credential-shaped keys on a creative plan (`token`, `access_token`, and the other forbidden keys in that module). New output schemas need the same rejection.

## Approval and policy

**IMPLEMENTED** policy `decide_approval` in `scheduler/approval_policy.py`. This is the reference policy. Proposed id: `instagram.still_image`.

Human lanes (auto flag off) stay `PENDING_APPROVAL` with reason `human_approval_required` plus any gate failures. `publish` is false.

Automatic lanes require `auto_daily_publish` or `auto_festival_publish`. They still refuse publish when any of these reasons is set:

- `image_qa_failed`
- `provider_result_malformed`
- `product_reference_missing`
- `offer_cannot_be_verified`
- `required_logo_missing`
- `festival_context_invalid`
- `tenant_ownership_failed`

If any gate fails in automatic mode, status stays `PENDING_APPROVAL`, `publish` is false, and `retryable` is true.

Studio generation does not use this function to publish. It stores `PENDING_APPROVAL` and waits for `POST /generation/{image_id}/approve`, which calls `PublicationService`. Reject and regenerate do not publish.

`PublicationService.publish_generated_image` runs only when `ApprovalDecision.publish` is true, on the scheduler path. That service enqueues the Instagram Agent.

**PROPOSED** policy engine:

- A policy is a named function registered in code, same as `decide_approval`.
- An agent binds one policy id.
- The engine cannot be given a policy document that sets `publish=true` while skipping `tenant_ownership_failed`.
- The engine cannot publish by itself. `publish=true` means "call `enqueue_instagram_publication`".
- User configuration may toggle the existing auto flags. It may not delete a gate.

Direct `POST /api/v1/instagram/publish` is an authenticated upload, not the scheduler policy. It still goes through `InstagramAgent` and still uses the caller's connected account. It is a trigger of the reference agent, not a bypass for other agents to copy.

## Publishing invariant

**IMPLEMENTED.** Preserved verbatim by the platform:

```text
MCP        = no direct Instagram publishing
DeepSeek   = no direct Instagram publishing
OpenAI     = no direct Instagram publishing
Canva      = no direct Instagram publishing
Content orchestrator and opportunity engine = published false
Festival catalog and festival MCP = no publishing
Instagram Agent = only publishing authority
```

`enqueue_instagram_publication` does not call Graph. `LLMPlanner` does not run. Formats other than a still image are not published.

`legacy_environment_credentials_allowed()` is false unless `INSTAGRAM_LEGACY_ENV_FALLBACK` is true and `APP_ENV` is `development`, `dev`, or `local`. `production`, `prod`, `staging`, and unset `APP_ENV` never enable it. The scheduler does not opt in.

## Control plane duties

**PROPOSED** list of duties the backend already performs and must keep when a dispatcher exists:

1. Authenticate the caller.
2. Build `TenantContext` from the session or the scheduler's user id.
3. Load and decrypt secrets inside the component that calls the vendor or Graph.
4. Validate model output before a side effect.
5. Apply the approval policy.
6. Call the Instagram Agent only through the publication gateway.
7. Redact logs and HTTP errors.
8. Return 404 for foreign ids.

A workflow handler that needs a new external API follows the same list. It does not pass the HTTP body through to that API.

## Tests

**IMPLEMENTED** coverage the platform work must not weaken:

- `tests/test_auth.py`
- `tests/test_database.py` (cross-user reads)
- `tests/test_mcp.py`
- `tests/test_approval_policy.py`
- `tests/test_instagram_credential_isolation.py`
- `tests/test_instagram_agent_integration.py`
- `tests/test_generation_api.py`
- `tests/test_integration_wiring.py`

Frontend login and protected-route tests cover the session cookie path.
