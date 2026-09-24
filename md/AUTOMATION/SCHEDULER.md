# Scheduler

Related: [Daily automation](DAILY_AUTOMATION.md), [Festival automation](FESTIVAL_AUTOMATION.md), [Trend architecture](../INTELLIGENCE/TREND_INTELLIGENCE_ARCHITECTURE.md), [Instagram agent](../AGENTS/INSTAGRAM_AGENT.md).

## Loop

`SCHEDULER_ENABLED` defaults to false. When true, `create_app` starts `scheduler_loop` (`scheduler/scheduler.py`). Interval: `SCHEDULER_INTERVAL_SECONDS`, default 60, minimum 5. A failed tick is logged and the loop continues.

`AutomationRunner.tick` opens a session, runs daily, then festival, then trend, then commits. An exception rolls the session back and is re-raised to the loop. Trend failures inside the trend scheduler are caught and returned as `{status: failed, reason: trend_run_failed}` so they do not fail the whole tick.

`POST /api/v1/automation/run-now` calls `tick` for the signed-in user only.

Docker Compose sets the scheduler on for the backend service. Local `.env.example` leaves it false.

## Pipeline

`CampaignPipeline` (`scheduler/pipeline.py`):

```text
content agent → optional Canva → image QA → decide_approval → PublicationService
```

`PublicationService.publish_generated_image` runs only when `ApprovalDecision.publish` is true. That service enqueues the Instagram Agent. Content agent, Canva, DeepSeek, and OpenAI do not publish.

## Approval

`scheduler/approval_policy.py`.

Manual lanes (auto flag off) stay `PENDING_APPROVAL`.

Automatic lanes require `auto_daily_publish` or `auto_festival_publish`. They still refuse publish when any of these reasons is set: `image_qa_failed`, `provider_result_malformed`, `product_reference_missing`, `required_logo_missing`, `offer_cannot_be_verified`, `festival_context_invalid`, `tenant_ownership_failed`.

`PUT /api/v1/automation` accepts those flags. If `daily_posts_per_day` is sent, the handler forces it to 1.

## Timezone

`automation_settings.timezone`, default `DEFAULT_TIMEZONE` (`Asia/Kolkata`). Festival dates use `Asia/Kolkata` as well (`festivals/campaign_rules.py`).

## Jobs table

`scheduled_jobs` stores per-user job rows. The in-process runner does not depend on an external queue.

## Tests

`tests/test_scheduler.py`, `tests/test_trend_scheduler.py`, `tests/test_approval_policy.py`, `tests/test_instagram_agent_integration.py`.
