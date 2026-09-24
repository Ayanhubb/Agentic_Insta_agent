# Architecture

Last verified against the working tree on 2026-09-24. Application version reported by FastAPI is `2.0.0` (`api/app.py`). Git HEAD at verification was `6c69ffe`; the tree also contains uncommitted implementation.

Related: [AI architecture](AI_ARCHITECTURE.md), [MCP](MCP.md), [API reference](API/API_REFERENCE.md), [Database](DATABASE/DATABASE.md), [Scheduler](AUTOMATION/SCHEDULER.md).

## What the process actually does

The product is a multi-user Instagram **still-image** studio. React talks to FastAPI. A signed-in user (or the in-process scheduler) asks for content. MCP loads that user's business, brand, product, festival, trend, and account facts. A creative planner writes a plan. OpenAI generates the image when configured. DeepSeek Vision reviews it when a DeepSeek key and vision client are present. A human approves, or automation publishes only when every approval gate passes. The Instagram Agent is the only component that calls Meta create/publish/verify.

```text
React (frontend/)
   ↓
FastAPI (api/app.py, prefix /api/v1)
   ↓
Authentication (JWT, httpOnly cookie access_token, or Bearer)
   ↓
Business / product / brand context (owner-scoped tables)
   ↓
Agents + in-process MCP (backend/mcp/)
   ↓
Creative plan
   LLM_PROVIDER=deepseek (default)
   studio: DeepSeekCreativeClient when DEEPSEEK_API_KEY is set,
           otherwise GroundedCreativeModel (no network)
   scheduler: that same reasoning provider via ContentAgent
   ↓
IMAGE_PROVIDER=openai
   OpenAI image generation when OPENAI_API_KEY and an image model are set,
   otherwise MockImageGenerationProvider
   ↓
DeepSeek Vision QA when a vision client is wired;
otherwise a structural image check
   ↓
Human approval, or automatic approval when gates pass
   ↓
Instagram Agent (six tools)
   ↓
Meta Graph API
```

DeepSeek is the reasoning provider. OpenAI is the image provider. An OpenAI key does not take over planning. Live keys are optional until a real DeepSeek or OpenAI call is requested; startup logs `LLM provider: deepseek` and `Image provider: openai` and does not crash when those keys are empty. See [AI architecture](AI_ARCHITECTURE.md).

## Publishing boundary

| Component | Publishes to Instagram |
| --- | --- |
| Instagram Agent (`agent/agent.py`) | Yes. Tools: `validate_image`, `prepare_image`, `upload_image`, `create_instagram_media`, `publish_instagram_media`, `verify_publication` |
| MCP allowlist (`backend/mcp/permissions.py`) | No. Publish aliases raise `TOOL_NOT_ALLOWED` |
| DeepSeek (`ai/llm/deepseek.py`) | No |
| OpenAI chat and images | No |
| Canva adapter (`backend/integrations/canva/`) | No |
| Content orchestrator and opportunity engine | No. Results set `published=False` |
| Festival catalog and festival MCP | No |

`enqueue_instagram_publication` in `agent/instagram_tasks.py` is the handoff into the agent. It does not call Graph itself.

Publishing credentials are the signed-in user's encrypted Instagram token:

```text
Authenticated user
  → connected Instagram account (instagram_accounts)
  → encrypted token decrypted for that request only
  → Instagram Agent
  → Meta Graph API
```

`META_ACCESS_TOKEN` and `INSTAGRAM_ACCOUNT_ID` are not that path. Production, staging, and the default (`APP_ENV` unset) never use them to publish. A development-only compatibility path exists when `APP_ENV` is `development`, `dev`, or `local` and `INSTAGRAM_LEGACY_ENV_FALLBACK=true`. The scheduler does not use it. Automatic publishing fails with `INSTAGRAM_NOT_CONNECTED` when the business has no connected account. The token is not returned through the API, MCP, model providers, React, or logs.

Only a verified `PUBLISHED` post with an Instagram media id counts. Ambiguous publish results set `skip_publish` and are not retried as a second publish (`agent/recovery.py`).

Formats the agent cannot publish (carousel, reel, story) stay concept-only in `agent/opportunity_engine.py`.

## Runtime wiring

`create_app` in `api/app.py`:

1. Loads `Settings.from_env()`, creates the SQLAlchemy engine, calls `init_db`, bootstraps the admin user.
2. Selects the reasoning provider from `LLM_PROVIDER` (default `deepseek`). DeepSeek is used when the key and `DEEPSEEK_MODEL` are set. A missing key uses `MockLLMProvider` and does not switch to OpenAI. `LLM_PROVIDER=openai` is an explicit opt-in.
3. Selects images from `IMAGE_PROVIDER` (default `openai`). OpenAI is used when `OPENAI_API_KEY` and an image model are set; otherwise `MockImageGenerationProvider`. DeepSeek is not an image provider.
4. Resolves vision through `scheduler.integrations.resolve_vision` (`ai.vision.deepseek.get_vision_provider`).
5. Builds `CanvaAdapter`. The client passed into generation is `None` unless `CANVA_ENABLED` is true.
6. Starts `scheduler_loop` only when `SCHEDULER_ENABLED` is true. Default interval is 60 seconds.

`POST /api/v1/generation` builds a `ContentOrchestrator` whose planner is `get_creative_model`: DeepSeek when `LLM_PROVIDER=deepseek` and `DEEPSEEK_API_KEY` is non-empty, otherwise `GroundedCreativeModel`. The scheduler `ContentAgent` and the trend stage use the reasoning provider from `select_reasoning_provider`. The trend stage does not open its own DeepSeek HTTP client. Instagram publishing stays on the Instagram Agent and Meta.

## Major packages

| Path | Role |
| --- | --- |
| `api/` | FastAPI routes |
| `auth/` | Passwords, JWT, session checks |
| `agent/` | Instagram Agent, content agent, orchestrator, opportunity engine |
| `ai/` | OpenAI LLM, OpenAI images, DeepSeek text and vision |
| `backend/mcp/` | In-process MCP registry and tools |
| `backend/ai/` | Re-exports plus the OpenAI image edit provider |
| `backend/trends/` | Research fetch, analyst, packet |
| `backend/integrations/canva/` | Optional Canva OAuth and remote MCP |
| `scheduler/` | Daily, festival, and trend ticks |
| `festivals/` | India catalog, campaign rules, scheduler festival MCP |
| `db/` | SQLAlchemy models, repositories, Alembic |
| `services/` | Graph client, media storage, publication, Instagram reader |
| `frontend/` | React dashboard |

## Not this product

- Reels, stories, and carousels are not published.
- There is no second Meta publisher under `backend/`.
- `LLMPlanner` in `agent/planner.py` is a stub and is not the production planner.
- `api/instagram_account_routes.py` is not mounted. Live connect routes are on `api/platform_routes.py`.
