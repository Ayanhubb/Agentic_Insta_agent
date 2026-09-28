# Architecture decisions

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Concept](AGENT_PLATFORM_CONCEPT.md), [Migration](AGENT_PLATFORM_MIGRATION_PLAN.md).

Decisions below are accepted for this proposal. They are not implemented except where the decision says the current code already is the decision. Rejected alternatives are listed after the decisions.

Inspection: 2026-09-25, HEAD `a63569e`. `brain.md` is absent. Product facts used here come from `md/ARCHITECTURE.md` and the code it names.

## ADR-001 — The studio is the first reference agent

**Status:** accepted.

The running product stays the Instagram still-image studio. Its platform id is `instagram_marketing`. Future agents are additional registrations. They are not a rewrite of this one.

**Current code:** `InstagramAgent`, `ContentOrchestrator`, `ContentAgent`, trend scheduler, MCP, providers, and the React studio.

## ADR-002 — One Instagram publisher

**Status:** accepted. **IMPLEMENTED** in code.

Only `InstagramAgent` calls Meta create, publish, and verify. Every other component stops at `enqueue_instagram_publication` or does not publish. The platform dispatcher must not construct a Graph client.

## ADR-003 — The generic runtime does not contain Instagram rules

**Status:** accepted. **PROPOSED** dispatcher. **IMPLEMENTED** specialized runtimes.

`dispatch` selects a catalog callable. Instagram tool order, `TaskStatus` steps, Graph host checks, recovery, and `AgentState.platform == "instagram"` stay inside `agent/` and `tools/`. The dispatcher does not grow a copy of that state machine.

## ADR-004 — Workflows are descriptions of existing call graphs

**Status:** accepted.

Workflow ids name `studio.generate`, `studio.approve`, `studio.regenerate`, `direct.publish`, `scheduler.campaign`, `trend.cycle`, and `instagram.publish`. The first engine calls the functions that already implement those graphs. A visual builder that inserts arbitrary nodes is not part of the design.

## ADR-005 — Publish is one closed workflow step

**Status:** accepted.

From the platform, `instagram.publish` is a single step implemented by `InstagramAgent` and `DeterministicPlanner`. The six tools are not reorderable by a model, by MCP, or by user configuration. `LLMPlanner` stays a disabled stub.

## ADR-006 — Providers are roles selected by environment

**Status:** accepted. **IMPLEMENTED** factories. **PLANNED** registry facade.

Roles are scheduler reasoning, studio reasoning, trend reasoning (same object as scheduler reasoning), image generate, image edit, and vision QA. Defaults remain DeepSeek for reasoning and vision, OpenAI for images. `LLM_PROVIDER=openai` stays an explicit opt-in. Empty keys still boot the process. Canva is creative export, not a model provider.

## ADR-007 — MCP is an allowlisted read plane

**Status:** accepted. **IMPLEMENTED.**

`MCP_TOOL_ALLOWLIST` is the model-facing tool set. Handlers are read-only. Tenant arguments from the model are stripped. Publish names raise `TOOL_NOT_ALLOWED` before a handler runs. Canva and `FestivalMcp` stay off that allowlist.

Tools are registered Python handlers. The model does not submit code.

## ADR-008 — Secrets stay on the server

**Status:** accepted. **IMPLEMENTED.**

Provider keys are process environment variables. Instagram and Canva tokens are Fernet columns decrypted in the component that calls the vendor. Prompts, MCP payloads, logs, and `GET /ai/status` do not receive raw secrets. The platform does not add per-tenant provider keys.

## ADR-009 — Tenant is the user

**Status:** accepted. **IMPLEMENTED.**

`users.id` is the tenant. `TenantContext` sources are `authenticated`, `scheduler`, and `system`. There is no organization table. New user-owned rows use `user_id` and owner-scoped repositories. Foreign ids return 404. Cross-user Instagram tokens return `PERMISSION_ERROR`.

## ADR-010 — Approval stays a named policy in front of the gateway

**Status:** accepted.

`decide_approval` is policy `instagram.still_image`. Automatic publish still requires the existing flags and still fails closed on the existing gates, including `tenant_ownership_failed`. `publish=true` only enqueues the Instagram agent. User configuration may toggle flags that exist on `automation_settings`. It may not remove a gate.

## ADR-011 — Knowledge and assets are the current stores

**Status:** accepted.

Knowledge bindings name business, brand, product, festival, account, trend, and content rules. Assets stay `business_assets` and `product_assets` with the current media paths. No retrieval index is added. Missing logos stay `found: false`.

## ADR-012 — Triggers are the current entry points

**Status:** accepted.

HTTP generation, approve, direct publish, run-now, and the in-process scheduler loop are the triggers. `SCHEDULER_ENABLED` remains the process switch. `scheduled_jobs` stays a table the in-process runner does not need an external queue to use. `daily_posts_per_day` stays forced to 1.

## ADR-013 — Observability uses the current channels

**Status:** accepted.

`log_step`, redaction, MCP audit lines, `pipeline_metrics`, `agent_events`, and the task SSE stream are the observability stack. No tracing vendor is introduced. The HTTP error field `"platform": "instagram"` stays until a new agent has its own routes.

## ADR-014 — Migration is additive

**Status:** accepted.

Phase 0 is documentation. Phase 1 is a catalog. Phase 2 is a dispatcher that calls existing functions. Phase 3 may add nullable columns. User-facing builder APIs and a second agent wait until the reference registration is behavior-neutral. See [migration plan](AGENT_PLATFORM_MIGRATION_PLAN.md).

## ADR-015 — Partial and unimplemented pieces stay that way

**Status:** accepted.

The platform does not complete `LLMPlanner`, `SemanticSimilarityBackend`, `SessionContextStore`, the unmounted Instagram account router, OpenAI vision, DeepSeek image generation, or DeepSeek browsing. It does not turn the development `META_ACCESS_TOKEN` fallback into the production credential.

## Rejected alternatives

### Rewrite the studio as a generic graph

Rejected. The publish recovery rules, certainty model, and six-tool registry are the product. A rewrite risks a second publisher and a second retry path.

### Let the model call tools that publish

Rejected. `LLMPlanner` would propose tools from `ALLOWED_TOOL_SET`, which includes `publish_instagram_media`. MCP already returns `TOOL_NOT_ALLOWED` for those names. The control plane executes handlers. The model returns validated JSON.

### Register Instagram tools on MCP "so the platform is uniform"

Rejected. Uniformity would put publish on the same client the model-facing allowlist uses. The planes stay split: `mcp_read`, `instagram_publish`, `canva_remote`, `festival_scheduler`.

### A platform Graph client beside `services/instagram_client.py`

Rejected. One client, owned by the Instagram agent tools.

### Hard-code DeepSeek or OpenAI in the dispatcher

Rejected. Factories and env vars already select implementations. Mocks and `GroundedCreativeModel` must remain valid implementations so startup and tests keep working.

### Per-user provider API keys

Rejected for this proposal. The schema has no key columns. Sharing process-level keys is the current trust model. A bring-your-own-key design would be a separate security review.

### User-supplied Python, shell, or HTTP tools

Rejected. `creative_validation` already forbids command, code, script, and URL-shaped plan fields. New tools are reviewed handlers.

### Model-supplied tenant id

Rejected. `strip_model_tenant_arguments` exists because that input is untrusted.

### Organization tenants

Rejected. No org table exists. Adding one is a product change, not a platform extraction.

### External workflow workers or a new queue

Rejected as part of this extraction. The scheduler loop and `POST /automation/run-now` are the runners. An external queue would change delivery and retry semantics that `RecoveryPolicy` already defines for publish.

### Visual node editor as the workflow model

Rejected as the definition of a workflow. Optional steps that already exist (Canva, auto-publish flags) can be forms over current APIs. Free-form graphs would let a user attach a publish node.

### Merge `FestivalMcp` into `MCP_TOOL_ALLOWLIST` for consistency

Rejected. Those tools are a scheduler adapter. The model-facing list is the 22-tool allowlist. Changing it changes the prompt surface.

### Treat trend recommendations as publishable facts

Rejected. The analyst contract separates OBSERVED, INFERRED, and RECOMMENDED. `published_because_trend_only` stays false. Auto-publish flags still apply.

### Publish reels, stories, or carousels to make the agent "general"

Rejected. The opportunity engine marks those formats concept-only. The agent publishes one still image.

### Enable the legacy `META_ACCESS_TOKEN` path for the scheduler

Rejected. Production, staging, and unset `APP_ENV` keep the fallback off. Automatic publishing without a connected account stays `INSTAGRAM_NOT_CONNECTED`.

### Replace owner checks with 403 for foreign ids

Rejected. The API returns 404 for another user's rows. The platform keeps that behavior so existence is not disclosed.

### Add a vector knowledge base

Rejected. Context is MCP reads over owner-scoped tables and the festival catalog. No embedding store exists.

### Second admin user list or second Instagram connect router as "platform APIs"

Rejected. The live admin list is the auth router. The live connect routes are `api/platform_routes.py`. Duplicates stay unused.

## Decision log

| ID | Topic | Implementation status |
| --- | --- | --- |
| ADR-001 | Reference agent id `instagram_marketing` | **PROPOSED** name, **IMPLEMENTED** behavior |
| ADR-002 | Single publisher | **IMPLEMENTED** |
| ADR-003 | Dispatcher free of Instagram rules | **PROPOSED** |
| ADR-004 | Workflow ids for current pipelines | **PROPOSED** names, **IMPLEMENTED** pipelines |
| ADR-005 | Closed publish sequence | **IMPLEMENTED** |
| ADR-006 | Provider roles | **IMPLEMENTED** selection, **PLANNED** facade |
| ADR-007 | MCP read plane | **IMPLEMENTED** |
| ADR-008 | Server-side secrets | **IMPLEMENTED** |
| ADR-009 | User tenant | **IMPLEMENTED** |
| ADR-010 | Approval policy id | **IMPLEMENTED** function, **PROPOSED** id |
| ADR-011 | Existing knowledge and assets | **IMPLEMENTED** |
| ADR-012 | Existing triggers | **IMPLEMENTED** |
| ADR-013 | Existing observability | **IMPLEMENTED** |
| ADR-014 | Additive migration | **PLANNED** after phase 0 |
| ADR-015 | Leave partial features partial | **IMPLEMENTED** as current gaps |
| ADR-016 | One canonical implementation per platform concern | **ACCEPTED** 2026-09-28. See [canonical components](PLATFORM_CANONICAL_COMPONENTS.md). Duplicates stay in the tree until a later cleanup |
