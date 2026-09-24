# Yotto Labs frontend

Vite + React + TypeScript dashboard for the Instagram Agentic AI platform.

The UI talks only to FastAPI `/api/v1` routes that exist in this repository. It does not invent endpoints, and it never reads `OPENAI_API_KEY`, `META_ACCESS_TOKEN`, password hashes, or OAuth tokens.

## Architecture

```
Browser
  → React routes + AuthContext + ToastContext
    → services/api (central HTTP client)
      → FastAPI /api/v1
        → Auth, dashboard, generation, posts, automation,
          festivals, business, Instagram, admin, SSE task events
```

- **Auth state** is centralized in `AuthContext`. Pages do not keep their own session copy.
- **Toasts** live in `ToastContext`.
- **Server data** is fetched per page. There is no global store for posts, images, or automation.
- **404** from a platform route means “not mounted yet” and is shown as an empty / unavailable state.
- Manual generation never publishes without an **Approve & Post** confirmation.

```
frontend/src/
  pages/            route screens
  components/       layout, dialogs, badges, timeline
  context/          AuthContext, ToastContext
  services/api/     HTTP client and per-resource APIs
  types/api.ts      contracts matching FastAPI payloads
  test/             Vitest + Testing Library
```

## Routes

| Path | Page | Auth |
| --- | --- | --- |
| `/login` | Email / password sign-in, including forced password change after login | public |
| `/register` | Account creation | public |
| `/dashboard` | Stats, recent activity / images / posts, upcoming festival, next scheduled post | session |
| `/generate` | Prompt → preview → regenerate / reject / approve & post | session |
| `/images` | Generated image cards | session |
| `/posts` | Publication history | session |
| `/automation` | Daily / festival scheduler controls | session |
| `/festivals` | Campaign progress | session |
| `/business` | Business profile fields | session |
| `/instagram` | Connection + V1 manual publish + agent timeline | session |
| `/brand` | Logo, guidelines, colors, festival preferences | session |
| `/products` | Product metadata, image upload, active offers | session |
| `/assets` | Owner-scoped brand and product files | session |
| `/ai-settings` | DeepSeek, OpenAI, and Canva connection status | session |
| `/mcp` | MCP tool availability | session |
| `/campaigns` | Festival campaigns and generate handoff | session |
| `/settings` | Password change and workspace snapshot | session |
| `/admin` | Users and API health | admin |

If `must_change_password` is true, every protected route except `/settings` shows the password-change gate. The default admin password is never hard-coded in this app.

## Components

- `AppShell` — desktop-first sidebar, mobile drawer
- `ProtectedRoute` — session + forced password + admin gate
- `Timeline` — live agent execution steps (SSE with HTTP poll fallback)
- `ConfirmDialog` — auto-publish and approve/post confirmations
- `StatusBadge`, `ProgressBar`, `EmptyState`, `ErrorState`, `LoadingState`
- `ChangePasswordForm` — used on `/settings` and the forced-password gate

## API integration

Central client: `frontend/src/services/api/`.

Confirmed FastAPI routes (inspected in `api/routes.py`, `api/auth_routes.py`, `api/platform_routes.py`, `api/generation.py`, `api/instagram_account_routes.py`):

V1 Instagram Agent (`api/routes.py`):

- `GET /api/v1/health`
- `POST /api/v1/instagram/publish`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/events` (SSE; the client falls back to polling `GET /api/v1/tasks/{task_id}`)
- `GET /api/v1/media/{filename}`

Platform / auth:

- `POST /api/v1/auth/register|login|logout`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/change-password`
- `GET /api/v1/dashboard`
- `GET|PUT /api/v1/business` (`{ profile }`)
- `GET|POST /api/v1/generation`
- `GET /api/v1/generation/{id}`
- `POST /api/v1/generation/{id}/approve|reject|regenerate`
- `GET /api/v1/media/generated/{id}`
- `GET /api/v1/posts` (`{ posts }`)
- `GET|PUT /api/v1/automation` and `POST /api/v1/automation/run-now`
- `GET /api/v1/festivals`, `GET /api/v1/festivals/campaigns`, `PUT /api/v1/festivals/settings`
- `GET /api/v1/instagram/status`
- `POST /api/v1/instagram/connect|disconnect` (body: `instagram_account_id`, `access_token`; the token is never returned)
- `GET /api/v1/admin/users`
- `GET /api/v1/settings`
- `GET /api/v1/tasks/{task_id}/owned`

Additive catalog routes mounted by `api/asset_routes.py` (owner-scoped; a 404 is an empty state):

- `GET|POST /api/v1/brand` — `company_name`, `website`, `instagram_handle`, `brand_colors` (`{ hex }`), `fonts`, `guidelines`, `logo_png_asset_id`, `logo_svg_asset_id`
- `GET|POST /api/v1/assets` — multipart `file` plus `role` (`logo_png`, `logo_svg`, `product_image`, …) and optional `product_id`
- `GET /api/v1/assets/{id}/media`
- `DELETE /api/v1/assets/{id}`
- `GET|POST /api/v1/products`, `PUT /api/v1/products/{id}` — product `offer` and numeric `price` live on the product

`festival_preferences` is included on brand save as an optional field. The current `BrandWrite` model ignores unknown keys, so that list is kept in the session until the API stores it.

`GET /api/v1/ai/status` and `GET /api/v1/mcp/status` are optional reads. When they are not mounted, DeepSeek and OpenAI show **Not configured**, Canva shows **Not connected**, and MCP shows **Not available**. A mounted status payload should match `Settings.public_ai_status()` (`deepseek_configured`, `openai_configured`, `canva_configured`, `mcp_enabled`) and must not include keys, tokens, or secrets.

`POST /api/v1/generation` still accepts `{ "prompt" }`. The generator may also send optional `product_id` and `festival`; servers that ignore unknown keys keep working.

Responses must not include API keys, Meta access tokens, JWT secrets, encryption keys, Canva tokens, or filesystem paths. The UI renders only whitelisted labels.

HTTP handling:

| Status | UI |
| --- | --- |
| 401 | clear session, redirect to login |
| 403 | permission error |
| 404 | empty “not available yet” or not found |
| 422 | form validation message |
| 500 | server error + retry |

Bearer tokens are stored only if login returns `access_token` / `token`. Cookie sessions use `credentials: include`.

Dashboard prefers `GET /api/v1/dashboard`. If that route is not mounted, it composes the same stats from posts, generation, automation, festivals, and Instagram status.

## Auth flow

1. `POST /api/v1/auth/login` or `register`
2. Persist optional JWT in `localStorage` under `agentic.access_token`
3. `GET /api/v1/auth/me` on load (`credentials: include` for cookie sessions)
4. Forced password change when `must_change_password` is true
5. `POST /api/v1/auth/logout`

## Agent activity

`/generate` (after approve) and `/instagram` (manual publish) subscribe to `GET /api/v1/tasks/{id}/events`. If EventSource fails, they poll `GET /api/v1/tasks/{id}` every 1.5s until `completed` or `failed`.

## Development commands

From `frontend/`:

```bash
npm install
npm run dev      # http://127.0.0.1:5173  (proxies /api → :8000)
npm test
npm run build
npm run preview
```

FastAPI must be running for live data:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Docker (UI on port 3000, API proxied at `/api`):

```bash
docker compose up --build
```
