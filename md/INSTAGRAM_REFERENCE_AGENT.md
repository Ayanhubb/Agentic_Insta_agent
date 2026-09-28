# Instagram Marketing Agent

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md), [Instagram agent](AGENTS/INSTAGRAM_AGENT.md), [Content agent](AGENTS/CONTENT_AGENT.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Migration plan](AGENT_PLATFORM_MIGRATION_PLAN.md).

**IMPLEMENTED** binding. The studio behavior is unchanged. `POST /api/v1/instagram/publish` still calls `PublicationGateway.enqueue_publication`. The reference package registers the system agent and can run the same publishing boundary through `dispatch`. It is not mounted on FastAPI.

Package: `instagram_reference`. Agent id: `instagram_marketing`. Display name: Instagram Marketing Agent. Owner: `system`. Version: `1`, status `active`.

## Publishing boundary

`InstagramAgent` is the only component that calls Meta create, publish, and verify. The reference workflow reaches it through `enqueue_instagram_publication`.

| Caller | What it may do |
| --- | --- |
| DeepSeek content planning | Produce a `CreativePlan` through `get_creative_model` |
| OpenAI image provider | Generate pixels through `select_image_provider` |
| DeepSeek Vision | Review an image through `ImageQAService` and `get_vision_provider` |
| MCP | Read the allowlisted context tools |
| Canva | Stays on the studio creative-export path. It is not a step in this workflow |
| Scheduler | Keeps `CampaignPipeline.publish_generated_image` |
| React | Keeps `POST /api/v1/instagram/publish` |
| Reference workflow | Calls `enqueue_instagram_publication` from the publication step only |
| `agent_runtime` | Dispatches registered steps. It does not import the Instagram agent |

The publication request uses the trigger tenant as `user_id`. `allow_environment_fallback` is false. A `publish` flag in the payload is ignored. Approval is `decide_approval` under policy id `instagram.still_image`.

Tool grants are `MCP_TOOL_ALLOWLIST`. They are disjoint from `ALLOWED_TOOL_SET` and from `INSTAGRAM_PUBLISHING_TOOLS`. The six Instagram tools stay inside `InstagramAgent` and `DeterministicPlanner`.

## Workflow `instagram.reference`

Trigger key: `reference.run`. Timeout: 120 seconds.

Festival context, Instagram intelligence, and trend evidence always run as steps. When the source is missing or not configured, the step completes with `found: false` and `applicable: false`. The run continues. A missing optional source does not publish.

| Step | Kind | Existing callee |
| --- | --- | --- |
| `load_business_context` | `load_context` `business` | `PlatformKnowledgeService` → `get_business_profile` when an invoker is bound |
| `load_brand_context` | `load_context` `brand` | `get_brand_guidelines` |
| `load_product_context` | `load_context` `product` | `get_active_offers` |
| `load_festival_context` | `load_context` `festival` | `get_upcoming_festivals` when applicable |
| `load_instagram_intelligence` | `load_context` `account` | `get_account_summary` when configured |
| `load_trend_evidence` | `load_context` `trend` | `get_current_trends` when evidence is available |
| `content_planning` | `reason` `content_planning` | `get_creative_model` when the trigger already carries a `CreativeContext` |
| `image_generation` | `reason` `image_generation` | `select_image_provider().generate` when the plan has an image prompt |
| `vision_qa` | `reason` `vision_qa` | `ImageQAService` with `get_vision_provider` |
| `approval_policy` | `decide_approval` | `scheduler.approval_policy.decide_approval` |
| `instagram_agent_publication` | `effect` | `enqueue_instagram_publication` |
| `meta_verification` | `validate` | Reads `verified` and `instagram_media_id` from the agent result |
| `persist_result` | `store` | Reports `agent_tasks` and `instagram_posts` ids the agent already wrote |

`meta_verification` does not call Meta. Success is a verified result with an Instagram media id. `skip_publish` does not start a second publication.

Reference-image editing stays in `ContentOrchestrator` and `submit_creative_image`, which use the same image provider factory. The reference image step calls `generate` for a planned prompt. It does not add a second image client.

Content planning does not rebuild `ContentOrchestrator`. When the trigger has no `CreativeContext`, the step returns `context_not_assembled` and does not invent a plan. The studio route remains the assembler for `/api/v1/generation`.

A plan or image result that names a publishing tool is rejected. The publication handler is not called.

## V1 path

These entry points are unchanged:

| Entry | Callable |
| --- | --- |
| `POST /api/v1/instagram/publish` | `publication_gateway.enqueue_publication` |
| `POST /api/v1/generation` | `ContentOrchestrator` |
| `POST /api/v1/generation/{image_id}/approve` | `PublicationService` |
| Scheduler campaign | `CampaignPipeline` → `publish_generated_image` |
| React | `API.instagram.publish` = `/api/v1/instagram/publish` |

`create_app` does not import `instagram_reference`. `LLMPlanner` stays disabled. `api/instagram_account_routes.py` stays unmounted.

## Platform path

```text
dispatch(tenant_id, payload)
  → AgentRuntime
  → instagram_marketing version 1
  → instagram.reference
  → enqueue_instagram_publication
  → InstagramAgent
```

`build_reference_runtime()` registers the catalog, the three handlers, and `InstagramStillImagePolicy`. Knowledge, model, and tool services are the adapters in `instagram_reference/services.py`. They call the factories above. They do not construct `InstagramGraphClient`.

The tenant id is the argument to `dispatch`. Payload keys `tenant_id` and `user_id` are dropped by the runtime.

Human mode (`USER_PROMPT`, the default) stops at `waiting_approval` and does not enqueue. Automatic mode enqueues only when `decide_approval` returns `publish=true`.

## Tests

`tests/test_instagram_reference_agent.py`

- The old handoff and `dispatch` both enqueue once each, with the trigger tenant, with environment fallback off, and without constructing a Graph client.
- A payload `publish: true` does not publish. Human approval waits. A failed vision gate denies the effect. Later steps are skipped.
- A plan that names `publish_instagram_media` does not enqueue. MCP and the reference tool service reject publishing names.
- The definition's tool grant equals the MCP allowlist and is disjoint from the Instagram agent's six tools.
- Route, app, pipeline, orchestrator, DeepSeek, and React publish paths are unchanged. The generic runtime still has no publication import.

## Files

| Path | Role |
| --- | --- |
| `instagram_reference/definition.py` | Agent, version, and the 13-step workflow |
| `instagram_reference/policy.py` | `instagram.still_image` → `decide_approval` |
| `instagram_reference/services.py` | Knowledge, model, and tool adapters |
| `instagram_reference/handlers.py` | Publication handoff, verification record, persist record |
| `instagram_reference/binding.py` | `build_reference_runtime`, `dispatch` |
| `instagram_reference/boundary.py` | Rejects publishing tool names in plans |
