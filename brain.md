# brain.md — Instagram Agentic AI

Working memory for this repo. Read this before changing architecture, publishing, auth, or the scheduler.

**Integration status (2026-09-24):** landed. Contract: [docs/AI_MCP_INTEGRATION_PLAN.md](docs/AI_MCP_INTEGRATION_PLAN.md). Current picture: [ARCHITECTURE.md](ARCHITECTURE.md).

DeepSeek plans. OpenAI makes pixels. DeepSeek Vision does QA. In-process MCP is `backend/mcp/`. Brand and product catalog APIs exist. Canva is optional and off by default. **Instagram Agent stays the only Meta publisher.** The Agent 1 note said not to add `backend/`; the implementation did. Use `backend/` for MCP, the OpenAI image adapter, and Canva. Do not add a second publisher there.

Content Agent still uses legacy `_plan` when MCP or vision is absent. Festival dates stay in `festivals/`. Ambiguous Meta publishes never auto-retry. Product-type plans with no featured name take the first catalog product so daily auto-publish is not blocked. Daily counts use `scheduled_date`.

Product name in docs: **Instagram Agentic AI** / **Yotto Labs frontend**.
Repo: `agentic`. Branding: Yotto Labs (`admin@yottolabs.com` bootstrap).

---

## What this is

Multi-user Instagram **still-image** content studio:

1. User (or scheduler) asks for content.
2. **MCP** loads festival, business, brand, product, and rules for that user. Parallel where the reads are independent.
3. **DeepSeek** writes the creative plan. **OpenAI** generates the still. **DeepSeek Vision** reviews it. Mocks when keys are missing.
4. Image and QA (`qa_status`, `qa_json`) are stored, owner-scoped. Failed QA cannot auto-publish.
5. Human **Approve & Post** (manual) or auto-approve (automation flags, every gate clear).
6. **Instagram Agent** is the **only publisher**. Six allowlisted tools. Meta Graph API only.
7. Verification required. Only verified `PUBLISHED` + `instagram_media_id` counts. Daily count uses the scheduled local date.

Not a chatbot. Not Reels/Stories/carousels. Not a multi-platform publisher. LLM never calls Meta.

---

## Non-negotiable invariants

1. **Instagram Agent is the only publisher.** Content Agent, LLM, scheduler, React never import Graph clients or call `media_publish`.
2. **LLM supplies intelligence, FastAPI keeps control.** Model output is JSON + Pydantic. Extra keys (`command`, `token`, `url`, tool names) are ignored or rejected. No `eval`/`exec`/shell.
3. **User isolation.** Every product row has `user_id`. Lookups use `get_owned(user_id, id)`. Missing and foreign both 404.
4. **Secrets never leave the backend.** No `VITE_*` / `NEXT_PUBLIC_*` for OpenAI or Meta. Instagram tokens Fernet-encrypted at rest. Logging redacts tokens/keys/passwords.
5. **Counting.** Only `status=PUBLISHED` + verified media id increments dashboard / daily / festival counts. `FAILED` retries. `AMBIGUOUS_PUBLICATION` does **not** count and must **not** be republished (duplicate risk).
6. **VERIFY_FIRST after unknown publish.** Timeout / unknown `media_publish` → skip publish, verify only. Never call `publish_instagram_media` again for that task.
7. **Single uvicorn worker** when `SCHEDULER_ENABLED=true`. Two processes both tick.
8. **User-prompt content is never auto-published**, even if `auto_daily_publish` / `auto_festival_publish` is on.
9. **Do not put SQL in route handlers.** Use `Database` / repositories. Do not dual-write SQLite while a session still holds the write lock (persist after scheduler commit).
10. **Meta fetches a public HTTPS JPEG.** `http://127.0.0.1` is invalid for real posts. `IMAGE_PUBLIC_BASE_URL` must be public HTTPS.

---

## System diagram

```text
Browser (Vite React :5173 / nginx :3000)
  credentials: include + optional Bearer
        │  /api/v1/*
        ▼
FastAPI  (uvicorn main:app :8000)
  CORS, RequestContext, JWT cookie/Bearer attach_user
        │
        ├─ auth/                 bcrypt + JWT + revocable sessions
        ├─ Content Agent         plan JSON → image bytes → GeneratedImage
        │     ├─ backend/mcp/    allowlisted tools; tenant from auth/scheduler
        │     ├─ ai/llm/deepseek.py     plan (or OpenAI / mock)
        │     ├─ OpenAI image           still (or mock)
        │     └─ ai/vision/deepseek.py  QA; None when unconfigured
        ├─ scheduler/pipeline.py MCP → content → optional Canva → QA → approval → gateway
        ├─ Canva (optional)      backend/integrations/canva/  never publishes
        ├─ MediaStorageService   storage/generated|prepared|published/{user_id}/
        ├─ Approval              failed QA, bad festival, foreign owner → hold
        ├─ PublicationGateway    sole enqueue into Instagram Agent
        │     └─ Instagram Agent
        │           DeterministicPlanner → ToolRegistry → Executor
        │           → Observation → RecoveryPolicy → StateMachine
        │           tools: validate → prepare → upload → create → publish → verify
        │           InstagramGraphClient → graph.facebook.com / graph.instagram.com
        ├─ Scheduler (optional in-process loop, Asia/Kolkata)
        │     DailyScheduler + FestivalScheduler
        └─ db/  SQLAlchemy  (SQLite default, PostgreSQL-ready)
              TaskStore (in-memory + SSE; persist_agent_state for durability)
```

V1 upload path (preserved, now requires login):

```text
POST /api/v1/instagram/publish  (multipart JPEG/PNG, optional caption)
  → save tmp → PublicationGateway.enqueue_publication
    → validate_image → prepare_image → upload_image (public JPEG URL)
      → create_instagram_media → publish_instagram_media → verify_publication
```

---

## Process / deploy

| Mode | How |
| --- | --- |
| Local API | `.venv` + `uvicorn main:app --reload --host 127.0.0.1 --port 8000` |
| Local UI | `cd frontend && npm run dev` → http://localhost:5173 proxies `/api` → :8000 |
| Docker | `docker compose up --build` — backend :8000, frontend nginx :3000 reverse-proxies `/api/` |
| DB | Default `sqlite:///./data/agentic.db`. Docker uses `sqlite:////app/storage/app.db` on volume `agent-storage` |
| Scheduler | `SCHEDULER_ENABLED=true`, interval default 60s, **one worker** |

Entry: `main.py` → `create_app()` in `api/app.py`.

`create_app()` wires:

- engine + `init_db` + admin bootstrap
- `TaskStore`, `MediaStorageService`, LLM/image providers (live or mock)
- `AutomationRunner` + optional `scheduler_loop`
- routers: auth, admin, platform, V1 instagram/health/tasks
- `PublicationGateway` bound via `bind_publication_gateway`
- CORS from `CORS_ORIGINS`, cookie credentials allowed

Tests inject fakes through `create_app(...)` kwargs (`instagram_client`, `llm_provider`, `image_provider`, `clock`, `current_user_provider`, `publication_store`, `task_store`).

---

## Directory map

```text
agentic/
  main.py                 ASGI app
  config.py               Settings.from_env(); never hard-code secrets
  Dockerfile              backend Python 3.12, uvicorn
  docker-compose.yml      backend + frontend + volumes
  alembic.ini             later PostgreSQL migrations

  api/
    app.py                factory, lifespan, CORS, user attach, AppError handler
    routes.py             health, POST /instagram/publish, tasks + SSE, media host
    platform_routes.py    business, generation, posts, automation, festivals, dashboard, IG connect
    auth_routes.py        /auth/* + admin_router /admin/users
    generation.py         GET/HEAD /generation/{id}/media (owner bytes)
    instagram_account_routes.py   duplicate-ish connect/status (platform_routes is canonical)
    task_store.py         in-process tasks + SSE queues; optional persist
    middleware.py         request id
    security.py           CurrentUser helper for generation media
    static/               leftover V1 demo UI at GET /

  auth/                   passwords, JWT, service, deps, bootstrap, isolation
  agent/
    content_agent.py      Content Strategy Agent (never publishes)
    agent.py              InstagramAgent observe-decide-act loop
    planner.py            DeterministicPlanner; LLMPlanner is a NON-EXECUTING stub
    registry.py           allowlist ToolRegistry
    executor.py           one tool → Observation
    recovery.py           CONTINUE / RETRY / FAIL / VERIFY_FIRST / SKIP
    state_machine.py      legal TaskStatus transitions only
    instagram_tasks.py    enqueue_instagram_publication / enqueue_from_handoff
    state.py              compat re-export

  ai/
    llm_client.py         LLMProvider + get_llm_provider()  openai | deepseek
    llm/deepseek.py       DeepSeek chat. Does not publish.
    vision/deepseek.py    DeepSeek vision QA. None without a key.
    openai_llm.py         OpenAILLMProvider
    image_generator.py    ImageGenerationProvider
    openai_image_generator.py
    mocks.py              MockLLMProvider / MockImageGenerationProvider
    schemas.py            ContentPlan, GeneratedImage DTOs
    creative_model.py     grounded plan when DeepSeek is off

  backend/
    mcp/                  in-process allowlist + ContextBuilder (asyncio.gather)
    ai/image/             OpenAI image adapter used beside ai/openai_image_generator.py
    integrations/canva/   official remote MCP; get_canva_client() is None when disabled

  tools/                  Instagram Agent tools only
    image_validator.py    JPEG/PNG magic + Pillow + size/dims
    image_preparer.py     Instagram JPEG 4:5–1.91:1, 1080w
    image_storage.py      public host under storage_dir; GET /api/v1/media/{filename}
    instagram_media.py    Graph: create container, poll, media_publish
    instagram_verifier.py confirm media id + IMAGE type
    base.py               Tool protocol

  services/
    publication.py        PublicationGateway + PublicationService facade
    publication_store.py  in-memory publication records (tests + process)
    instagram_client.py   InstagramGraphClient
    media_storage.py      owner-scoped generated lifecycle
    media_paths.py        ONLY module that resolves generated paths
    media_repository.py
    logging.py            redaction
    clock.py              injectable time (scheduler tests)

  db/
    models.py             ORM
    repositories.py       all queries
    uow.py                Database(session, encryptor)
    session.py            engine, init_db, get_db
    persist.py            AgentState → agent_tasks / events / instagram_posts
    schemas.py            Pydantic DTOs
    enums.py
    crypto.py             Fernet Instagram tokens
    exceptions.py         DuplicateRecordError, RecordNotFoundError, TokenEncryptionError
    migrations/           Alembic 001 schema, 002 brand/product assets, 003 Canva, 004 image QA columns

  scheduler/
    scheduler.py          AutomationRunner.tick + scheduler_loop
    daily_scheduler.py    1 verified daily post / account / local date
    festival_scheduler.py default 2 verified posts per campaign
    pipeline.py           content → optional Canva → QA → approval → PublicationService
    approval_policy.py    human mode always pending; failed QA never auto-publishes
    image_qa.py           structural QA, or DeepSeek Vision when configured
    integrations.py       resolve_vision / resolve_festival_mcp / resolve_canva
    policies.py           what counts as published; FestivalDiversityPolicy
    job_manager.py        last-run records

  festivals/
    india_festivals.py    catalog — date source of truth
    festival_service.py   campaign windows
    mcp.py                FestivalMcp for the scheduler; reads the catalog
    mcp_tools.py          FestivalToolServer (regional filter, lunar dates)
    data/lunar_dates.json per-year lunar/Islamic dates — do not compute; update the table

  models/                 Pydantic domain (errors, state, content, requests, responses)
  frontend/               Vite + React 19 + TS, Vitest
  tests/                  pytest; default fully mocked
  scripts/init_db.py
```

Existing long-form docs (do not duplicate; this file is the index):

- `ARCHITECTURE.md` `AI_ARCHITECTURE.md` `CONTENT_AGENT.md` `INSTAGRAM_AGENT.md`
- `AUTHENTICATION.md` `DATABASE.md` `MEDIA_STORAGE.md` `SCHEDULER.md`
- `FESTIVAL_AUTOMATION.md` `FRONTEND.md` `README.md`
- `ARCHITECTURE_AUDIT.md` is a **V1→V2 audit** (historical). Prefer current docs + this file.

---

## Two agents (do not mix)

### Content Agent (`agent/content_agent.py`)

Plans content + may generate an image. **`published` is always false.**

Modes:

| Mode | Trigger | Auto-publish? |
| --- | --- | --- |
| `USER_PROMPT` | `POST /generation` | Never. Always `PENDING_APPROVAL` |
| `DAILY` | scheduler | If `daily_enabled` and `auto_daily_publish` |
| `FESTIVAL` | scheduler | If `festival_enabled` and `auto_festival_publish` |

Needs a business profile (`CONTENT_PROFILE_MISSING` otherwise).

Diversity (token Jaccard, theme equality, type repetition): **blocks** daily/festival after one retry; **advisory only** for user prompts.

Festival plans must mention **this** business. Generic “Happy Diwali fireworks” is rejected.

Handoff is data only: `generated_image_id`, `image_path`, `caption`, `user_id`, `source`, `requires_instagram_agent=true`. Callers then `enqueue_*`.

### Instagram Agent (`agent/agent.py`)

Deterministic six-tool pipeline. Production planner is `DeterministicPlanner`. `LLMPlanner` exists as a stub and **must not** be pointed at OpenAI with publish tools in scope.

Allowlisted tools (`agent/planner.py` `ALLOWED_TOOLS`):

1. `validate_image`
2. `prepare_image`
3. `upload_image`
4. `create_instagram_media`
5. `publish_instagram_media`
6. `verify_publication`

Unknown tool names are rejected by `ToolRegistry`.

TaskStatus machine (`agent/state_machine.py`):

```text
pending → planning → validating → preparing → uploading
  → creating_media → publishing → verifying → completed | failed
```

`creating_media` may jump to `verifying` (skip publish). `verifying` may loop (retry verify). Terminal: completed/failed.

Recovery (`agent/recovery.py`):

| Observation | Decision |
| --- | --- |
| Success | CONTINUE |
| Invalid / corrupted image | STOP |
| Auth / permission | STOP |
| Temp storage / short rate-limit | bounded RETRY |
| Publish timeout / unknown | VERIFY_FIRST, set skip_publish |
| Verify delay | retry verify only |
| Unconfirmed | `AMBIGUOUS_PUBLICATION` |

Graph HTTP lives in `tools/instagram_media.py`. Hosts allowlisted: `graph.facebook.com`, `graph.instagram.com` (localhost for tests).

---

## Publication gateway

`services/publication.py`

- `PublicationGateway` — sole enqueue path.
- `PublicationService` — thin subclass that can share a SQLAlchemy session (scheduler + approve route).

Public entry points:

- `POST /api/v1/instagram/publish` (authenticated upload)
- `enqueue_instagram_publication(...)` / `enqueue_from_handoff(...)` in `agent/instagram_tasks.py`
- `PublicationService.publish_generated_image(...)` used by approve + daily/festival schedulers

Credential resolution:

1. User's connected Instagram account (encrypted token in DB)
2. Optional env fallback (`META_ACCESS_TOKEN` + `INSTAGRAM_ACCOUNT_ID`) only when `allow_environment_fallback=True` (V1 upload + generated publish currently pass this)

Connect (`POST /instagram/connect`) calls Graph `get_account` **before** storing the token. Auth/permission failure does not save.

Duplicate guards (gateway + SQL):

- Same `generated_image_id` cannot enter PUBLISHING / PUBLISHED / AMBIGUOUS twice
- Daily: unique `(user, account, local_date)` slot + partial unique index on published `DAILY_RETAIL_POST`
- `FAILED` daily may retry same date; `AMBIGUOUS` must not

`PublicationGateway` also mirrors into `InMemoryPublicationStore` for process/tests. Durable source of truth is SQLAlchemy via `_db_*` helpers + `db.persist.persist_agent_state`.

---

## Auth

`auth/` + `api/auth_routes.py`

- bcrypt hashes
- JWT `{ sub/user_id, email, jti, exp }` HMAC `JWT_SECRET`
- httpOnly `SameSite=Lax` cookie `access_token` + JSON `access_token` for Bearer
- `sessions` row keyed by `jti`; logout revokes
- `require_password_ok` on product routes
- Bootstrap admin from `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` with `must_change_password=true`
- Admin gate is **backend** `is_admin`, never a frontend flag

Public: health, register, login, `GET /api/v1/media/{filename}` (Meta fetch; UUID names), demo UI `/`.

Forced password: all product routes 403 `MUST_CHANGE_PASSWORD` except me / change-password / logout. Frontend gates every protected route except `/settings`.

---

## Database

SQLAlchemy 2.x. SQLite WAL locally. Alembic ready for `postgresql+psycopg://...`.

Tables:

| Table | Role |
| --- | --- |
| `users` | email unique, bcrypt, is_admin, must_change_password |
| `sessions` | JWT jti, revoke |
| `instagram_accounts` | unique (user, ig user id); `access_token_encrypted` |
| `business_profiles` | 1:1 user; products/services JSON |
| `generated_images` | owner files + approval/publication status + `qa_status` / `qa_json` |
| `business_assets`, `brand_profiles`, `brand_guidelines` | logos and brand rules, owner-scoped |
| `products`, `product_assets` | catalog products and images |
| `canva_connections`, `canva_oauth_states` | per-user Canva tokens; never returned to React |
| `instagram_posts` | publication records; FK to account **row id** (not Meta ig id) |
| `agent_tasks` | PK = V1 `task_id` string; `state_json` snapshot |
| `agent_events` | timeline |
| `scheduled_jobs` | unique (user, account, job_type) |
| `festival_campaigns` | unique (user, festival_name, year); remaining = required − published |
| `festival_posts` | unique (campaign, sequence_number) |
| `automation_settings` | 1:1 user; daily/festival flags + timezone |
| `daily_post_slots` | scheduler claim per (user, account, local_date) |

`instagram_posts.instagram_account_id` is FK to `instagram_accounts.id`. Meta's IG user id lives on `instagram_accounts.instagram_account_id`. Do not confuse these.

Published row **requires** `instagram_media_id` (check constraint).

Unit of work: `Database(session, encryptor)` with repo attributes. Import from `db` / `db.repositories`. Aliases exist (`BusinessRepository` = `BusinessProfileRepository`, etc.).

---

## Media

Two stores:

1. **V1 public host** (`tools/image_storage.py`) — Instagram-fetchable JPEGs. `GET /api/v1/media/{filename}`.
2. **Generated lifecycle** (`services/media_storage.py` + `media_paths.py`):

```text
storage/generated/{user_id}/img_<32hex>.png
storage/prepared/{user_id}/img_<32hex>.jpg
storage/published/{user_id}/img_<32hex>.jpg
```

Path rules: sanitize user_id to `[A-Za-z0-9_-]`; filename `img_<32 hex>.(png|jpg|jpeg)`; resolved path must stay in `media_root`; atomic write.

API never returns filesystem paths. Preview via `/api/v1/media/generated/{id}` or `/api/v1/generation/{id}/media`.

---

## Scheduler + festivals

Timezone default **Asia/Kolkata**. “Today” uses the user's `automation_settings.timezone`. Store timestamps UTC.

Daily:

1. `daily_enabled`
2. after `daily_post_time` (or `POST /automation/run-now` force)
3. no existing PUBLISHED `DAILY_RETAIL_POST` for (user, account, local date)
4. claim `daily_post_slots`
5. CampaignPipeline: MCP context → Content Agent DAILY → QA → approval gates → `publish_generated_image` → Instagram Agent → verify

Festival:

- Default `required_posts = 2`: pre-festival day (`festival_date - pre_festival_days`) + festival day
- Not same calendar day unless `allow_same_day_festival_posts`
- Catch-up ≤ 7 days after festival, still one post/day
- Failures do **not** reset `required_posts` or increment `published_posts`
- Calendar: `festivals/india_festivals.py` + `festivals/data/lunar_dates.json`. DeepSeek must not invent dates. Diwali 2026 in the catalog is 2026-11-08.

`AutomationRunner.tick(user_id=)` is what `run-now` calls.

---

## Frontend

Vite 7 + React 19 + React Router 7. No Redux. No OpenAI/Meta keys.

```text
frontend/src/
  main.tsx              BrowserRouter + AuthProvider + ToastProvider
  App.tsx               routes
  pages/                one screen per route
  context/              AuthContext, ToastContext
  services/api/         central client; endpoints.ts is the path source of truth
  types/api.ts          FastAPI-shaped contracts
  components/           AppShell, ProtectedRoute, Timeline (SSE), ConfirmDialog, UI kit
  test/                 Vitest + Testing Library
```

Routes: `/login` `/register` `/dashboard` `/generate` `/images` `/posts` `/automation` `/festivals` `/business` `/brand` `/products` `/assets` `/campaigns` `/ai-settings` `/mcp` `/instagram` `/settings` `/admin` (admin). `/` → dashboard.

HTTP client: `credentials: include`; 401 clears session → login. SSE on tasks with 1.5s poll fallback.

Manual generate never publishes without **Approve & Post** confirmation.

Dev proxy: `/api` → `http://127.0.0.1:8000`. Docker nginx proxies `/api/` to `backend:8000` with long timeouts for publish + SSE (`proxy_buffering off`).

---

## API surface (`/api/v1`)

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/health` | public | |
| POST | `/auth/register` `/auth/login` | public | |
| POST | `/auth/logout` | auth | |
| GET | `/auth/me` | auth (even if must change pw) | |
| POST | `/auth/change-password` | auth | revokes other sessions |
| GET/PUT | `/business` | password-ok | |
| POST/GET | `/generation` | password-ok | Content Agent USER_PROMPT |
| GET | `/generation/{id}` | owner | |
| POST | `/generation/{id}/approve\|reject\|regenerate` | owner | approve → Instagram Agent |
| GET | `/generation/{id}/media` | owner | |
| GET | `/media/generated/{id}` | owner | |
| GET | `/media/{filename}` | public | Meta JPEG fetch |
| GET | `/posts` | owner | |
| GET/PUT | `/automation` | password-ok | |
| POST | `/automation/run-now` | password-ok | force tick |
| GET | `/festivals` `/festivals/campaigns` | auth | |
| PUT | `/festivals/settings` | auth | |
| GET | `/instagram/status` | auth | no token |
| POST | `/instagram/connect` `/disconnect` | auth | connect verifies Graph first |
| POST | `/instagram/publish` | password-ok | multipart `image` or `file`, optional `caption`, `?wait=` |
| GET | `/dashboard` | auth | |
| GET | `/tasks/{id}` `/tasks/{id}/events` | owner | SSE |
| GET | `/tasks/{id}/owned` | owner | platform alias |
| GET | `/admin/users` | admin | |
| GET/PUT | `/brand` `/brand/guidelines` | password-ok | studio brand |
| GET/POST | `/brand/assets` | password-ok | logo upload |
| GET/POST | `/products` `/products/{id}/images` | password-ok | catalog |
| GET/POST | `/offers` | password-ok | mapped onto product offer text |
| GET/POST | `/assets` | password-ok | owner files |
| GET | `/ai/status` `/mcp/status` | password-ok | flags only, no keys |
| GET | `/integrations/canva/status` | password-ok | no client secret or user token |
| GET | `/settings` | auth | public AI status, no keys |

Errors: `AppError` → `{ success, task_id, request_id, platform, status, error: { code, message } }`. Messages redacted.

---

## Config (env)

See `.env.example`. Important:

- `LLM_PROVIDER` `deepseek` or `openai`. `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` (default `deepseek-flash`), `VISION_PROVIDER=deepseek`
- `OPENAI_API_KEY`, `IMAGE_PROVIDER=openai`, `OPENAI_IMAGE_MODEL` (default `gpt-image-2.5-sunburst`), `LLM_MODEL`, `IMAGE_MODEL`
- Missing DeepSeek or OpenAI key → mocks / vision `None`. The app still boots.
- `CANVA_ENABLED` default false. `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_REDIRECT_URI`, `CANVA_MCP_URL=https://mcp.canva.com/mcp`
- `META_ACCESS_TOKEN`, `INSTAGRAM_ACCOUNT_ID` — V1 fallback only
- `META_GRAPH_API_BASE_URL`, `META_API_VERSION` (default v26.0)
- `DATABASE_URL`, `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET`
- `SCHEDULER_ENABLED`, `DEFAULT_TIMEZONE=Asia/Kolkata`
- `IMAGE_PUBLIC_BASE_URL` / `PUBLIC_IMAGE_BASE_URL`
- `CORS_ORIGINS`

`Settings.public_ai_status()` never includes the API key.

---

## Testing

```bash
pytest                          # mocked; excludes real_openai and real_deepseek
pytest -m real_openai           # needs RUN_OPENAI_INTEGRATION=1 + models
pytest -m real_deepseek         # needs RUN_DEEPSEEK_INTEGRATION=1
cd frontend && npm test && npm run build
```

Default pytest never constructs `AsyncOpenAI`. It blocks `DeepSeekTransport.chat`, not the shared `httpx.AsyncClient` (that breaks Instagram mocks). Real Graph tests are opt-in markers.

When changing publish/generation/auth/scheduler: run the matching `tests/test_*.py` plus frontend tests for that page.

---

## Landed integration notes

- Caption on `POST /instagram/publish` is optional, max 2200. Do not regress the multipart contract or JSON result shape.
- MCP logo/product tools still read the business profile and generated images, not only the `products` / `business_assets` tables.
- Scheduled generation names a catalog product. It does not always attach logo bytes to the OpenAI edit call.
- Canva off: OpenAI image generation still runs.
- Default suite (2026-09-24): pytest exit 0 (275 passed, 4 skipped live-Instagram, 2 real OpenAI deselected). Frontend 29 tests and `npm run build` passed.
- Do not commit `.env`. Do not start Windows Postgres automatically or switch to Supabase until asked.

---

## Gotchas (read before you “fix” them)

- **Two Instagram account route modules.** Canonical product routes are `api/platform_routes.py`. `api/instagram_account_routes.py` also exists; don’t fork behavior.
- **`PublicationGateway` vs `PublicationService`.** Service is the session-aware subclass. Scheduler and approve use Service. App state holds Gateway and assigns `runner._publication_gateway`.
- **TaskStore persist.** `save(..., persist=True)` is opt-in and only on COMPLETED/FAILED. Don’t persist from TaskStore while the scheduler session still holds SQLite.
- **SQLite lock.** Scheduler must `commit()` before the Agent opens another connection. Symptom: `database is locked`.
- **Counts vs generation.** Generated/approved/publishing/failed/ambiguous are not published counts.
- **User A vs User B.** Isolation tests in `tests/test_auth.py` / `test_platform_flows.py`. Admins do **not** bypass owner routes.
- **Mocks when no OpenAI key.** UI and pytest work. Live generation needs key **and** model names.
- **Do not automate instagram.com login.** Official Content Publishing API only.
- **Parked (user rule):** do not set up Windows auto-start Postgres or switch to Supabase until asked. Local Postgres via `start-postgres.ps1` if needed.
- **Don’t commit `.env`.** Docker Compose has a dev JWT fallback — replace before shared deploy.

---

## How to think about a change

1. Is this **intelligence** (prompts, plans, images) or **control** (auth, approval, publish, counts)? Control stays in FastAPI/Agent/DB.
2. Does it create a **publication**? Must go through `PublicationGateway` → Instagram Agent tools. No shortcuts.
3. Is it **user-scoped**? `get_owned` / `assert_task_owner`. 404 not 403 for foreign ids.
4. Could it **double-post**? Check daily slot, generated_image_id, AMBIGUOUS policy, unique indexes.
5. Could it **leak a secret**? Response DTO, SSE, logs, frontend types.
6. After UI changes: verify in browser (or tests if no browser tools) on the touched routes **and** related pages that share state.

---

## Quick run (Windows)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# fill JWT_SECRET, TOKEN_ENCRYPTION_KEY, optional OPENAI_*, META_*

uvicorn main:app --reload --host 127.0.0.1 --port 8000

cd frontend
npm install
npm run dev
```

Health: `GET http://127.0.0.1:8000/api/v1/health`
Dashboard: http://localhost:5173
