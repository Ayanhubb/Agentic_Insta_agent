# Agent runtime

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Security](AGENT_PLATFORM_SECURITY.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md).

The platform runtime is a dispatcher. The Instagram publish runtime is `InstagramAgent`. They are different objects so that Instagram states, Graph certainty, and the six-tool allowlist never become generic runtime rules.

## Runtimes that exist

**IMPLEMENTED.**

| Runtime | Entry | Plans with | Executes |
| --- | --- | --- | --- |
| `InstagramAgent` | `run` | `DeterministicPlanner` | `Executor` on `ToolRegistry` |
| `ContentOrchestrator` | studio generation | creative model | MCP, Canva or image provider, vision, store |
| `CampaignPipeline` | scheduler | `ContentAgent` | image path, `decide_approval`, `PublicationService` |
| `TrendIntelligenceScheduler` | trend tick | trend analyst | research, packet, save, optional pipeline |
| `Executor` | one tool | n/a | `tool.execute(state)` → `Observation` |

`InstagramAgent.run` wraps `_run_inner` in `asyncio.wait_for` using `agent_timeout_seconds`. Timeout during `publishing`, `verifying`, or `creating_media` is certainty `UNKNOWN`. Other timeouts are `FAILED`. Unhandled exceptions become `INTERNAL_ERROR`. Temp files are cleaned in `finally`.

`Executor` accepts `Observation` or `ToolResult`. Any other return type is `INTERNAL_ERROR`. It does not eval code.

`ToolRegistry` is constructed by `build_registry` in `agent/agent.py` with exactly six tools. `register` refuses any other name.

## Generic dispatcher

**PROPOSED.** A small function:

```text
dispatch(agent_id, workflow_id, tenant, payload) -> result
```

Rules:

1. `agent_id` and `workflow_id` must be in the catalog.
2. `tenant` must be a `TenantContext`.
3. The handler is the callable stored on the step or on the whole-workflow adapter.
4. The dispatcher does not import `InstagramGraphClient`, does not read `META_ACCESS_TOKEN`, and does not branch on Instagram tool names.
5. Publish workflows are invoked as `InstagramAgent`. The dispatcher does not loop the six tools itself.

**PLANNED** first adapter: `workflow_id` selects the existing function, which keeps its current signature. Routes keep calling those functions directly until a later phase switches the route to `dispatch` without changing status codes. See [migration](AGENT_PLATFORM_MIGRATION_PLAN.md).

`LLMPlanner` is not the dispatcher and is not the publish planner.

## Task model

**IMPLEMENTED** tables:

`agent_tasks`: `id`, `user_id`, `task_type`, `trigger`, `status`, `current_step`, `started_at`, `completed_at`, `error`, `created_at`, `state_json`.

`agent_events`: `id`, `task_id`, `from_state`, `to_state`, `tool`, `result`, `observation` JSON, `timestamp`.

`AgentState` is the in-memory publish state. `platform` is fixed to `instagram`. Fields include container id, media id, permalink, `publish_certainty`, `skip_publish`, and `verified`.

`TaskStatus`: `pending`, `planning`, `validating`, `preparing`, `uploading`, `creating_media`, `publishing`, `verifying`, `completed`, `failed`.

HTTP:

- `GET /api/v1/tasks/{task_id}` — other users' tasks are 404.
- `GET /api/v1/tasks/{task_id}/events` — `text/event-stream` until completed or failed.
- `GET /api/v1/tasks/{task_id}/owned` — task plus events.

`instagram_posts.agent_task_id` links a post to a task. New writes set `instagram_posts.user_id`. Older rows may have a null `user_id`.

**PROPOSED** execution record, only as a description until migration adds columns:

| Field | Source today |
| --- | --- |
| `task_id` | `agent_tasks.id` / `AgentState.task_id` |
| `tenant_id` | `user_id` |
| `agent_id` | absent. Would be `instagram_marketing` for new publish and generation runs |
| `workflow_id` | absent. Implied by `task_type` and `trigger` |
| `status` | `agent_tasks.status` |
| `current_step` | `agent_tasks.current_step` |
| `error` | redacted `AppError` body |

Generation runs that never publish still create image rows. They do not all create an `agent_tasks` row. The platform does not force a task row onto studio generation in the first registration. Observability for that path stays the image row, QA JSON, and API response.

**PLANNED.** Nullable `agent_id` and `workflow_id` on `agent_tasks` when the first code phase needs them. Existing readers ignore unknown columns. No backfill is required for correctness of publish.

## State machine boundary

`StateMachine` and `TOOL_TO_STATUS` map the six tools onto Instagram statuses. That mapping is **IMPLEMENTED** and stays inside the publish runtime.

The dispatcher's states for a non-publish workflow are the handler's own result: image approval status, trend report status, or an `AppError` code. They are not translated into `CREATING_MEDIA` or `PUBLISHING`.

Ambiguous publication stays a publish-runtime concern. The dispatcher must not retry `enqueue_instagram_publication` after certainty `UNKNOWN`. `PublicationService` and `RecoveryPolicy` already own that rule.

## Execution policies at runtime

The dispatcher passes the process `Settings` into the handler that already reads them. It does not override `skip_publish`, attempt caps, or Graph host allowlists.

Graph hosts are `graph.facebook.com` and `graph.instagram.com` unless `strict_graph_hosts` is relaxed for localhost tests. `allow_insecure_graph_http` defaults to false. Those checks stay in the Graph client used by `InstagramAgent`.

Credential resolution stays in `PublicationGateway.resolve_credentials`: the user's `instagram_accounts` row, decrypted for that request, passed into the agent. The dispatcher never sees the token.

## Observability

**IMPLEMENTED** channels. They are the platform's observability. This proposal does not add a tracing vendor.

| Channel | What it records | Secret rule |
| --- | --- | --- |
| `services/logging.py` `log_step` | Task events such as `TASK_CREATED`, `PLAN_CREATED`, `TASK_COMPLETED` | `redact_text` on secret-like substrings |
| `AppError` HTTP body | `code`, redacted `message`, `request_id`, `"platform": "instagram"` | Handler redacts before send |
| MCP audit | `mcp.tool.execute` with tool, tenant, status, duration, error code, argument keys after sanitize, result keys | Paths and secret keys dropped. Tool name redacted on unknown-tool errors |
| `pipeline_metrics` | In-process counters only | Counts, never payloads |
| `agent_events.observation` | Tool observation JSON | Must stay free of tokens. Existing sanitize path is the pattern |
| `GET /api/v1/ai/status` | Provider names, model names, configured booleans | No key material |
| `GET /api/v1/mcp/status` | MCP enabled, Canva connected, discovered tool list | No Canva token |
| SSE task events | Publish progress for the owning user | Owner check |

Scheduler integration failures in `scheduler/integrations.py` log the module name and not the exception text, so a provider key in an error string is less likely to be written.

**PROPOSED.** When `dispatch` exists, it logs `agent_id`, `workflow_id`, `tenant_id`, `task_id`, and `status` through `log_step`. It logs the same fields the handlers already log. It does not log prompts in full if those prompts can contain offer text the product already stores in the database. Prompt bodies are not a new log source.

**PLANNED.** `pipeline_metrics` names stay the current counters. New counters, if added with dispatch, are counts of workflow starts and failures by workflow id. They are not required for the documentation phase.

The HTTP field `"platform": "instagram"` stays on current error bodies. A future agent adds its id in a new field when its routes exist. This proposal does not rename the field.

## Failure behavior the runtime must keep

| Situation | Behavior today |
| --- | --- |
| Missing DeepSeek key on a live reasoning call | `DEEPSEEK_CONFIGURATION_ERROR`. Startup still uses `MockLLMProvider` or `GroundedCreativeModel` |
| Missing OpenAI key or image model on a live image call | `OPENAI_CONFIGURATION_ERROR`. Startup uses `MockImageGenerationProvider` |
| Unknown `LLM_PROVIDER` at startup | `MockLLMProvider`. `get_llm_provider` raises `OPENAI_CONFIGURATION_ERROR` |
| `IMAGE_PROVIDER` not `openai` on a live call | configuration error, no pixels |
| Instagram account missing for automatic publish | `INSTAGRAM_NOT_CONNECTED` |
| Publish tool requested through MCP | `TOOL_NOT_ALLOWED` |
| Asset owned by someone else | `ASSET_ACCESS_DENIED` or HTTP 404 |
| Agent crash | `INTERNAL_ERROR`, state `failed`, temp files removed |

## What the runtime is not

- A loop that asks a model which tool to call next for publishing.
- A host for user-supplied Python.
- A second implementation of the six tools.
- A scheduler. `AutomationRunner` remains the trigger. The runtime runs one workflow when the trigger fires.
