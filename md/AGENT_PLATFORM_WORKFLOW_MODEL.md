# Workflow model

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Architecture](AGENT_PLATFORM_ARCHITECTURE.md), [Runtime](AGENT_PLATFORM_RUNTIME.md), [Workflow engine](AGENT_PLATFORM_WORKFLOW_ENGINE.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md), [MCP](AGENT_PLATFORM_MCP.md).

A workflow is an ordered list of steps the control plane already runs. The list is a description. The engine is a dispatcher to existing functions. It is not a scripting language, and it is not a place where a model inserts a step at runtime.

## Definition

**PROPOSED** record:

| Field | Meaning |
| --- | --- |
| `id` | Stable string, unique in the process catalog |
| `agent_id` | Owning agent. The first catalog is owned by `instagram_marketing` |
| `steps` | Ordered step ids |
| `output_schema` | Pydantic model the step already validates, when the step calls a model |
| `approval_policy` | Policy id, or empty when the workflow itself is the publish runtime |
| `side_effects` | Declared classes: `none`, `local_write`, `provider_call`, `instagram_publish` |

A step record:

| Field | Meaning |
| --- | --- |
| `id` | Stable within the workflow |
| `handler` | Python callable path that exists today |
| `tool_allowlist` | Subset of a registry, or empty when the step does not call tools |
| `reads` | Knowledge source names |
| `writes` | Tables the handler already writes |

**PLANNED.** The catalog is a Python module of these records, imported by a dispatcher that calls the handler. **PROPOSED** later: rows in the database so a user can enable or disable optional steps that are already in the catalog (Canva). Users do not insert handler paths.

## Step kinds that exist

These kinds are the only ones the catalog may use. A new kind requires a new handler in code.

| Kind | Side effect | Handler today |
| --- | --- | --- |
| `load_context` | none | `MCPClient.invoke` on allowlisted read tools |
| `reason` | provider_call | `get_creative_model` or `LLMProvider.generate_content_plan` or the trend analyst |
| `validate_plan` | none | `agent/creative_validation.py` |
| `render_image` | provider_call, local_write | OpenAI generate, or OpenAI edit when MCP loaded logo or product bytes |
| `export_creative` | provider_call, local_write | `CanvaAdapter.produce` when the plan action is `apply_template` or `use_reference` |
| `vision_qa` | provider_call | `DeepSeekVisionProvider`, or the structural Pillow check |
| `store_pending` | local_write | generated image row, `approval_status` pending, `published` false |
| `decide_approval` | none | `scheduler/approval_policy.py` `decide_approval` |
| `enqueue_publish` | instagram_publish | `enqueue_instagram_publication` only |
| `instagram_tool_sequence` | instagram_publish | `InstagramAgent.run` with `DeterministicPlanner` |
| `collect_trends` | local_write | `TrendResearcher.collect` and the trend scheduler's save path |
| `human_decision` | local_write | `POST /generation/{image_id}/approve` or `reject` |

`instagram_tool_sequence` is one step from the platform's point of view. Inside it, the six tools stay ordered by `DeterministicPlanner`. The workflow engine does not schedule `publish_instagram_media` as a free step.

## Catalog for the reference agent

**PROPOSED** names for **IMPLEMENTED** call order.

### `studio.generate`

Owner: `ContentOrchestrator`.

1. `load_context` — `get_business_profile` is required. Products, offers, brand, festival. Canva query only when the request sets `use_canva`.
2. `reason` — `CreativePlan`. DeepSeek when `LLM_PROVIDER=deepseek` and `DEEPSEEK_API_KEY` is set. Otherwise `GroundedCreativeModel` (local template, no HTTP).
3. `validate_plan` — reject publish tools, credential fields, and facts that are not in the MCP packet.
4. `export_creative` or `render_image` — Canva when `canva_action` is `apply_template` or `use_reference`. Otherwise OpenAI. A request that asks for Canva when the user is not connected returns `CANVA_NOT_CONNECTED` and does not call OpenAI.
5. `vision_qa` — same check for Canva bytes and OpenAI bytes.
6. `store_pending` — `published=False`, `handoff_to_instagram=False`.

The orchestrator does not call the Instagram Agent.

### `studio.approve`

1. `human_decision` — owner-scoped image. Reject stops.
2. `enqueue_publish` — `PublicationService` on approve.

### `studio.regenerate`

Same as `studio.generate` for a new image. `published` stays false. **IMPLEMENTED** as `POST /generation/{image_id}/regenerate`.

### `direct.publish`

`POST /api/v1/instagram/publish`. Multipart JPEG or PNG. This workflow is `instagram_tool_sequence` only. It does not call the creative model. Query `wait=false` returns 202.

### `scheduler.campaign`

Owner: `CampaignPipeline`.

1. `reason` — `ContentAgent.create_plan` / `run` via `select_reasoning_provider`.
2. Optional `export_creative` when the plan sets `canva_action`.
3. `render_image` when Canva is not the renderer. Logo and product references use the same MCP resolver as the studio before any OpenAI edit.
4. `vision_qa`.
5. `decide_approval`.
6. `enqueue_publish` only when `ApprovalDecision.publish` is true.

Content planning, Canva, DeepSeek, OpenAI, and MCP do not publish. The scheduler does not use `META_ACCESS_TOKEN`.

### `trend.cycle`

Owner: `TrendIntelligenceScheduler`, after daily and festival work in the same tick.

1. Expire opportunities past `expires_at`.
2. Skip when the local post time has not arrived or a report for the day already exists.
3. `load_context` — account MCP tools inside `collect_mcp_evidence`.
4. `collect_trends` — `TrendResearcher.collect` into observations and evidence.
5. `reason` — `DeepSeekTrendAnalyst` over the packet. The trend stage does not open its own DeepSeek HTTP client. It uses the reasoning provider from `select_reasoning_provider`.
6. Write `trend_reports` and `content_opportunities`.
7. Optional `scheduler.campaign` when the lane's auto-publish flag is already on. A festival opportunity also needs `auto_festival_publish` and a catalog festival. The result records `published_because_trend_only: false`.

Trend tick failures return `{status: failed, reason: trend_run_failed}` and do not roll back the daily and festival work already done in that tick.

### `instagram.publish`

Owner: `InstagramAgent`. Closed sequence:

`validate_image` → `prepare_image` → `upload_image` → `create_instagram_media` → `publish_instagram_media` → `verify_publication`.

`enqueue_instagram_publication` creates this run. It does not call Graph itself.

Recovery is part of this workflow, not a platform-wide policy:

- Retry rate limits, storage, container creation, and verification inside the configured caps.
- If publish returns unknown certainty or times out, set `skip_publish` and do not publish again until verification.
- Do not publish again when certainty is `SUCCEEDED` or a media id already exists.

Only a verified `PUBLISHED` post with an Instagram media id counts.

## Workflow engine

**IMPLEMENTED** for generic, registered capabilities only. See [workflow engine](AGENT_PLATFORM_WORKFLOW_ENGINE.md). That engine is not wired to Instagram routes, and it does not call `enqueue_instagram_publication`. The reference workflows still run as the functions named above.

**PLANNED** shape for the reference agent, unchanged:

```text
trigger
  → lookup workflow id on the agent
  → for each step: call handler(tenant, input)
  → pass the handler's typed result to the next step
  → stop on AppError
```

The engine does not:

- parse a user expression
- let the model append a tool
- import a tool by a string that is not in the catalog
- call `media_publish` itself
- branch on `platform == "instagram"` as a generic rule. The Instagram checks stay inside `DeterministicPlanner` and `AgentState`

**PROPOSED** workflow builder:

- A builder UI is a form over the catalog: enable Canva, set the existing automation flags, pick a product.
- It is not a graph editor that adds nodes.
- It is not in the React app today.

The first engine can be a dictionary from workflow id to the function that already implements the whole pipeline (`ContentOrchestrator`, `CampaignPipeline`, `InstagramAgent.run`, trend tick). Splitting those functions into separately addressable steps is **PLANNED** only where the function already has that structure. It is not a rewrite.

## Validation

**IMPLEMENTED** for creative plans: `creative_validation` rejects plans that select a publish tool or carry credentials, and rejects prices, offers, and dates that are not in the MCP context.

**IMPLEMENTED** for MCP arguments: object schema, required keys, types, string lengths, integer ranges, enums, `additionalProperties: false`.

**IMPLEMENTED** for provider JSON: `response_format: {"type": "json_object"}` and Pydantic validation. Invalid JSON is retried up to `LLM_MAX_ATTEMPTS` (default 3).

**PROPOSED.** The workflow engine treats a failed validation as a failed step. It does not ask the model to "try a different tool". Studio and scheduler already cap plan retries. Those caps stay on the handlers.

## Data the workflow may write

From the schema doc, the pipelines already write:

- `generated_images`
- `instagram_posts`
- `agent_tasks`, `agent_events`
- `daily_post_slots`
- `festival_campaigns`, `festival_posts`
- `scheduled_jobs` (rows exist; the runner is in-process)
- `trend_observations`, `trend_evidence`, `trend_reports`, `content_opportunities`
- `account_snapshots`, `media_snapshots`, `insight_snapshots`, `instagram_intelligence_records`

A workflow description lists those tables. It does not gain new tables in this proposal. Agent and workflow definition tables are **PLANNED** in the migration plan and stay empty of behavior until a later phase.

## Formats

`instagram.publish` accepts one still image. Caption text on the upload route is at most 2200 characters. Carousel, reel, and story stay concept-only in `agent/opportunity_engine.py`. A workflow that names those formats cannot include `enqueue_publish`.
