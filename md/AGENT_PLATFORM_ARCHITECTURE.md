# Agent platform architecture

> **Status on 2026-09-28.** The studio path below is the production process and is not being replaced. Agents, versions, and executions are in Alembic `007_platform_agents`. Shadow mode can run `instagram.reference` and cannot publish. One canonical implementation for each platform concern is fixed in [PLATFORM_CANONICAL_COMPONENTS.md](PLATFORM_CANONICAL_COMPONENTS.md). Duplicates are listed in [PLATFORM_DUPLICATE_AUDIT.md](PLATFORM_DUPLICATE_AUDIT.md) and are not deleted yet. Webhooks and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Concept](AGENT_PLATFORM_CONCEPT.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Runtime](AGENT_PLATFORM_RUNTIME.md), [MCP](AGENT_PLATFORM_MCP.md), [Providers](AGENT_PLATFORM_PROVIDER_MODEL.md), [Security](AGENT_PLATFORM_SECURITY.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md), [Migration](AGENT_PLATFORM_MIGRATION_PLAN.md), [Decisions](AGENT_PLATFORM_DECISIONS.md).

Inspection date: 2026-09-25. HEAD `a63569e`. This page describes boundaries. It does not add routes or tables.

## Process that exists today

**IMPLEMENTED.** `create_app` in `api/app.py` loads `Settings.from_env()`, creates the SQLAlchemy engine, calls `init_db`, bootstraps the admin user, selects reasoning and image providers, resolves vision, builds `CanvaAdapter` (client is `None` unless `CANVA_ENABLED`), and starts `scheduler_loop` only when `SCHEDULER_ENABLED` is true.

```text
React (frontend/)
   ↓
FastAPI /api/v1
   ↓
JWT cookie or Bearer, sessions row, require_password_ok
   ↓
Owner-scoped repositories (tenant = users.id)
   ↓
Content orchestrator | Content agent | Trend scheduler | Instagram Agent
   ↓
In-process MCP reads, provider calls, optional Canva export
   ↓
Approval
   ↓
enqueue_instagram_publication
   ↓
Instagram Agent six tools
   ↓
Meta Graph API
```

`DATABASE_URL` empty defaults to SQLite `data/agentic.db`. PostgreSQL is a supported URL. There is no external job queue. `scheduled_jobs` stores per-user rows. The runner does not depend on one.

## Canonical components

Chosen 2026-09-28. The studio functions in the diagram stay the live publishers and generators.

| Concern | Use this | Do not add another |
| --- | --- | --- |
| Agent API | `/api/v1/agents` (`AgentBuilderService`) | `/api/v1/platform/agents` is the unused duplicate |
| Agent store | `agent_runtime.catalog.InMemoryCatalog`, with SQL in `agents`, `agent_versions`, `agent_executions` | `AgentBuilderStore` |
| Workflow engine | `agent_runtime.engine.WorkflowEngine` | `agent_platform.workflow.engine.WorkflowEngine` |
| Tool grants | `backend.mcp` `MCPRegistry` | `PlatformToolRegistry` |
| Publish tools | `agent/registry.py` inside `InstagramAgent` only | MCP, Canva, DeepSeek, OpenAI, the scheduler, the runtime |
| Policy | `decide_approval` through `InstagramStillImagePolicy` | `agent_platform.policy.PolicyEngine` |
| Platform runtime | `agent_runtime.AgentRuntime` | A runtime that calls `media_publish` |
| Providers | `ai.model_registry`, which calls the existing factories | A second image or reasoning client |
| Scheduler | `AutomationRunner` | `TriggerScheduler` |

`InstagramAgent` remains the only Meta publisher. The generic runtime orchestrates. It does not call Graph.

## Two planes

**PROPOSED** names for a split the code already enforces.

### Control plane

FastAPI routes, `auth/`, repositories, `TenantContext`, MCP client checks, `decide_approval`, `PublicationService`, provider factories, and `InstagramAgent`. This plane authenticates the caller, chooses the tenant, loads secrets, validates schemas, and decides whether a side effect may run.

### Capability plane

Allowlisted MCP handlers, image and vision provider implementations, the Canva adapter, and the six Instagram tools. A capability runs only after the control plane has authorized the call. Handlers do not accept a tenant id from the model. They do not accept a raw access token in arguments.

React is a client of the control plane. It is not a capability. It does not hold provider keys or Instagram tokens. Product docs already forbid `VITE_` / `NEXT_PUBLIC_` provider keys.

## Layers and where they live now

| Layer | Status | Current home | Platform home |
| --- | --- | --- | --- |
| HTTP and session | **IMPLEMENTED** | `api/`, `auth/` | Unchanged. Future agent routes mount beside these, they do not replace them |
| Agent configuration | **PROPOSED** | Python classes only | A system record for the Instagram reference agent, then optional per-user config that points at registered workflows |
| Workflow | **IMPLEMENTED** as Python call order. **PROPOSED** as data | `ContentOrchestrator`, `CampaignPipeline`, `InstagramAgent._run_inner`, `TrendIntelligenceScheduler` | Workflow ids dispatched to those functions |
| Runtime | **IMPLEMENTED** specialized. **PROPOSED** generic dispatcher | `InstagramAgent`, orchestrator, pipeline | Dispatcher calls the specialized runtimes. It does not reimplement them |
| Model providers | **IMPLEMENTED** factories. **PLANNED** registry view | `ai/llm_client.py`, `ai/image_generator.py`, `ai/creative_model.py`, `ai/vision/deepseek.py` | Role → implementation, still selected by env |
| MCP / tools | **IMPLEMENTED** | `backend/mcp/`, `agent/registry.py`, `festivals/mcp.py`, `backend/integrations/canva/` | One catalog with separate allowlists. See [MCP](AGENT_PLATFORM_MCP.md) |
| Knowledge | **IMPLEMENTED** | Business, brand, product, festival, trend, account tables read through MCP | Named sources. No new store |
| Assets | **IMPLEMENTED** | `business_assets`, `product_assets`, `services/media_storage.py` | Same rows. Workflows receive asset ids, not paths in model prompts |
| Triggers | **IMPLEMENTED** | Generation and publish routes, `POST /automation/run-now`, `AutomationRunner` | Bindings from an agent to those entry points |
| Approval | **IMPLEMENTED** | `decide_approval`, approve/reject routes | Policy id on the Instagram workflows |
| Tasks | **IMPLEMENTED** | `agent_tasks`, `agent_events`, `GET /tasks/{id}` | Same tables. A later column may name the agent. See [runtime](AGENT_PLATFORM_RUNTIME.md) |
| Observability | **IMPLEMENTED** | `log_step`, MCP audit line, `pipeline_metrics`, SSE | Same channels, tagged with tenant and task id |
| Tenancy | **IMPLEMENTED** | `users.id` on owner-scoped tables | Unchanged |
| Reference agent | **IMPLEMENTED** behavior | Packages in the table below | Registered id `instagram_marketing` |

## Package map (implemented)

| Path | Role |
| --- | --- |
| `api/` | Routes. Prefix `/api/v1` |
| `auth/` | Passwords, JWT, session checks, bootstrap admin |
| `agent/` | Instagram Agent, content agent, orchestrator, opportunity engine, creative validation |
| `ai/` | LLM interface, OpenAI chat, DeepSeek text, DeepSeek vision, image factory, mocks |
| `backend/mcp/` | In-process registry, client, tenant strip, 22 tools |
| `backend/ai/` | Re-exports and OpenAI image edit provider |
| `backend/trends/` | Research fetch, analyst, packet |
| `backend/integrations/canva/` | Optional OAuth and remote MCP. Creative export only |
| `scheduler/` | Daily, festival, trend ticks, approval policy, campaign pipeline |
| `festivals/` | India catalog, campaign rules, scheduler festival adapter |
| `db/` | SQLAlchemy models, repositories, Alembic, Fernet helper |
| `services/` | Graph client, publication, media storage, Instagram reader, logging, metrics |
| `tools/` | The six Instagram agent tools plus image helpers |
| `frontend/` | React studio: dashboard, generate, images, posts, automation, festivals, business, brand, products, assets, Instagram connect, trends |

`api/instagram_account_routes.py` is not mounted. Live connect routes are on `api/platform_routes.py`. Status in the API reference: **NOT IMPLEMENTED** as a live router. The platform does not revive it as a second connect API.

`GET /api/v1/admin/users` is registered twice. The auth router is mounted first, so its handler is the one that runs. Status: **PARTIAL**. Unrelated to the agent platform.

## Knowledge and context

**IMPLEMENTED** sources the studio already loads. A model sees sanitized MCP payloads, not ciphertext and not filesystem paths (`sanitize_payload` drops path keys and secret-like keys).

| Source | Storage | How a model receives it |
| --- | --- | --- |
| Business profile | `business_profiles` | `get_business_profile` |
| Brand guidelines | `brand_profiles`, `brand_guidelines` | `get_brand_guidelines` |
| Products and offers | `products` | `get_product`, `get_active_offers` |
| Logo and product files | `business_assets`, `product_assets` | `get_company_logo`, `get_product_image`. Bytes go to the image edit path, not into the prompt as a secret |
| Festivals | Code catalog `festivals/data/lunar_dates.json` plus `festival_campaigns` | `get_upcoming_festivals`, `get_festival_details`. Scheduler-only extra tools live on `FestivalMcp`, not on the MCP allowlist |
| Content rules | `automation_settings` plus static rules | `get_content_rules` reports `publishing.available: false` |
| Account facts | Snapshots and live Graph GET via the user's token | Account MCP tools. Tenant comes from `TenantContext` |
| Trends | `trend_sources`, `trend_observations`, `trend_evidence`, `trend_reports`, `content_opportunities` | Trend MCP tools read stored observations. They do not fetch URLs at tool time |

**PROPOSED.** A knowledge binding is a name from this list attached to an agent. The Instagram reference agent binds all of them. A future agent binds a subset. There is no embedding index, document corpus, or retrieval service in the tree, so none is part of this design.

Context assembly stays server-side. `TrendIntelligence` in `backend/trends/packet.py` is the pattern: the server calls a fixed tool list and builds a packet of OBSERVED, INFERRED, and RECOMMENDED material. The analyst does not browse.

`SessionContextStore.get_festival_context` always returns `None`. `save_content_task` does not persist. Status in the content agent doc: **PARTIAL**. The platform does not pretend session memory exists.

## Assets

**IMPLEMENTED.**

- Upload routes under `/api/v1/assets`, `/api/v1/products/{id}/images`, and `/api/v1/brand/assets`.
- Owner check before read or delete. A foreign asset id is denied (`ASSET_ACCESS_DENIED` on MCP, 404 on HTTP).
- Public `GET /api/v1/media/{filename}` exists so Meta can fetch a prepared image. Generated images and brand files are not on that public path.
- Production logos and product images are not uploaded yet. A missing file returns `found: false`. The image provider must not invent a logo.

**PROPOSED.** Workflow steps refer to asset ids and roles (`LOGO`, product image). Resolution stays in `services/asset_resolution.py` and the MCP asset tools. The platform does not add a second blob store.

## Triggers

**IMPLEMENTED** entry points:

| Trigger | What runs |
| --- | --- |
| `POST /api/v1/generation` | `ContentOrchestrator` for the session user |
| `POST /api/v1/generation/{image_id}/approve` | Approval, then `PublicationService` |
| `POST /api/v1/instagram/publish` | Instagram Agent on an uploaded JPEG/PNG |
| `POST /api/v1/automation/run-now` | One `AutomationRunner.tick` for the session user |
| `scheduler_loop` | Daily, then festival, then trend, when `SCHEDULER_ENABLED` is true |
| Trend `_maybe_generate` | Campaign pipeline only when the lane's auto-publish flag is already on |

`automation_settings` holds `daily_enabled`, `daily_post_time`, `festival_enabled`, `auto_daily_publish`, `auto_festival_publish`, `timezone` (default `Asia/Kolkata`), and related festival fields. If `daily_posts_per_day` is sent, the handler forces it to 1.

**PROPOSED.** An agent trigger binding names one of these entry points and the settings row that already gates it. The design does not add webhooks, cron expressions beyond `daily_post_time`, or a new queue. `SCHEDULER_ENABLED` remains the process switch. Docker Compose turns the scheduler on for the backend service. Local `.env.example` leaves it false.

## How a request crosses the platform

**PROPOSED** sequence, using only current functions:

1. Authenticate. Build `TenantContext` from `user.id` (or `scheduler` when the loop runs for that user).
2. Resolve the agent definition. Until user-defined agents exist, the only definition is `instagram_marketing`.
3. Resolve the workflow id for that trigger (`studio.generate`, `studio.approve`, `scheduler.campaign`, `instagram.publish`, `trend.cycle`).
4. Dispatch to the existing function. Provider calls use the factories in `create_app`.
5. If the workflow's approval policy returns `publish=true`, call `enqueue_instagram_publication`.
6. Persist `agent_tasks` / `agent_events` as today. Emit the existing SSE stream for publish tasks.

Step 3 is a name. It is not a new interpreter. See [workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md) and [migration](AGENT_PLATFORM_MIGRATION_PLAN.md).

## Multi-tenancy

**IMPLEMENTED.** The tenant id is `users.id`. Routes pass the session user into repositories. A miss is 404. MCP `trusted_tenant` is built on the server. Instagram publish resolves `instagram_accounts` for that user only. User A cannot publish with user B's token (`PERMISSION_ERROR`).

`canva_connections` stores both `tenant_id` and `user_id`. Elsewhere the tenant column is `user_id`. **PROPOSED:** keep that. Do not introduce a separate organization tenant. The product has no org table.

The development-only `META_ACCESS_TOKEN` path is not a tenant and is not a platform credential. It stays behind `INSTAGRAM_LEGACY_ENV_FALLBACK` and `APP_ENV` in `development`, `dev`, or `local`. The scheduler does not use it. Unset `APP_ENV` is `production`.

## Frontend

**IMPLEMENTED.** React pages are the Instagram studio. A separate builder at `/agents` edits user-owned configuration and shows `instagram_marketing` as read-only. See [frontend](AGENT_PLATFORM_FRONTEND.md).

The studio UI remains the interface of the reference agent. The builder is not required to register that agent. See [decisions](AGENT_PLATFORM_DECISIONS.md).

## Out of this architecture

- A second process for MCP. In-process MCP is the control-plane tool plane. Canva is the only remote MCP, and it stays off `MCP_TOOL_ALLOWLIST`.
- A generic state machine that replaces `TaskStatus` values `validating` through `verifying`. Those states belong to the Instagram publish runtime.
- Per-user provider API keys. Keys are process environment variables. They are not columns.
