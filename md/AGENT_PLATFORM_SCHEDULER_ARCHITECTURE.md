# Scheduler architecture

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Triggers](AGENT_PLATFORM_TRIGGER_ARCHITECTURE.md), [Runtime](AGENT_PLATFORM_RUNTIME.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md), [Decisions](AGENT_PLATFORM_DECISIONS.md).

Inspection date: 2026-09-25. Generic tick: `agent_platform.triggers.TriggerScheduler`. Product loop: `scheduler.scheduler.AutomationRunner`.

## Two schedulers

**IMPLEMENTED** product loop, unchanged. `scheduler_loop` calls `AutomationRunner.tick`. That tick still runs `DailyScheduler`, then `FestivalScheduler`, then the trend scheduler. `SCHEDULER_ENABLED` is still the process switch. Those classes still own Instagram campaign slots, festival post rows, and approval. They still hand publish to `PublicationService`. The generic tick is not called from `create_app`.

**IMPLEMENTED** generic tick. `TriggerScheduler.tick(now)` loads enabled trigger definitions and starts any published agent whose schedule or condition is due.

```text
TriggerScheduler.tick
  ↓
due schedule or condition
  ↓
TriggerDispatcher.fire
  ↓
Agent Resolver
  ↓
Agent Runtime
  ↓
Workflow
  ↓
Execution
```

The generic tick starts `manual` only when something calls `manual`. It does not poll `event` or `webhook` definitions. It does not open a socket. It does not read `META_ACCESS_TOKEN`. It does not construct a Graph client. Meta publication stays inside the Instagram publish workflow, which this tick does not import.

## Tick rules

`now` must be timezone-aware. The schedule is interpreted in the definition's IANA timezone. Asia/Kolkata 10:00 is due at 04:30 UTC. 09:59 local is not due.

| Schedule | Due when |
| --- | --- |
| `daily` | Local time is at or after `local_time`. One occurrence per local date |
| `calendar` | Local date equals a supplied civil date, or that date minus `lead_days` |

When the lead date and the civil date are the same day, the tick keeps the on-date phase unless `allow_same_day` is true. The day after a supplied date produces nothing for that row. Catch-up after the date stays in `FestivalService.due_campaigns`. The generic calendar does not add it.

A disabled definition is omitted. A paused, retired, or unpublished agent is a failed fire. The workflow steps do not run. The failure stays in trigger history. The next tick of that occurrence does not call the runtime again.

## Retry

`RetryPolicy.max_attempts` is 1 to 5. A failed occurrence is started again only when all of these are true:

- the error code is listed in `retryable_codes`
- the attempt number is still below `max_attempts`
- `now` is at or after `next_retry_at`

A runtime `retryable` flag does not start a second execution by itself. Instagram publish recovery stays in `RecoveryPolicy` on the product path. The Instagram trigger configuration uses one attempt so this layer does not generate a second campaign for the same occurrence.

## Execution history

Each fire stores tenant, trigger, agent, kind, occurrence, attempt, status, execution id, error code, and timestamps. Statuses are `claimed`, `completed`, `failed`, `waiting`, `skipped`, and `rejected`.

`waiting` is the runtime's `waiting_approval` state. It is not retried and it is not completed. A failed approval or a failed step is `failed`.

History queries are tenant-scoped. The store is in-process. Restart durability for the generic tick is **NOT IMPLEMENTED**. The product loop's `daily_slots`, `festival_posts`, and `scheduled_jobs` rows are unchanged.

## Instagram as configuration

`scheduler/trigger_configuration.py` builds two `TriggerDefinition` values for one tenant. It does not run them and it does not publish.

| Product setting | Trigger |
| --- | --- |
| `daily_enabled`, `daily_post_time`, `timezone` | `instagram_daily`, agent `instagram_marketing`, trigger key `scheduler.daily`, payload mode `daily` |
| `festival_enabled`, `pre_festival_days`, `allow_same_day_festival_posts` | `instagram_festival`, trigger key `scheduler.festival`, payload mode `festival` |
| Festival local time | `10:00`, the same clock time `FestivalScheduler` writes on `scheduled_for` |

Dates come from `festivals_for_year`. That function reads `FESTIVALS` and `festivals/data/lunar_dates.json`. The configuration copies `festival_name` and the catalog date. It does not ask a model for a date. Phase names on that definition are `pre-festival` and `festival-day`, matching `FestivalService.due_campaigns`.

`DailyScheduler.run_user` and `FestivalScheduler.run_user` remain the executors for the live loop. Registering the configuration on `TriggerScheduler` is a later cutover. Doing both in one tick would start the campaign twice.

The published `instagram_marketing` version, when it is registered, binds `scheduler.daily` and `scheduler.festival` to the existing campaign workflow. The generic tick can start that agent. The handler that publishes still has to be the current pipeline, registered outside `agent_platform.triggers`.

## Tests

`tests/test_trigger_platform.py` covers two agents, tenant isolation, paused and retired agents, draft versions, timezone, idempotency, retry, disabled triggers, event and webhook rejection, conditions, catalog dates, and a source check that the trigger package does not import the Instagram publisher.

## Not in this change

- `AutomationRunner` is not rewritten
- Festival campaign counting, same-day blocking, and catch-up stay in `festivals/` and `scheduler/festival_scheduler.py`
- No new queue and no new cron process
- No webhook URL
