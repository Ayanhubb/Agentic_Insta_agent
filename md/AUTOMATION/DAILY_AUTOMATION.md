# Daily automation

Related: [Scheduler](SCHEDULER.md), [Festival automation](FESTIVAL_AUTOMATION.md).

Module: `scheduler/daily_scheduler.py`.

## Settings

`automation_settings`: `daily_enabled`, `daily_posts_per_day` (API forces 1), `daily_post_time`, `auto_daily_publish`, `timezone`.

## Tick

Skip when daily is disabled, when local time is before `daily_post_time`, or when `PostRepository.has_published_daily` is already true for that user, account, and local date.

Otherwise the scheduler claims `daily_post_slots` (unique per user, account, local date). A claimed slot runs `CampaignPipeline`.

| Outcome | Slot |
| --- | --- |
| Published | Counts for that local date. Status follows the post |
| Pending approval | Slot stays claimed so a second tick does not generate another post |
| Failure | Slot marked failed |

Duplicate prevention is the slot plus `has_published_daily`. A second successful publish the same local day returns `{status: skipped, reason: already_published}`.

## Content

The daily scheduler uses `ContentAgent` and the app LLM (`LLM_PROVIDER`), not the studio `ContentOrchestrator`. See [Content agent](../AGENTS/CONTENT_AGENT.md).

Auto-publish happens only inside the pipeline when `auto_daily_publish` is true and every gate passes.
