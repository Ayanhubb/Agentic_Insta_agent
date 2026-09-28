# Canonical platform components

Decision date: 2026-09-28. Updated the same day after the persistence, shadow, and scheduler-lock work. This chooses one implementation for each platform concern. It does not add a new package and it does not delete the duplicates. Duplicate locations are in [PLATFORM_DUPLICATE_AUDIT.md](PLATFORM_DUPLICATE_AUDIT.md).

Status of the generic runtime as the live Instagram control plane: **PARTIAL**. Shadow execution of `instagram.reference` is **IMPLEMENTED**. Automatic Instagram publishing still uses the studio path.

The Instagram studio is unchanged. `POST /api/v1/instagram/publish`, generation approval, and `AutomationRunner` still reach Meta only through `InstagramAgent`.

## Choices

| Concern | Canonical | Stays in the studio, unchanged | Not canonical |
| --- | --- | --- | --- |
| Agent API | `api/agent_routes.py` → `AgentBuilderService` at `/api/v1/agents` | Studio routes in `api/routes.py` and `api/platform_routes.py` | `/api/v1/platform/agents` (`AgentBuilderStore`) |
| Agent store | `agent_runtime.catalog.InMemoryCatalog`, with SQL snapshots in `agents`, `agent_versions`, `agent_executions` | SQL tables for users, posts, images, tasks | `backend/platform/builder/store.py` |
| Workflow engine | `agent_runtime.engine.WorkflowEngine`. `run_declared` resolves a closed registry and calls this engine | `ContentOrchestrator`, `CampaignPipeline`, `InstagramAgent._run_inner` | `agent_platform.workflow.engine.WorkflowEngine` |
| Tool registry | `backend.mcp.registry.MCPRegistry` | `agent/registry.py` `ToolRegistry` (publish allowlist, Instagram Agent only) | `backend.platform.tools.PlatformToolRegistry` |
| Policy engine | `scheduler.approval_policy.decide_approval` via `InstagramStillImagePolicy` | Same function on the campaign pipeline | `agent_platform.policy.PolicyEngine` |
| Runtime | `agent_runtime.AgentRuntime` for platform workflows | `InstagramAgent` for Meta | A second runtime is not added |
| Provider registry | `ai.model_registry`, delegating to the existing factories | `create_app` may keep calling `select_reasoning_provider` and `select_image_provider` | A new vendor client |

Scheduler and the reference agent are included because the audit found two of each kind of entry point.

| Concern | Canonical | Not canonical |
| --- | --- | --- |
| Scheduler | `scheduler.scheduler.AutomationRunner` | `agent_platform.triggers.TriggerScheduler` |
| Instagram reference record | `instagram_reference` definition `instagram_marketing` | A second system agent |
| Instagram publisher | `InstagramAgent` | Any handler that constructs a Graph client |

## Why these

### Agent API and store

React (`frontend/src/services/api/agents.ts`) calls `/api/v1/agents`. That service stores versions, tool grants, and workflow pins on `InMemoryCatalog`, and it rejects publishing tool names. `/api/v1/platform/agents` is a second memory store with a different id shape. New platform work uses `/api/v1/agents` only.

### Workflow engine and runtime

`AgentRuntime` runs the reference workflow. Shadow mode loads the published SQL version, then runs that runtime with a publication handler that does not enqueue. `run_declared` looks up `agent_runtime.workflows` and calls the registered implementation. An unknown key fails. `agent_platform.workflow.WorkflowEngine` still has no production caller.

`InstagramAgent` is not replaced. It remains the only component that calls Meta. The runtime may hand work to `enqueue_instagram_publication`. It may not call `media_publish` itself.

### Tool registry

Studio reads already go through `MCPRegistry` and `assert_tool_allowed`. Grants on an agent version are names from `MCP_TOOL_ALLOWLIST`. `PlatformToolRegistry` copies those tools and is not constructed in `create_app`.

The Instagram `ToolRegistry` is a different plane. It registers only `ALLOWED_TOOL_SET`. It stays inside `InstagramAgent`. Platform code must not register those names.

### Policy

`decide_approval` is the gate the campaign pipeline and `InstagramStillImagePolicy` already share. A declarative `PolicyEngine` that HTTP never calls would become a second way to allow publication. Platform steps that need a decision call `InstagramStillImagePolicy`.

### Providers

DeepSeek remains reasoning, content planning, trend analysis, and vision QA. OpenAI remains image generation and editing. `ai.model_registry` is the lookup agents use, and it already calls the factories. `create_app` does not have to switch to the registry for the studio to keep working.

### Scheduler

`AutomationRunner` is the process tick. It does not construct a Graph client. When a campaign is allowed to publish, it calls `PublicationService`, which runs `InstagramAgent`. The FastAPI lifespan starts that loop only after `SchedulerLeader` takes an exclusive file lock, so a second worker in the same container does not tick. `TriggerScheduler` is not started and must not be started as a second loop. See [SCHEDULER_DEPLOYMENT_MODEL.md](SCHEDULER_DEPLOYMENT_MODEL.md).

## Rules that follow from this

1. Do not mount another agent router.
2. Do not persist a second agent model. Durability is `agents`, `agent_versions`, and `agent_executions` (Alembic `007_platform_agents`), written by `AgentBuilderService` when it has a session factory. The runtime catalog stays `InMemoryCatalog`. Bindings stay inside the version snapshot.
3. Do not register Instagram publishing tools on MCP or on `PlatformToolRegistry`.
4. Do not execute workflow steps from model text. Step kinds stay the closed set on `agent_runtime.models.StepKind`.
5. Do not build a visual workflow editor. The React workflow page stays read-only.
6. Builder `POST /execute` stays test mode. Shadow mode is a separate call (`instagram_reference.shadow.run_shadow`). It is not attached to `POST /api/v1/instagram/publish` or to `AutomationRunner`.

## Tenant

**IMPLEMENTED** boundary: one user is the tenant. `users.id` is `tenant_id` on user-owned agents and on executions. The shared reference agent uses tenant id `system` and is not a user row.

**NOT_IMPLEMENTED**: organizations, teams, and `Organization → users → agents`. Do not add those tables until a product requirement says so.

## Non-canonical code

Left in the tree on purpose:

- `api/agent_builder_routes.py` and `AgentBuilderStore`
- `agent_platform.workflow.engine.WorkflowEngine`
- `agent_platform.policy.PolicyEngine` and `agent_platform.approval.engine.ApprovalEngine`
- `backend.platform.tools.PlatformToolRegistry`
- `agent_platform.triggers.TriggerScheduler`
- `backend.platform.builder.runner.run_declared` is the declared-workflow entry. It calls the closed registry. The second workflow engine under `agent_platform` is still the duplicate.

They are covered by tests. Deleting them is a later cleanup, after callers are confirmed gone.
