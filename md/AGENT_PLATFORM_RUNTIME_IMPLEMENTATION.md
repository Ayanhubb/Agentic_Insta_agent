# Agent runtime implementation

> **Status on 2026-09-28.** Checked against the tree. This page matches the package it names. The live studio path is unchanged. Agent rows are durable in `007_platform_agents`. Limits are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Runtime proposal](AGENT_PLATFORM_RUNTIME.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Security](AGENT_PLATFORM_SECURITY.md), [Decisions](AGENT_PLATFORM_DECISIONS.md).

**IMPLEMENTED.** Package `agent_runtime`. This is the generic dispatcher. It is not `InstagramAgent`, and it does not replace the studio routes.

Inspection of this package: it does not import `agent`, `tools`, `services`, `scheduler`, or `festivals`. It does not read provider keys. It does not construct a Graph client. HTTP handlers still call the existing functions directly.

## Flow

```text
TriggerContext
  ↓
AgentRuntime.run
  ↓
AgentResolver          published pointer in production, draft only in test mode
  ↓
Execution snapshot     immutable copy of the agent version and workflow version
  ↓
WorkflowEngine         ordered steps, registered handlers and injected services
  ↓
Tool / model / knowledge / asset services
  ↓
Approval policy        required before an external effect
  ↓
ExecutionRecord
```

`AgentRuntime.execute` is the same engine entry when the caller already holds `AgentDefinition`, `AgentVersion`, `WorkflowVersion`, `ExecutionContext`, and `TriggerContext`. `run` is the trigger entry: it resolves, seals the snapshot, then calls `execute`.

## What a run records

| Field | Rule |
| --- | --- |
| Execution id | Generated. A payload value named `execution_id` is discarded |
| Correlation id | Taken from the trigger, or generated |
| Tenant id | Taken from `TriggerContext`. A payload `tenant_id` is discarded |
| User id | Taken from the trigger when the caller has one. Omitted for a system run that has no user |
| Trigger | `trigger_key`, mode, and the trigger's timestamp |
| Agent version | Number and `draft` or `published`, copied onto the execution |
| Workflow version | Key and number copied onto the execution |
| Execution status | `pending`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`, `timed_out` |
| Step status | `pending`, `running`, `completed`, `failed`, `skipped`, `cancelled`, `timed_out`, `blocked`, `waiting` |
| Error | `StructuredError`: code, message, retryable, step key, details |
| Idempotency key | Optional. Unique per tenant |

The snapshot is sealed once. A second seal fails. Later edits to the in-memory catalog do not change the steps or grants of a run that already started. Pydantic models for definitions and versions are frozen.

## Production and draft

| Mode | Version that runs | External effect |
| --- | --- | --- |
| `production` | The agent's `published_version_number` only, and that version's status must be `published` | Allowed only when a registered policy returns `allowed=true` and `requires_human=false` |
| `test` | The requested version, including a draft. If no version is requested, the published pointer wins over a draft | A draft version cannot perform an external effect. The policy is not consulted for that step. The effect handler is not called |

An agent that is `paused` or `retired` does not start work in either mode.

Production rejects a request for any version other than the published pointer. A higher draft is not selected just because its number is larger. A second published version is not selected unless the pointer names it.

A test run does not change `published_version_number` and does not change a draft's status. The runtime has no publish operation.

`external_effect` is the generic side effect that needs approval. The package does not name a product publisher. A draft step of that kind ends `blocked` with `DRAFT_CANNOT_PUBLISH`. Later steps are `skipped`. Earlier steps keep `completed`.

If the policy asks for a person, the execution stays `waiting_approval` and the effect handler is not called. If the policy denies, the execution is `failed` with `APPROVAL_DENIED`. If no policy id is bound, or the id is not registered, the effect does not run (`APPROVAL_POLICY_NOT_FOUND`).

A draft may still call a model, load knowledge, or invoke a granted tool. Those are not external effects.

## Dependency injection

`AgentRuntime` takes its collaborators in the constructor. Missing services are fail-closed stubs: a step that needs one fails with a structured error. Nothing is imported from a string.

| Port | Role |
| --- | --- |
| `AgentCatalog` | Load definitions, versions, and workflow versions. `InMemoryCatalog` is the process store. No table was added |
| `HandlerRegistry` | `handler_id` → callable registered by the host process |
| `ToolService` | Invoke a granted tool name |
| `ModelService` | Complete a granted model role |
| `KnowledgeService` | Load a granted source name |
| `AssetService` | Resolve a granted asset role |
| `ApprovalRegistry` | `policy_id` → object with `evaluate` |
| `ExecutionStore` | Records and idempotency claims |
| `Clock`, `IdGenerator`, `ExecutionObserver` | Time, ids, and status events |

Handler ids match `^[a-z][a-z0-9_-]{0,63}$`. `os.system` cannot be a handler id. The registry rejects `eval`, `exec`, and `compile`. A step kind that calls a service (`load_context`, `reason`, `invoke_tool`, `resolve_asset`, `decide_approval`) cannot also carry a handler id, so a workflow cannot smuggle a callable around the grant check.

Service steps fail before the service is called when the name is not on the sealed version's grant list.

Payload keys that mean code (`code`, `script`, `command`, `shell`, `exec`, `eval`, `handler`, `handler_path`, `import_path`, `callable`) are rejected. Secret-like keys and tenant keys are dropped. Secret-like values in strings are redacted. Unexpected exceptions become `INTERNAL_ERROR` with a fixed message. The exception text is not stored.

Observer events carry ids, keys, status, and the error code. They do not carry the payload.

## Workflow engine

The engine walks `ExecutionSnapshot.steps` in order. It passes each handler a copy of the state. A handler must return a JSON object. A string or other result is `STEP_FAILED`.

On a failed step the engine stops. It does not retry and it does not ask another step to choose a different tool. Remaining steps are `skipped`.

Timeouts use `ExecutionPolicy.timeout_seconds` for the whole run (default 120, maximum 3600) and an optional per-step limit. A timeout is `timed_out`, `retryable=true`, and is not run again inside the runtime. Cancellation is a token checked before each step and while a step is in progress. A cancelled step is `cancelled`. Cancelling before start does not call handlers.

Idempotency is `(tenant_id, idempotency_key)`. The first claim owns the execution. A replay, including a replay after failure, returns that same record and does not call handlers again. Two tenants may use the same key. A parallel duplicate observes the in-flight record instead of starting a second run.

User-owned agents are stored per `user_id`. Another tenant's key is `AGENT_NOT_FOUND`. The trigger's `user_id` must match that owner. A system agent has no user id and can run for any tenant. The tenant still comes from the trigger, not from the payload.

## Step kinds

These are the only kinds the engine runs. A product adapter maps its existing functions onto them by registering handlers. The engine does not import those functions.

| Kind | Dispatch |
| --- | --- |
| `load_context` | `KnowledgeService.load` |
| `reason` | `ModelService.complete` |
| `invoke_tool` | `ToolService.invoke` |
| `resolve_asset` | `AssetService.resolve` |
| `decide_approval` | Registered policy, result stored on the step output |
| `validate`, `store`, `human_decision` | Registered handler |
| `effect` | Approval gate, then a registered handler. Side effect `external_effect` |

`decide_approval` records the decision and does not perform the effect. Only `effect`, or any step marked `external_effect`, can reach a side effect, and only after the gate.

Workflow versions used by a run must themselves be `published`. Users do not submit handler paths. A published catalog entry cannot be overwritten in place. An agent can hold one draft version.

## What this package does not do

- It does not publish a draft, and a test run cannot promote a draft.
- It does not retry a failed or timed-out step.
- It does not evaluate, import, or spawn user-supplied code.
- It does not read `META_ACCESS_TOKEN`, provider API keys, or a tenant token.
- It does not branch on a product name, a Graph host, or a tool allowlist from the studio.
- It does not add routes, ORM models, or Alembic revisions.
- It does not change `InstagramAgent`, `ContentOrchestrator`, `CampaignPipeline`, or the scheduler loop.

A later adapter in the product packages can register handlers and pass this runtime an `AgentCatalog`. That adapter is not part of this package. Until a route is switched, studio behavior stays on the direct calls described in the [migration plan](AGENT_PLATFORM_MIGRATION_PLAN.md).

## Files

| Path | Role |
| --- | --- |
| `agent_runtime/runtime.py` | `AgentRuntime.run` and `AgentRuntime.execute` |
| `agent_runtime/resolver.py` | Published pointer, draft test selection, owner check |
| `agent_runtime/engine.py` | Ordered steps, timeout, cancellation, approval gate |
| `agent_runtime/models.py` | Definitions, versions, snapshot, execution record |
| `agent_runtime/services.py` | Protocols, registries, fail-closed services |
| `agent_runtime/catalog.py` | In-memory catalog and published immutability |
| `agent_runtime/store.py` | Execution records and idempotency |
| `agent_runtime/errors.py` | Structured errors |
| `agent_runtime/sanitize.py` | Payload cleaning. Data is not executed |
| `tests/test_agent_runtime.py` | Behavior tests |
| `tests/test_agent_runtime_boundaries.py` | Import and call scan of the package |

## Tests

`tests/test_agent_runtime.py` covers production versus draft, the published pointer, draft external effects, approval deny and human wait, grants, unknown handlers, payload stripping, redacted internal errors, idempotency including parallel and cross-tenant keys, cancellation, timeout, snapshot immutability, paused and retired agents, user-agent isolation, and direct `execute`.

`tests/test_agent_runtime_boundaries.py` fails if the package imports the product publishers or calls `eval`, `exec`, `compile`, or `__import__`.
