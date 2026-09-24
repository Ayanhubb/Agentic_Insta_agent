# API reference

Source of truth: route decorators under `api/`, mounted in `api/app.py` with prefix `/api/v1` unless noted. Related: [Authentication](AUTHENTICATION.md), [Instagram API](INSTAGRAM_API.md).

## Auth model

Most routes use `require_password_ok`: valid session (cookie `access_token` or `Authorization: Bearer`), user active, and `must_change_password` is false. Otherwise 403 `MUST_CHANGE_PASSWORD`.

Admin routes use `require_admin` (password ok and `is_admin`).

Errors raised as `AppError` become:

```json
{"success": false, "task_id": null, "request_id": "...", "platform": "instagram", "status": "failed", "error": {"code": "...", "message": "..."}}
```

Owner scope means the row's `user_id` must equal the session user. Foreign ids return 404, not 403.

## App

| Method | Path | Auth | Response |
| --- | --- | --- | --- |
| GET | `/` | public | `api/static/index.html`, or 500 if the file is missing |
| GET | `/static/...` | public | Mounted only when `static_dir` exists |

## Auth — `api/auth_routes.py`

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/auth/register` | public | `email`, `password` (min 8) | `{user, access_token, token_type}` and sets the cookie. Conflict if the email exists |
| POST | `/api/v1/auth/login` | public | `email`, `password` | Same session shape. 401 on bad credentials |
| POST | `/api/v1/auth/logout` | current user | none | `{success: true}`, revokes the session |
| GET | `/api/v1/auth/me` | active user | none | `{user}` with `id`, `email`, `is_admin`, `is_active`, `must_change_password` |
| POST | `/api/v1/auth/change-password` | active user | `current_password`, `new_password` (min 8) | New session. 401 if the current password is wrong |
| GET | `/api/v1/admin/users` | admin | none | `{users}` from `UserRepository.list_all()` |

`api/platform_routes.py` also declares `GET /api/v1/admin/users` (`list_active`). The auth router is mounted first, so Starlette serves the auth handler. The platform handler is unreachable. Status: **PARTIAL** (duplicate registration).

## Publish and tasks — `api/routes.py`

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | public | none | `{"status":"ok"}` |
| POST | `/api/v1/instagram/publish` | password ok | Multipart `image` or `file`. Optional form `caption` (max 2200). Query `wait` default false | 202 while the agent runs, or 200 when `wait=true` and the task succeeds. JPEG/PNG only. 415 wrong type, 413 too large |
| GET | `/api/v1/tasks/{task_id}` | password ok | path | Task status. Other users' tasks are 404 |
| GET | `/api/v1/tasks/{task_id}/events` | password ok | path | `text/event-stream` until completed or failed |
| GET, HEAD | `/api/v1/media/{filename}` | public | path | Prepared JPEG for Meta to fetch. Invalid path 400. Missing file 404 |
| GET, HEAD | `/api/v1/generation/{image_id}/media` | current user | optional query `stage` | Owner file. `stage` containing `..` or a slash is 400 |

## Platform — `api/platform_routes.py`

| Method | Path | Auth | Request | Response / errors |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/business` | password ok | none | `{profile}` or null |
| PUT | `/api/v1/business` | password ok | `BusinessBody` | Upsert `{profile}` |
| POST | `/api/v1/generation` | password ok | `prompt` min 3, optional `product_id`, `offer_id`, `festival`, `use_canva` | Plan and image. `published` is false |
| GET | `/api/v1/generation` | password ok | none | `{images}` for this user |
| GET | `/api/v1/generation/{image_id}` | password ok | path | `{image}` or 404 |
| POST | `/api/v1/generation/{image_id}/reject` | password ok | path | Approval `REJECTED` |
| POST | `/api/v1/generation/{image_id}/regenerate` | password ok | path | New image, `published` false |
| POST | `/api/v1/generation/{image_id}/approve` | password ok | path | Approves and publishes via `PublicationService` |
| GET | `/api/v1/posts` | password ok | none | `{posts}` |
| GET | `/api/v1/automation` | password ok | none | Automation dict |
| PUT | `/api/v1/automation` | password ok | `AutomationBody`, fields optional | Forces `daily_posts_per_day` to 1 when that field is sent |
| POST | `/api/v1/automation/run-now` | password ok | none | One scheduler tick for this user |
| GET | `/api/v1/festivals` | password ok | none | Catalog for the clock year, Asia/Kolkata |
| GET | `/api/v1/festivals/campaigns` | password ok | none | This user's campaigns |
| PUT | `/api/v1/festivals/settings` | password ok | Festival fields of `AutomationBody` | Automation dict |
| GET | `/api/v1/instagram/status` | password ok | none | Account status with secrets stripped |
| POST | `/api/v1/instagram/connect` | password ok | `instagram_account_id`, `access_token` | Encrypted store. Token is not echoed |
| POST | `/api/v1/instagram/disconnect` | password ok | none | Disconnected status |
| GET | `/api/v1/dashboard` | password ok | none | Counts, recent rows, `trend_intelligence` |
| GET | `/api/v1/tasks/{task_id}/owned` | password ok | path | Task plus events, or 404 |
| GET | `/api/v1/media/generated/{image_id}` | password ok | path | File, or 404 |
| GET | `/api/v1/settings` | password ok | none | `{user, automation}` |

`api/instagram_account_routes.py` defines status/connect/disconnect again and is not included in `create_app`. Status: **NOT IMPLEMENTED** as a live router. Use the platform paths.

## Assets and catalog — `api/asset_routes.py`

All require password ok and are scoped to `user.id`.

| Method | Path | Request |
| --- | --- | --- |
| POST | `/api/v1/assets` | Multipart `file`, form `role`, optional `scope`, `product_id`. 413 if too large |
| GET | `/api/v1/assets` | Query `scope`, `role` |
| GET | `/api/v1/assets/{asset_id}` | |
| GET | `/api/v1/assets/{asset_id}/media` | File |
| DELETE | `/api/v1/assets/{asset_id}` | `{deleted: true}` |
| POST | `/api/v1/products` | `ProductWrite` |
| GET | `/api/v1/products` | Query `active` default false |
| GET | `/api/v1/products/{product_id}` | |
| PUT | `/api/v1/products/{product_id}` | `ProductUpdate` |
| POST | `/api/v1/products/{product_id}/images` | Multipart `file` |
| POST | `/api/v1/brand` | `BrandWrite` |
| GET | `/api/v1/brand` | |
| GET | `/api/v1/brand/guidelines` | |
| PUT | `/api/v1/brand/guidelines` | `StudioGuidelines` |
| GET | `/api/v1/brand/assets` | Brand scope |
| POST | `/api/v1/brand/assets` | Multipart `file`, form `kind` default `LOGO` |
| DELETE | `/api/v1/brand/assets/{asset_id}` | |
| GET | `/api/v1/brand/assets/{asset_id}/media` | File |
| GET | `/api/v1/offers` | Derived from products that have offer text |
| POST | `/api/v1/offers` | `OfferWrite` |
| GET | `/api/v1/ai/status` | Provider names and configured booleans. No keys |
| GET | `/api/v1/mcp/status` | Tool list and Canva connection flags |

## Canva — `api/canva_routes.py`

Prefix `/api/v1/integrations/canva`. Password ok.

| Method | Path | Response |
| --- | --- | --- |
| GET | `/status` | Public connection fields |
| POST | `/connect` | `{authorization_url}` |
| GET | `/callback` | Query `code`, `state`. 303 redirect or HTML |
| POST | `/disconnect` | `{connected: false}` |

## Trends — `api/trend_routes.py`

Prefix `/api/v1/trends`. Password ok. Queries `industry` and `region` are optional on the list routes.

| Method | Path | Response |
| --- | --- | --- |
| GET | `/account` | Observed account metrics |
| GET | `/research` | `{trends, filters}` |
| GET | `/opportunities` | `{opportunities, filters}` |
| GET | `/reports` | Report with account, trend, festival, product, and content queue sections |
| POST | `/opportunities/{opportunity_id}/dismiss` | Updated row, or 404 |
| POST | `/opportunities/{opportunity_id}/save` | Updated row, or 404 |
| GET | `/opportunities/{opportunity_id}/evidence` | Evidence plus a note, or 404 |
| POST | `/opportunities/{opportunity_id}/create-content` | Prompt and `path: "/generate"`. Does not publish. Expired → 404 |

## Intelligence — `api/intelligence_routes.py`

Prefix `/api/v1/intelligence`. Password ok.

| Method | Path |
| --- | --- |
| GET | `/account` |
| GET | `/account/posts` |
| GET | `/account/insights` |
| GET | `/account/top-content` |

Field availability is documented in [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md).
