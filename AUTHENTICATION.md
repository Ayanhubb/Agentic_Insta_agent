# Authentication and authorization

This document describes the FastAPI authentication layer (`auth/`, `api/auth_routes.py`). OpenAI, React, and Instagram publishing are documented separately; every product route still goes through this layer so data stays scoped to `user_id`.

## Architecture

The product uses **bcrypt password hashes** plus **short-lived JWT access tokens** bound to **revocable server-side sessions**.

```text
React / API client
  → POST /api/v1/auth/login
    → verify bcrypt hash
    → insert sessions row
    → sign JWT { sub, email, jti, exp }
    → httpOnly cookie `access_token` and JSON { user, access_token }

Protected route
  → Bearer token or cookie
    → verify JWT signature + expiry
    → load auth_sessions.jti (must exist, not revoked, not expired)
    → load users row (must exist and is_active)
    → require_password_ok / require_admin
    → filter every resource by user_id
```

Logout deletes the cookie and sets `sessions.revoked_at`. A stolen JWT cannot be reused after logout.

## Endpoints

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/register` | public | Creates a non-admin user and starts a session |
| `POST` | `/api/v1/auth/login` | public | Safe errors only: "Invalid email or password." |
| `POST` | `/api/v1/auth/logout` | authenticated | Revokes the current session |
| `GET` | `/api/v1/auth/me` | authenticated | Allowed even when `must_change_password` is true |
| `POST` | `/api/v1/auth/change-password` | authenticated | Clears `must_change_password`; revokes other sessions |
| `GET` | `/api/v1/admin/users` | admin | Explicit backend admin gate |

Login / register response (password never included):

```json
{
  "user": {
    "id": "...",
    "email": "admin@yottolabs.com",
    "is_admin": true,
    "is_active": true,
    "must_change_password": true
  },
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

The JWT is also stored in an **httpOnly**, **SameSite=Lax** cookie named `access_token`. React may keep the optional `access_token` body field for `Authorization: Bearer`.

## Default admin

Bootstrap credentials come **only** from environment variables:

- `DEFAULT_ADMIN_EMAIL`
- `DEFAULT_ADMIN_PASSWORD`

They are not hard-coded in application logic, not logged, not returned by any API, and not present in frontend source.

On first application start, if both variables are set and no user with that email exists, an admin row is created with `must_change_password = true`. The first successful login also forces that flag. Every product route except `/auth/me`, `/auth/change-password`, and `/auth/logout` then returns `MUST_CHANGE_PASSWORD` (403) until the password is changed.

Documented local values (`.env.example` only):

```text
DEFAULT_ADMIN_EMAIL=admin@yottolabs.com
DEFAULT_ADMIN_PASSWORD=12345
```

The bootstrap password is shorter than the 8-character minimum required for registration and password changes. That is intentional: the default must be replaced immediately.

## Dependencies

| Dependency | File | Behavior |
| --- | --- | --- |
| `get_current_user` | `auth/deps.py` | Valid JWT + live session + existing user |
| `require_active_user` | `auth/deps.py` | Inactive users are rejected |
| `require_password_ok` | `auth/deps.py` | Blocks users who must change password |
| `require_admin` | `auth/deps.py` | `is_admin` only; frontend flags are ignored |
| `assert_owner` / `assert_task_owner` | `auth/isolation.py` | Fail closed with 404 |

Route handlers do not run raw SQL. User and session writes go through `UserRepository` and `SessionRepository`.

## Protected vs public routes

**Public (by design):**

- `GET /api/v1/health`
- `GET /api/v1/media/{filename}` — Meta must fetch hosted JPEGs; keys are unguessable UUIDs
- `GET /` demo UI
- Auth register / login

**Authenticated + password already changed:**

- `POST /api/v1/instagram/publish`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/events`
- Instagram account connect / status / disconnect
- Generation, posts, business profile, automation, festival campaigns

**Admin only:**

- `GET /api/v1/admin/users`

Authorization is enforced on the backend. A React hide/show is never sufficient.

## User isolation

User A cannot read or mutate User B's:

- generated images
- Instagram posts
- business profile
- automation settings
- festival campaigns
- Instagram account / encrypted credentials
- agent tasks
- agent events

Lookups use `*_repository.get_owned(user_id, id)` or `assert_task_owner`. A missing row and another user's row both return 404 so existence is not leaked. Admins do not get a backdoor through user-scoped routes.

`AgentState.user_id` is set on every authenticated publish. Task and SSE reads compare that field to the current user.

## Secrets that never reach React

Responses, SSE payloads, and cookies never include:

- `OPENAI_API_KEY`
- `META_ACCESS_TOKEN`
- `password` / `password_hash`
- Instagram access tokens (`access_token_encrypted` is stored only after Fernet encryption)

`services/logging.py` redacts password, JWT, OpenAI, Meta, and encrypted Instagram token keys.

## CORS

`CORSMiddleware` is configured from `CORS_ORIGINS` with `allow_credentials=True` so the httpOnly cookie works from the Vite dev origin.

## Environment

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET` | HMAC key for access tokens |
| `JWT_EXPIRE_MINUTES` | Access-token lifetime (default 480) |
| `JWT_COOKIE_SECURE` | Set true behind HTTPS |
| `DEFAULT_ADMIN_EMAIL` | Bootstrap admin email |
| `DEFAULT_ADMIN_PASSWORD` | Bootstrap admin password |
| `CORS_ORIGINS` | Comma-separated browser origins |
| `DATABASE_URL` | SQLite locally; PostgreSQL-ready |

## Files

| Path | Role |
| --- | --- |
| `auth/passwords.py` | bcrypt hash / verify |
| `auth/tokens.py` | JWT create / decode |
| `auth/service.py` | register, login, logout, change-password |
| `auth/deps.py` | FastAPI dependencies |
| `auth/bootstrap.py` | env-only admin create |
| `auth/isolation.py` | owner checks + public user DTO |
| `api/auth_routes.py` | HTTP routes |
| `db/models.py` `AuthSession` | revocable sessions |
| `db/repositories.py` `SessionRepository` | session persistence |

## Tests

`tests/test_auth.py` covers registration, login, invalid credentials, protected routes, logout, password change, forced admin password change, admin authorization, user isolation, token expiration, and inactive users.

Run:

```bash
pytest
```
