# Workflow engine

> **Status on 2026-09-28.** Checked against the tree. This page matches the package it names. The live studio path is unchanged. Agent rows are durable in `007_platform_agents`. Limits are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Architecture](AGENT_PLATFORM_ARCHITECTURE.md), [Runtime](AGENT_PLATFORM_RUNTIME.md), [MCP](AGENT_PLATFORM_MCP.md), [Security](AGENT_PLATFORM_SECURITY.md), [Data model](AGENT_PLATFORM_DATA_MODEL.md), [Migration](AGENT_PLATFORM_MIGRATION_PLAN.md).

Inspection date: 2026-09-25. Package: `agent_platform.workflow`. The name is `agent_platform` so it does not shadow the Python standard library module `platform`.

## What this is

**IMPLEMENTED.** An in-process engine that runs a structured definition by calling capabilities registered in server code.

It is not wired into `create_app`, HTTP routes, or the scheduler. It does not read or write `agent_tasks`. It does not import the Instagram agent, the six publish tools, or `enqueue_instagram_publication`. The reference workflows in the [workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md) still run as the functions that already implement them.

A definition is data. A step stores a capability **name**. The engine resolves that name against `CapabilityRegistry`. It does not import the name, and it does not run text.

## Pipeline

```text
START
  ↓
CONTEXT
  ↓
LLM / TOOL / MCP / SERVICE
  ↓
CONDITION
  ↓
APPROVAL
  ↓
ACTION
  ↓
VERIFICATION
  ↓
END
```

Those are step types, not a requirement that every definition contains every type. A definition chooses a path. `START` and `END` are required. The middle types are used when that stage exists.

| Step type | Runs a capability | Behavior |
| --- | --- | --- |
| `start` | No | Copies the sanitized payload into the step output |
| `context` | Yes | Registered context loader |
| `llm` | Yes | Registered model role. The engine does not import a vendor SDK |
| `tool` | Yes | Name must resolve in the registry |
| `mcp` | Yes | Same rule. This engine does not open the MCP client |
| `service` | Yes | Registered callable. Not a string of code |
| `condition` | No | Compares a prior output field. Operators: `eq`, `neq`, `exists`, `not_exists`, `in` |
| `approval` | Yes | `decision: pause` waits. `approve` continues. `reject` fails the run |
| `action` | Yes | Registered side effect. Instagram publish is not one of these names |
| `verification` | Yes | Registered check. A failure stops the run |
| `end` | No | Collects succeeded step outputs |

`instagram_tool_sequence` is not a step type here. Publish remains inside `InstagramAgent`.

## Records

| Record | Role |
| --- | --- |
| `WorkflowDefinition` | `id`, integer `version`, optional `agent_id`, ordered `steps` |
| `WorkflowStep` | `id`, `step_type`, optional `capability`, `input`, `condition`, `retry`, `timeout`, `depends_on`, `next_step` |
| `StepType` | The types in the table above |
| `StepInput` | Literal values and copies of fields from earlier steps. No code |
| `StepOutput` | `data` plus an error code when the step failed. Secret keys are not stored |
| `StepCondition` | `source_step`, `field`, `operator`, `value`, `then_step`, `else_step` |
| `StepRetryPolicy` | `max_attempts` from 1 to 5, and the error codes that may be retried |
| `StepTimeout` | Seconds for one handler call, greater than 0 and at most 120 |
| `StepDependency` | A step id that must already be `succeeded` or `skipped` |
| `WorkflowExecution` | One run: tenant, copied version, status, idempotency key, steps |
| `WorkflowExecutionStep` | Status and attempts for one step of that run |

Statuses for a run: `pending`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`, `timed_out`.

Statuses for a step: `pending`, `running`, `succeeded`, `failed`, `skipped`, `waiting_approval`, `timed_out`, `cancelled`.

## Registry

`CapabilityRegistry.register(name, handler, step_types=...)` stores a callable. Registration is server code. A workflow file cannot supply the callable.

`resolve(name, step_type)` fails with `UnknownCapability` when the name is missing, and with `CapabilityRejected` when the name is registered for a different step type.

The engine refuses these names even if a caller tries to register them:

- The six Instagram tool names and the publish aliases MCP already rejects (`publish`, `media_publish`, `instagram_publish`, `post_to_instagram`, `create_media`, `publish_media`)
- `eval`, `exec`, `compile`
- Names whose first segment is `os`, `sys`, `subprocess`, `importlib`, or `builtins`

A model that puts a new tool name on a step does not create a handler. The run stops with `UnknownCapability` before any handler is called.

## What the engine will not do

- Call `eval`, `exec`, `os.system`, `subprocess`, or import a string
- Read a provider key, a Graph token, or a Canva token. Definitions and payloads that contain those keys raise `SecretRejected`. The value is not copied onto the execution
- Treat `tenant_id` or `user_id` inside a payload as the tenant. Those keys are dropped. The tenant is the `TenantContext` argument (`authenticated`, `scheduler`, or `system`)
- Branch on `platform == "instagram"`
- Call `media_publish` or construct `InstagramGraphClient`
- Accept a handler path, a module path, or a field named `handler`, `code`, `script`, `command`, or `shell`

Conditions read a dotted field of letters, digits, and underscores. `__` is rejected. There is no expression string.

Timeouts are cooperative. The handler call runs on a daemon thread, and the engine stops waiting when the limit is reached. It does not kill a process. A timed-out step fails the run unless the retry policy lists `timeout`.

Cancellation is checked between steps. A step already inside a handler finishes that call. Later steps are `cancelled`. A run that is `waiting_approval` can be cancelled immediately. A finished run stays finished.

## Control flow

Steps form a graph. Every step except `end` has one successor. A condition has `then_step` and `else_step` instead. Every path starts at the single `start` step and reaches the single `end` step. Cycles, unreachable steps, and a dependency that is not earlier on the path are `InvalidDefinition`.

The untaken branch is marked `skipped` through the first step that is also reachable from the taken branch. Skipped dependencies count as satisfied so a later step can join both arms.

A failed step, a timeout, a rejected approval, or a handler exception stops the run. Later steps stay `pending` and are not called. `CapabilityFailure("failed")` is retried only when `failed` is in `retry_on` and attempts remain. Other codes are not retried unless listed. The handler's exception text is not stored.

Human approval: the approval capability returns `{"decision": "pause"}`. The execution status becomes `waiting_approval`. `resume(tenant, id, approved=True)` continues at the next step and does not call the approval capability again. `approved=False` fails the run with `approval_rejected`.

## Tenancy and idempotency

Executions live in an in-memory store on the engine instance. `get`, `resume`, and `cancel` require the same `tenant_id`. A different tenant receives `ExecutionNotFound`, including when the id exists. That matches the product rule that a foreign id is not disclosed.

An idempotency key is scoped by `(tenant_id, workflow_id, key)`. The second call returns the first execution and does not call handlers again, including when the first run failed or is waiting for approval. Another tenant with the same key gets its own execution. The key does not override a failed step inside one run. A new attempt needs a new key.

The execution copies `workflow_version` at start. Changing the published definition later does not rewrite that run.

## Tests

`tests/test_workflow_engine.py` covers sequence, failed step, condition, approval pause, retry, timeout, unknown tool, tenant isolation, idempotency, secret rejection, cancellation, and a source check that this package does not call dynamic execution or import the Instagram publisher.

## Not in this change

- No migration of `studio.generate`, `scheduler.campaign`, `trend.cycle`, or `instagram.publish` onto this engine
- No Alembic revision and no `agent_tasks` columns
- No route that accepts a definition from a client
- No change to `InstagramAgent`, `DeterministicPlanner`, or `enqueue_instagram_publication`
