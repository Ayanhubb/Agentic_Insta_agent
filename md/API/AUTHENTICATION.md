# Authentication

Related: [Security](../SECURITY/SECURITY.md), [API reference](API_REFERENCE.md), [Tenant isolation](../SECURITY/TENANT_ISOLATION.md).

## Session

`auth/tokens.py` signs JWTs with HS256 and `JWT_SECRET`. Default expiry is `JWT_EXPIRE_MINUTES` (480). The cookie name is `access_token`. `JWT_COOKIE_SECURE` controls the Secure flag (default false).

`api/app.py` accepts the token from `Authorization: Bearer` or from that cookie. The `jti` must match a row in `sessions` that is unexpired and not revoked. Logout revokes the row.

Passwords are bcrypt (`auth/passwords.py`). Cost is `BCRYPT_ROUNDS`, default 12, clamped between 4 and 16. Minimum length is 8.

## Bootstrap admin

If `DEFAULT_ADMIN_EMAIL` and `DEFAULT_ADMIN_PASSWORD` are set, `auth/bootstrap.py` creates that user on startup when missing and sets `must_change_password`. The first login must call `POST /api/v1/auth/change-password` before `require_password_ok` routes will succeed.

`.env.example` ships a sample admin password. Replace it before any shared deployment. The application does not hard-code that password; it reads the environment.

## Who can call what

| Dependency | Rule |
| --- | --- |
| `get_current_user` | Valid session |
| `require_active_user` | `is_active` |
| `require_password_ok` | Active and not `must_change_password` |
| `require_admin` | Password ok and `is_admin` |

Registration and login are public. `GET /api/v1/health` is public. `GET /api/v1/media/{filename}` is public so Meta can fetch a prepared image. Generated images and brand files are not on that public path.

## Instagram tokens

`POST /api/v1/instagram/connect` stores the Graph token with Fernet (`TOKEN_ENCRYPTION_KEY`, with fallback env name `ACCOUNT_TOKEN_FERNET_KEY`). Responses pass through `strip_secrets`. Disconnect clears the connection for that user only.

Production publishing does not fall back to `META_ACCESS_TOKEN` or `INSTAGRAM_ACCOUNT_ID`. Those variables are a development-only path (`APP_ENV=development` and `INSTAGRAM_LEGACY_ENV_FALLBACK=true`). Without a connected account the API returns `INSTAGRAM_NOT_CONNECTED`.

## Tests

`tests/test_auth.py`, `frontend/src/test/login.test.tsx`, `frontend/src/test/protected-routes.test.tsx`.
