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
   studio: DeepSeekCreativeClient if DEEPSEEK_API_KEY is set,
           otherwise GroundedCreativeModel (no network)
   scheduler: LLM_PROVIDER (default openai) via ContentAgent
   ↓
OpenAI image generation (or MockImageGenerationProvider)
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

That diagram is the implemented shape, with two planner paths. It is not a single DeepSeek-only reasoning pipeline. See [AI architecture](AI_ARCHITECTURE.md).

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

Only a verified `PUBLISHED` post with an Instagram media id counts. Ambiguous publish results set `skip_publish` and are not retried as a second publish (`agent/recovery.py`).

Formats the agent cannot publish (carousel, reel, story) stay concept-only in `agent/opportunity_engine.py`.

## Runtime wiring

`create_app` in `api/app.py`:

1. Loads `Settings.from_env()`, creates the SQLAlchemy engine, calls `init_db`, bootstraps the admin user.
2. Selects the scheduler LLM: `LLM_PROVIDER=deepseek` uses DeepSeek when the key and `DEEPSEEK_MODEL` are set; otherwise `MockLLMProvider`. Any other provider uses OpenAI when the key and `LLM_MODEL` are set; otherwise the mock.
3. Selects images: OpenAI when `OPENAI_API_KEY` and an image model are set; otherwise `MockImageGenerationProvider`.
4. Resolves vision through `scheduler.integrations.resolve_vision` (`ai.vision.deepseek.get_vision_provider`).
5. Builds `CanvaAdapter`. The client passed into generation is `None` unless `CANVA_ENABLED` is true.
6. Starts `scheduler_loop` only when `SCHEDULER_ENABLED` is true. Default interval is 60 seconds.

`POST /api/v1/generation` does not use that scheduler LLM. It builds a `ContentOrchestrator` whose planner is `get_creative_model`: DeepSeek if `DEEPSEEK_API_KEY` is non-empty, otherwise `GroundedCreativeModel`.

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
