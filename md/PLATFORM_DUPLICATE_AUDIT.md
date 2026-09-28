# Platform duplicate audit

Inspection date: 2026-09-28. Updated the same day after persistence and shadow mode. Nothing was deleted.

The Instagram studio path is the production process: `create_app` selects providers with the existing factories, starts `AutomationRunner` when `SCHEDULER_ENABLED` is true, and publishes only through `InstagramAgent`. The platform layer added a second copy of several of those jobs. The copies are libraries or a second HTTP API. They are not a second Meta publisher.

## Summary

| Concern | Copies found | Production caller today |
| --- | --- | --- |
| Agent API | 2 | React uses `/api/v1/agents` only |
| Agent store | 2 | Canonical catalog is still process memory. `AgentBuilderService` also writes `agents`, `agent_versions`, and `agent_executions` |
| Workflow engine | 2 | Neither studio route uses either engine |
| Tool registry | 3 planes | Studio uses MCP reads and the Instagram publish allowlist |
| MCP registry | 1 live registry, 1 unused adapter | `MCPClient` / `build_registry` |
| Policy engine | 2 | Studio and the reference policy call `decide_approval` |
| Provider registry | 2 entry points, 1 implementation | `create_app` calls the factories directly |
| Execution engine | 4 | Studio routes call orchestrator, pipeline, and `InstagramAgent` |
| Scheduler | 2 | `scheduler_loop` starts `AutomationRunner` only |
| Instagram reference agent | 1 definition, 1 live publisher | HTTP publish does not call `instagram_reference.dispatch` |

## Agent API

### `/api/v1/agents`

| | |
| --- | --- |
| Location | `api/agent_routes.py`, `agent_builder/service.py`, `agent_builder/schema.py` |
| Responsibility | Authenticated CRUD, draft, validate, publish version, disable, rollback, clone, list executions, test execute |
| Callers | `create_app` mounts the router. `app.state.agent_builder`. React `frontend/src/services/api/endpoints.ts` (`platformAgents` points here) and `frontend/src/services/api/agents.ts` |
| Tests | `tests/test_agent_builder_api.py`, `frontend/src/test/agents.test.tsx` |
| Production usage | Mounted. Test execute stubs publication and does not call Meta. `create_app` also writes the agent and its versions to SQL |
| Recommendation | **Canonical API.** It is the one the UI calls, and it stores versions on `agent_runtime.catalog.InMemoryCatalog` |

### `/api/v1/platform/agents`

| | |
| --- | --- |
| Location | `api/agent_builder_routes.py`, `backend/platform/builder/store.py`, `backend/platform/builder/catalog.py`, `backend/platform/builder/runner.py` |
| Responsibility | A second builder: catalog, per-section PUT, the same lifecycle verbs, test execute |
| Callers | `create_app` mounts the router. `app.state.platform_agents`. No React caller |
| Tests | Covered indirectly by platform builder modules; the React tests hit `/api/v1/agents` |
| Production usage | Mounted and unused by the UI. Separate memory from `AgentBuilderService`. An agent created on one route is invisible to the other |
| Recommendation | Non-canonical. Keep the file until a later reconciliation removes the mount. Do not add a third API |

## Agent store

### `InMemoryCatalog`

| | |
| --- | --- |
| Location | `agent_runtime/catalog.py` |
| Responsibility | Definitions, versions, drafts, workflow pins for `AgentRuntime` |
| Callers | `AgentBuilderService`, `instagram_reference.definition.register_instagram_marketing`, `backend/platform/builder/runner.py` (its own catalog inside a test runtime) |
| Tests | `tests/test_agent_runtime.py`, `tests/test_agent_builder_api.py`, `tests/test_instagram_reference_agent.py` |
| Production usage | Process memory on `app.state.agent_builder`, plus SQL snapshots when `create_app` passes the session factory. Alembic revision `007_platform_agents` |
| Recommendation | **Canonical store** for platform agents. SQL is the durable copy of that catalog, not a third store |

### `AgentBuilderStore`

| | |
| --- | --- |
| Location | `backend/platform/builder/store.py` |
| Responsibility | A second in-memory agent record (`agent_` ids, config snapshots, execution list) |
| Callers | `api/agent_builder_routes.py` only |
| Tests | Store behavior is exercised with the platform builder routes, not by the React client |
| Production usage | `app.state.platform_agents`. Lost on restart |
| Recommendation | Non-canonical duplicate of the catalog |

## Workflow engine

### `agent_runtime.engine.WorkflowEngine`

| | |
| --- | --- |
| Location | `agent_runtime/engine.py` |
| Responsibility | Run pinned steps: context, reason, tool, asset, approval, handler. Block external effects on draft versions. No `eval` / `exec` |
| Callers | `agent_runtime/runtime.py` `AgentRuntime` |
| Tests | `tests/test_agent_runtime.py`, `tests/test_agent_runtime_boundaries.py` |
| Production usage | Only when a builder test run or `instagram_reference.dispatch` builds an `AgentRuntime`. Studio generation and publish do not call it |
| Recommendation | **Canonical workflow engine** for the platform |

### `agent_platform.workflow.engine.WorkflowEngine`

| | |
| --- | --- |
| Location | `agent_platform/workflow/engine.py` |
| Responsibility | A second interpreter: capability names, structured conditions, secret and code-key rejection |
| Callers | `tests/test_workflow_engine.py`. Not imported by `api/app.py` |
| Tests | `tests/test_workflow_engine.py` |
| Production usage | None |
| Recommendation | Non-canonical. Same job as the runtime engine, not connected to an agent |

`backend/platform/builder/runner.py` `run_declared` resolves `agent_runtime.workflows`. The only registered key is `instagram.reference`, which runs `AgentRuntime`. Unknown keys fail. It is an entry point, not a second engine.

## Tool registry

These three are not interchangeable. Collapsing the Instagram allowlist into MCP would give other agents a publisher. That is forbidden.

### Instagram publish allowlist

| | |
| --- | --- |
| Location | `agent/registry.py` `ToolRegistry`, `agent/planner.py` `ALLOWED_TOOL_SET`, `tools/instagram_media.py` |
| Responsibility | The six publish tools. Registration of any other name fails |
| Callers | `InstagramAgent` only, through `PublicationService` and `POST /api/v1/instagram/publish` |
| Tests | `tests/test_agent.py`, `tests/test_instagram_media.py`, `tests/test_instagram_agent_integration.py` |
| Production usage | This is the live Meta publisher |
| Recommendation | Keep as the closed publish plane. It is not the platform tool registry |

### MCP registry

| | |
| --- | --- |
| Location | `backend/mcp/registry.py` `MCPRegistry`, `backend/mcp/servers/__init__.py` `build_registry`, `backend/mcp/permissions.py` |
| Responsibility | 22 read tools. `assert_tool_allowed` rejects publishing names. Tenant comes from `TenantContext` |
| Callers | Content orchestrator, trend and account MCP, studio context. `MCPClient.invoke` |
| Tests | `tests/test_mcp.py`, `tests/test_trend_mcp.py`, `tests/test_mcp_account_intelligence.py`, `tests/test_brand_assets.py` |
| Production usage | Live read plane for the studio |
| Recommendation | **Canonical tool registry** for platform grants |

### Platform tool registry

| | |
| --- | --- |
| Location | `backend/platform/tools/registry.py` `PlatformToolRegistry`, `executor.py`, `mcp_adapter.py` |
| Responsibility | A second catalog. `MCPAdapter.register_into` copies MCP tools and refuses publish names |
| Callers | `tests/test_platform_tool_registry.py`. `create_app` does not construct it |
| Tests | `tests/test_platform_tool_registry.py` |
| Production usage | None |
| Recommendation | Non-canonical facade. Future grants should name MCP tools, not a second registry |

## MCP registry

There is one MCP implementation: `backend/mcp/`. `backend/platform/tools/mcp_adapter.py` does not open a second server. Canva (`backend/integrations/canva/`) is a remote creative adapter and is off `MCP_TOOL_ALLOWLIST`. Festival-only tools live on `FestivalMcp`, not on the allowlist.

No second MCP publisher exists.

## Policy engine

### `decide_approval`

| | |
| --- | --- |
| Location | `scheduler/approval_policy.py` |
| Responsibility | Still-image gates used by the campaign pipeline. Returns whether publication may be handed to the Instagram Agent |
| Callers | `scheduler/pipeline.py`, `instagram_reference/policy.py` `InstagramStillImagePolicy` |
| Tests | `tests/test_approval_policy.py`, `tests/test_integration_wiring.py`, `tests/test_instagram_reference_agent.py` |
| Production usage | Live approval for scheduler publish. The reference runtime uses the same function |
| Recommendation | **Canonical policy.** `InstagramStillImagePolicy` is the adapter the runtime calls. It does not publish |

### `agent_platform.policy.PolicyEngine`

| | |
| --- | --- |
| Location | `agent_platform/policy/engine.py`, `agent_platform/approval/engine.py` |
| Responsibility | Declarative policies, human-approval records, rejection of secrets, code keys, and publish capability names |
| Callers | `tests/test_policy_approval.py` only |
| Tests | `tests/test_policy_approval.py` |
| Production usage | None. Not on `POST /generation/{id}/approve` or `POST /instagram/publish` |
| Recommendation | Non-canonical duplicate of the approval decision |

## Provider registry

### Factories

| | |
| --- | --- |
| Location | `ai/llm_client.py` `select_reasoning_provider`, `ai/image_generator.py` `select_image_provider`, `ai/vision/deepseek.py` |
| Responsibility | DeepSeek for reasoning, planning, trend analysis, and vision QA. OpenAI for image generation and edit. Mock when keys are empty |
| Callers | `create_app` |
| Tests | `tests/test_provider_routing.py`, `tests/test_deepseek.py`, `tests/test_ai_providers.py`, `tests/test_openai_image.py` |
| Production usage | Live provider selection |
| Recommendation | Keep as the implementations. Do not add another vendor client |

### `ai.model_registry`

| | |
| --- | --- |
| Location | `ai/model_registry/registry.py`, `binding.py` |
| Responsibility | Role → implementation. `call` refuses publish tool names. Text and image calls go back to the factories |
| Callers | `AgentBuilderService` and `instagram_reference/services.py` for role names. `create_app` does not call the registry |
| Tests | `tests/test_model_registry.py` |
| Production usage | Platform lookup only |
| Recommendation | **Canonical registry for the platform**, required to delegate to the factories above. The studio may keep calling the factories directly |

## Execution engine

| Implementation | Location | What it runs | Production? |
| --- | --- | --- | --- |
| `InstagramAgent` | `agent/agent.py` | Validate, prepare, upload, create, publish, verify | Yes. Only Meta caller |
| `ContentOrchestrator` | `agent/content_orchestrator.py` | Studio generation. `published` stays false | Yes. `POST /generation` |
| `CampaignPipeline` | `scheduler/pipeline.py` | Daily and festival campaigns. Publish goes through `PublicationService` | Yes, when the scheduler or run-now fires |
| `AgentRuntime` | `agent_runtime/runtime.py` | Pinned platform workflow | Only builder test runs and `instagram_reference.dispatch`, which HTTP does not call |
| `run_declared` | `backend/platform/builder/runner.py` | Closed registry. `instagram.reference` runs the shadow workflow. Unknown keys fail | Declared workflow entry, not the studio publisher |

**Canonical platform execution** is `AgentRuntime`. Studio routes stay on the three studio functions. `run_declared` calls that runtime through the closed registry. It does not publish.

`instagram_reference/handlers.py` `instagram_agent_publish` calls `enqueue_instagram_publication`. That is a handoff, not a second Graph client. `create_app` does not call `dispatch`.

## Scheduler

### `AutomationRunner`

| | |
| --- | --- |
| Location | `scheduler/scheduler.py` `scheduler_loop` |
| Responsibility | Daily, festival, and trend ticks for each user. Does not construct a Graph client. Publication, when gates pass, is `publish_generated_image` → `InstagramAgent` |
| Callers | `create_app` lifespan. `POST /api/v1/automation/run-now` |
| Tests | `tests/test_scheduler.py`, `tests/test_trend_scheduler.py` |
| Production usage | Live when `SCHEDULER_ENABLED` is true |
| Recommendation | **Canonical scheduler.** It triggers work. It does not publish by itself |

### `TriggerScheduler`

| | |
| --- | --- |
| Location | `agent_platform/triggers/scheduler.py` |
| Responsibility | In-process schedule and condition tick. Module text says a tick never publishes. Webhooks are not delivered |
| Callers | `tests/test_trigger_platform.py` |
| Tests | `tests/test_trigger_platform.py` |
| Production usage | Not started |
| Recommendation | Non-canonical. Do not start it beside `AutomationRunner` |

## Instagram reference agent

| | |
| --- | --- |
| Location | `instagram_reference/` (`definition.py`, `binding.py`, `handlers.py`, `policy.py`, `services.py`) |
| Responsibility | System agent `instagram_marketing`, workflow `instagram.reference`, one external effect that hands off to `enqueue_instagram_publication` |
| Callers | `AgentBuilderService` seeds it. `build_reference_runtime` / `dispatch` are used by `tests/test_instagram_reference_agent.py`. Not referenced by `api/app.py` or `api/routes.py` |
| Tests | `tests/test_instagram_reference_agent.py` |
| Production usage | The definition is registered in the builder catalog. The live studio does not run `dispatch`. Publish remains `POST /instagram/publish`, generation approval, and the campaign pipeline |
| Recommendation | **Canonical platform record** of the Instagram marketing agent. **Canonical publisher** remains `InstagramAgent` |

## What is not a duplicate

- `backend/platform/knowledge/store.py` `KnowledgeStore` holds versioned documents in memory. Live business, brand, product, festival, account, and trend reads stay on MCP and SQL. Canonical read path: MCP.
- `backend/platform/assets/store.py` classifies files. Live bytes stay in the existing asset tables and `services/media_storage.py`.
- `GET /api/v1/admin/users` is registered twice (`api/auth_routes.py` and `api/platform_routes.py`). The auth handler is the one that runs. Unrelated to agents. Leave it until a cleanup pass.
- `api/instagram_account_routes.py` is not mounted. Live connect routes are `api/platform_routes.py`.
