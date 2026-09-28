# Trigger architecture

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Scheduler](AGENT_PLATFORM_SCHEDULER_ARCHITECTURE.md), [Runtime](AGENT_PLATFORM_RUNTIME.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Workflow engine](AGENT_PLATFORM_WORKFLOW_ENGINE.md), [Security](AGENT_PLATFORM_SECURITY.md), [Decisions](AGENT_PLATFORM_DECISIONS.md).

Inspection date: 2026-09-25. Package: `agent_platform.triggers`.

## Path

**IMPLEMENTED.** One occurrence follows this path. The dispatcher does not skip the resolver, and it does not call a workflow step itself.

```text
Trigger
  ↓
Agent Resolver
  ↓
Agent Runtime
  ↓
Workflow
  ↓
Execution
```

`TriggerDispatcher.fire` builds a `TriggerContext` and calls `AgentRuntime.run`. That call resolves the published agent version, seals the workflow pin, and runs the workflow engine. The execution record is the runtime's record. The trigger history stores the fire and the execution id.

The package does not import `InstagramAgent`, the six publish tools, or `enqueue_instagram_publication`. A workflow may later perform a side effect through a handler registered outside this package. This package does not register that handler.

## Kinds

| Kind | Who starts it | What the tick does |
| --- | --- | --- |
| `manual` | `TriggerDispatcher.manual` with an idempotency key | Nothing. A tick does not invent a click |
| `schedule` | `TriggerScheduler.tick` when the local time is due | Evaluates the schedule in the trigger's timezone |
| `event` | `TriggerDispatcher.accept` when `source_id` equals the registered source | Nothing. A tick does not poll |
| `webhook` | `TriggerDispatcher.accept` with the same source rule | Nothing. There is no HTTP listener |
| `condition` | `TriggerScheduler.tick` when caller-supplied facts match | Compares one field. There is no expression string |

`EVENT` and `WEBHOOK` are definitions plus an in-process accept gate. Arbitrary external delivery is **NOT IMPLEMENTED**. Accept runs only when the source id was stored on the definition before the call. A different source is history status `rejected`, with no execution id and no runtime call. Payload keys `command`, `script`, `code`, and `handler` are rejected before a claim.

## Definition

A `TriggerDefinition` is tenant-scoped data.

| Field | Role |
| --- | --- |
| `trigger_id` | Stable id inside the tenant |
| `tenant_id` | `users.id` |
| `agent_key` | Any agent in the catalog |
| `trigger_key` | Must match a `TriggerBinding` on the resolved agent version. The binding chooses the workflow |
| `kind` | One of the five kinds |
| `enabled` | `false` records `skipped` and does not call the runtime |
| `timezone` | IANA name. A naive timestamp is refused |
| `schedule_kind` | `daily` or `calendar` for `schedule` |
| `local_time` | `HH:MM` in that timezone. Due at that minute and after |
| `occurrences` | Civil dates supplied by the caller for a calendar schedule |
| `condition` | `eq`, `neq`, `exists`, `not_exists`, or `in` |
| `registered_source` | Required for `event` and `webhook` |
| `static_payload` | JSON scalars merged under the occurrence payload |
| `retry` | `max_attempts` 1–5, listed error codes, delay in seconds |

The trigger does not pin a workflow version. Production mode asks the resolver for `published_version_number`. A draft version on the same agent is left unused.

## Who may run

| Situation | Result | Runtime called |
| --- | --- | --- |
| Agent `active`, version `published`, workflow `published` | `completed` or a step failure | Yes |
| Agent `paused` | `failed`, `AGENT_PAUSED` | Resolver only. Steps do not run |
| Agent `retired` | `failed`, `AGENT_RETIRED` | Resolver only |
| No published version | `failed`, `VERSION_NOT_PUBLISHED` | Resolver only |
| Published version exists beside a draft | The published pointer runs. The draft workflow is unused | Yes, published steps only |
| Trigger `enabled=false` | `skipped`, `TRIGGER_DISABLED` | No |
| Another tenant's trigger id | `trigger_not_found` | No |
| User-owned agent addressed by a different tenant | `failed`, `AGENT_NOT_FOUND` | Resolver only |

Failed fires are stored. A second tick of the same occurrence returns that fire and does not start another execution, unless the retry policy lists the error code, attempts remain, and `next_retry_at` has passed.

## Idempotency

The claim key is `(tenant_id, trigger_id, occurrence_key)`.

| Kind | Occurrence key |
| --- | --- |
| `manual`, `event`, `webhook` | Caller idempotency key |
| `daily` | Local date, `dYYYY-MM-DD` |
| `calendar` | Catalog key, phase code, local date |
| `condition` | Hash of field, operator, and matched value |

The runtime idempotency key is a hash of tenant, trigger, occurrence, and attempt. Attempt 2 is a new execution. Attempt 1 is not run again. Another tenant with the same trigger id gets its own claim.

History is in memory on `TriggerHistory`. `get` and `list` require the tenant. A foreign fire id is missing. This store is **NOT** `agent_tasks` and it is not durable across process restart. Instagram daily slots and festival post rows remain the durable duplicate protection for the current product loop.

## Condition and calendar data

A condition reads a dotted field of letters, digits, and underscores. `__` is rejected. The operators compare JSON scalars. The fact mapping is not executed.

A calendar occurrence is a `CalendarDate`: `occurrence_key`, `occurs_on`, and `label`. The scheduler copies those values into the payload. It does not add a date that was not on the definition.

## Not in this change

- No HTTP route for webhooks or manual fires
- No subscription to an external bus
- No Alembic table for trigger history
- No change to `AutomationRunner`, `DailyScheduler`, or `FestivalScheduler`
