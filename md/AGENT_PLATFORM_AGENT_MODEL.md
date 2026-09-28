# Agent model

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Concept](AGENT_PLATFORM_CONCEPT.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md), [Security](AGENT_PLATFORM_SECURITY.md).

An agent is a named binding of workflows, providers, tools, knowledge, assets, triggers, an approval policy, and an execution policy. The binding is data. The behavior is code that already exists, or a new handler added in review. The model never is the agent.

## Definition

**PROPOSED** shape. No table stores this today. The first record is a system definition, not a user insert.

| Field | Meaning | Instagram reference |
| --- | --- | --- |
| `id` | Stable string | `instagram_marketing` |
| `owner` | `system` or `users.id` | `system` |
| `version` | Integer. Runs record the version they started with | `1` when first registered |
| `workflows` | Ids from the workflow catalog | `studio.generate`, `studio.approve`, `scheduler.campaign`, `instagram.publish`, `trend.cycle` |
| `providers` | Role → configured implementation name | reasoning `deepseek`, image `openai`, vision `deepseek`, creative export `canva` when enabled |
| `tools` | Grants into existing allowlists | MCP read tools. Instagram publish tools are not a grant. They stay inside `instagram.publish` |
| `knowledge` | Source names from the architecture page | business, brand, product, festival, account, trend, content rules |
| `assets` | Scopes and roles the workflows may resolve | brand logo, product image, generated image |
| `triggers` | Entry points that start a workflow | studio HTTP, approve HTTP, direct publish HTTP, scheduler tick, run-now |
| `approval_policy` | Policy id | `instagram.still_image` |
| `execution_policy` | Timeouts and retry caps already in `Settings` and the recovery policy | `AGENT_TIMEOUT_SECONDS` default 120. MCP client timeout default 5 seconds. Plan and QA attempt caps of 3 |

User-owned agents are **PROPOSED** and come after the system record exists. A user-owned agent may only reference workflows, tools, knowledge sources, and policies that are already registered. It may not add a tool name, a Python callable, or a Graph host.

Provider fields store the role and the implementation name (`deepseek`, `openai`, `mock`). They do not store API keys.

## What is not an agent definition

**IMPLEMENTED** classes stay classes:

| Class | Role in the model |
| --- | --- |
| `InstagramAgent` | Runtime of workflow `instagram.publish` only |
| `ContentOrchestrator` | Runtime of `studio.generate` |
| `ContentAgent` | Planning step inside `scheduler.campaign` |
| `DeepSeekTrendAnalyst` | Analysis step inside `trend.cycle` |
| `AccountIntelligenceService` | Knowledge source, not an agent |
| `LLMPlanner` | Unused stub. Not a planner for any agent |

`OpportunityEngine.evaluate` builds a request and a bridge with `published=False`. It does not start `ContentAgent.run`. Carousel, reel, and story recommendations stay concept-only because the publish workflow accepts one still image. That engine is a helper of the reference agent, not a fifth agent.

## Lifecycle

**IMPLEMENTED** lifecycle is task lifecycle (`pending` → `planning` → tool states → `completed` or `failed`) and image approval (`PENDING_APPROVAL`, approve, reject). There is no draft/active flag on an agent.

**PROPOSED** lifecycle for a definition:

| State | Who can enter it | Effect |
| --- | --- | --- |
| `active` | System record, on registration | Triggers dispatch to its workflows |
| `paused` | The owner, later | Triggers return a configuration error and do not publish. In-flight Instagram tasks finish under the recovery rules already in `agent/recovery.py` |
| `retired` | The owner, later | Hidden from new runs. Historical `agent_tasks` remain |

The system agent `instagram_marketing` is **PLANNED** to be registered directly as `active`. Pause and retire of that system agent are not part of the first registration. Pausing it would turn off the studio, which is the product.

User-defined agents, when they exist, start `active` only after the control plane checks that every referenced id is registered and that the tool grant does not include `INSTAGRAM_PUBLISHING_TOOLS`.

There is no marketplace, template gallery, or share-across-tenants operation in the tree. Those are rejected for this proposal. See [decisions](AGENT_PLATFORM_DECISIONS.md).

## Configuration versus builder

**Agent configuration** is the record above.

**Agent builder** is the React surface at `/agents` and `GET`/`PUT /api/v1/platform/agents`. It writes a user-owned record from registered ids. The Instagram studio remains the interface of `instagram_marketing`, which the builder shows as read-only. See [frontend](AGENT_PLATFORM_FRONTEND.md).

Configuration a user may set, once the API exists, is limited to what `automation_settings`, business profile, brand, products, assets, and the connected Instagram account already store, plus:

- which registered optional steps are on (Canva when `CANVA_ENABLED` and the user is connected)
- the existing auto-publish flags

Configuration a user may not set:

- a new tool
- a publish step that skips `InstagramAgent`
- another tenant's knowledge or assets
- a provider API key
- `LLMPlanner` as the publish planner
- formats the agent cannot publish

`DeterministicPlanner` remains the planner for `instagram.publish`. The comment on `LLMPlanner` says the LLM may only propose tools from `ALLOWED_TOOL_SET` and cannot execute Python, shell, or arbitrary HTTP. That stub stays disabled. Enabling it would let a model choose publish steps. That is rejected.

## How a future agent is added

**PROPOSED** checklist. Each item is a reviewed code or data change. None of it is implemented.

1. Choose an id that is not `instagram_marketing`.
2. Implement any new step handler in the control plane. A handler is a Python function the workflow catalog points at. It is not a string the model evaluates.
3. If the agent needs context, add read-only MCP tools through `build_registry` and `MCP_TOOL_ALLOWLIST`, with schemas that set `additionalProperties` false, and with `tenant_id` taken from `TenantContext`.
4. If the agent needs an external side effect other than Instagram publish, add a dedicated tool module and a gateway function. Do not register that tool on MCP. Instagram publish is not available to that agent. If the new agent must post to Instagram, its last step calls `enqueue_instagram_publication` and stops.
5. Bind provider roles to existing factories. A new vendor is a new implementation of `LLMProvider` or `ImageGenerationProvider`, selected by env, not a hard-coded import in the runtime.
6. Bind knowledge sources and asset roles that already exist, or add a repository scoped by `user_id`.
7. Bind triggers to an existing HTTP route or to a new branch inside `AutomationRunner.tick` that still commits with the same session rules.
8. Bind an approval policy. If the policy can set `publish=true`, the only publish implementation is the Instagram gateway.
9. Add tests that the new tool grant does not include publish names, that cross-tenant ids fail, and that provider prompts contain no token fields.

Copying `InstagramAgent` and swapping the Graph URL is not the method. That would be a second publisher.

## Execution policy

**IMPLEMENTED** values the reference agent already uses:

| Knob | Where | Default |
| --- | --- | --- |
| Agent timeout | `Settings.agent_timeout_seconds` / `AGENT_TIMEOUT_SECONDS` | 120 |
| Storage retries | `RecoveryPolicy` via `Settings.storage_retry_attempts` | 3. `from_env` does not read an environment variable for this count |
| Verification retries | `Settings.verification_retry_attempts` | 5. Same note: class default, not read from env in `from_env` |
| Ambiguous publish | `RecoveryPolicy` | Sets `skip_publish`. Does not publish again until verification |
| Studio plan attempts | `ContentOrchestrator` `PLAN_ATTEMPT_CAP` | 3 |
| Studio QA attempts | orchestrator | 3 (constructors default to 2, then clamp) |
| Scheduler plan attempts | `ContentAgent` | 3 |
| Diversity | `DiversityPolicy` | Rejects repeated themes inside a session |
| Semantic similarity | `SemanticSimilarityBackend` | **NOT IMPLEMENTED.** `enabled` is false and enabling it raises |
| MCP tool timeout | `MCPClient` | 5 seconds |

**PROPOSED.** The agent record cites these knobs. It does not invent a second retry policy for the publish tools. Changing retry behavior means changing `RecoveryPolicy`, which stays Instagram-specific.

## Identity of a run

**IMPLEMENTED.** `AgentState.task_id`, `user_id`, `request_id`, and `db_task_id`. `agent_tasks.task_type` and `trigger` columns exist.

**PLANNED.** A run of `instagram_marketing` stores `task_type` values the code already writes. A new `agent_id` column is optional and nullable so old rows remain valid. See [migration](AGENT_PLATFORM_MIGRATION_PLAN.md).

## Versioning

**PROPOSED.** Workflow and agent versions are integers on the definition. A run copies the version onto the task when the column exists. In-flight tasks keep the code that started them. This proposal does not require a migration until a second version exists. Version 1 is "today's call graph".
