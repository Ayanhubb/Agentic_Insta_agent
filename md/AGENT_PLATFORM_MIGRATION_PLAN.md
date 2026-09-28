# Migration plan

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Concept](AGENT_PLATFORM_CONCEPT.md), [Decisions](AGENT_PLATFORM_DECISIONS.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md).

This plan turns the existing studio into a registered reference agent. It does not rewrite it. Phase 0 is this documentation. No application behavior changes in phase 0.

Each later phase is **PLANNED**. It starts only when implementation is requested. A phase that needs a new product capability is **PROPOSED** and sits after the reference agent is registered.

## Constraints on every phase

- `InstagramAgent` remains the only component that calls Meta create, publish, and verify.
- `enqueue_instagram_publication` remains the only handoff into that agent.
- MCP `assert_tool_allowed` still rejects publish aliases and the six tool names.
- `LLMPlanner` stays disabled.
- DeepSeek and OpenAI stay behind `LLM_PROVIDER` and `IMAGE_PROVIDER`.
- Empty keys still allow startup. Live calls still raise the existing configuration errors.
- Tenant id still comes from the session or the scheduler.
- No second connect router. `api/instagram_account_routes.py` stays unmounted.
- No new Git remote and no deploy step are part of this plan.
- Default pytest (`not real_openai and not real_deepseek`) and the frontend tests stay the regression gate.

## Phase 0 — proposal

**IMPLEMENTED** as of 2026-09-25: the markdown files named in the task. Application code is untouched.

Exit: readers can point at a workflow id and a module path for every current pipeline.

## Phase 1 — catalog only

**PLANNED.** Add a platform package that describes current capabilities. It does not change routes.

Contents:

- Workflow ids from [workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), each pointing at the existing callable (`ContentOrchestrator`, `CampaignPipeline`, `InstagramAgent.run`, trend tick, publication gateway).
- Provider roles calling the existing factories.
- Tool planes listing the current allowlists, without registering new tools.
- Knowledge source names and asset roles.
- Policy id `instagram.still_image` pointing at `decide_approval`.
- Agent id `instagram_marketing` with `owner=system`.

The catalog is importable in tests. Production `create_app` does not need to call it for behavior to stay the same. A test asserts:

- the publish workflow's callable is the Instagram agent path
- the MCP grant and the Instagram tool set are disjoint
- provider roles resolve through `select_reasoning_provider` and `select_image_provider`

No Alembic revision in this phase.

## Phase 2 — dispatcher behind existing functions

**PLANNED.** `dispatch(agent_id, workflow_id, tenant, payload)` calls the catalog callable.

HTTP handlers still contain today's validation and status codes. They may call `dispatch` only if a characterization test shows the same response for generation, approve, reject, publish, and run-now. If that cannot be shown, the handlers keep their direct calls and `dispatch` is used by new tests only.

The dispatcher must not:

- construct `InstagramGraphClient`
- read tokens
- accept a model-supplied tenant id
- retry a publish after `skip_publish` or unknown certainty

Scheduler `AutomationRunner.tick` keeps its order: daily, festival, trend, then commit. Trend failures still return `trend_run_failed` without failing the whole tick.

## Phase 3 — optional columns

**PLANNED** only if phase 2 logs or stores an agent id.

- Nullable `agent_id` and `workflow_id` on `agent_tasks`.
- New rows for publish tasks may set `instagram_marketing` and `instagram.publish`.
- Old rows stay null. Readers do not require the columns.
- Alembic revision follows `db/migrations/versions/` and the models in `db/models.py`. No second ORM.

Generation requests that do not create `agent_tasks` today stay that way.

Do not add agent-definition tables until a user can create an agent. The system catalog can live in code.

## Phase 4 — read APIs

**PROPOSED.** Authenticated reads:

- `GET` agent catalog: id, workflows, provider role names, tool planes, whether the caller may configure it.
- Existing `GET /ai/status` and `GET /mcp/status` remain the provider and tool status routes.

Responses include no secrets and no other user's configuration. The Instagram studio routes stay as they are.

This phase still does not let a client add a tool or a workflow step.

## Phase 5 — user configuration of the reference agent

**PROPOSED.** Limited to fields that already exist:

- business, brand, products, offers, assets
- Instagram connect and disconnect
- Canva connect and disconnect
- `automation_settings` flags, including the force-to-1 rule for `daily_posts_per_day`

The API writes the same tables the current routes write. A builder UI, if built, calls those routes or thin aliases. It does not gain a new publish path.

## Phase 6 — a second agent

**PROPOSED.** Out of scope until a concrete agent is specified. The checklist is in [agent model](AGENT_PLATFORM_AGENT_MODEL.md).

Minimum bar:

- new workflow handlers in code
- no new name in `ALLOWED_TOOL_SET` unless the product decision is to extend the Instagram agent itself
- if the agent publishes to Instagram, it calls `enqueue_instagram_publication` only
- tests for tenant denial and for `TOOL_NOT_ALLOWED` on publish names

Do not start phase 6 by copying `agent/agent.py`.

## What not to migrate

| Item | Reason |
| --- | --- |
| `TaskStatus` values | They are the publish runtime's states |
| `AgentState.platform` literal | The publish runtime is Instagram |
| `FestivalMcp` onto the model allowlist | It is a scheduler adapter. Moving it changes what the model can call |
| `scheduled_jobs` into Redis, Celery, or another queue | The runner is in-process and the product does not depend on an external queue |
| Provider keys into columns | They are environment secrets |
| `META_ACCESS_TOKEN` into the publication gateway | Production publishing uses the user token. The scheduler must keep failing closed |
| `LLMPlanner` | Enabling it changes who chooses publish tools |
| Public media route | Meta must still fetch the prepared image without a session |
| SQLite default | Local and tests use it. PostgreSQL stays a URL, not a required new service |

## Test gate for phases 1–3

Run the default suite. These files are the ones that encode the invariants:

| Area | Tests |
| --- | --- |
| Publish allowlist and agent | `tests/test_agent.py`, `tests/test_planner.py`, `tests/test_agentic_workflow.py`, `tests/test_instagram_agent_integration.py` |
| MCP cannot publish | `tests/test_mcp.py`, `tests/test_instagram_mcp_deepseek.py` |
| Credentials | `tests/test_instagram_credential_isolation.py`, `tests/test_auth.py` |
| Approval | `tests/test_approval_policy.py` |
| Wiring | `tests/test_integration_wiring.py`, `tests/test_content_orchestrator.py`, `tests/test_scheduler.py` |
| Providers | `tests/test_ai_providers.py`, `tests/test_provider_routing.py` |
| Canva does not publish | `tests/test_canva_mcp.py`, `tests/test_canva_execution.py` |

`real_openai`, `real_deepseek`, and `real_instagram` stay opt-in. Phase 1–3 do not require live vendor calls.

Frontend `npm test` stays in the gate when a phase touches React. Phases 1–3 should not touch React.

## Rollback

Phases 1–2 add a package and a function. Rollback is deleting the call from handlers if one was added, and leaving the catalog unused. Database rollback applies only if phase 3 shipped: the revision drops nullable columns that no old code reads.

No phase rewrites `agent/agent.py` in place. If a phase needs a change inside the agent, that change is a separate review and must keep `ALLOWED_TOOL_SET` and `RecoveryPolicy`.

## Order

```text
Phase 0  docs                          this change
Phase 1  in-code catalog               no route change
Phase 2  dispatcher                    behavior-neutral
Phase 3  nullable task columns         optional
Phase 4  read catalog API              proposed
Phase 5  configuration via current APIs proposed
Phase 6  second agent                  only with a named agent
```

Phases 4–6 are not implied by finishing the docs. The Instagram studio remains the running product at every step.
