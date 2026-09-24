# Autonomous scheduler

Default timezone: **Asia/Kolkata**. All “today” decisions use the user’s automation timezone. Timestamps are stored timezone-aware (UTC).

The scheduler never publishes. It builds context, asks the Content Agent for a plan and image, reviews that image, and only then calls `PublicationService`. The Instagram Agent is the only component that talks to Meta.

## Layout

```text
scheduler/
  scheduler.py            AutomationRunner + in-process loop
  daily_scheduler.py      1 verified daily post per account
  festival_scheduler.py   festival campaigns (default 2 verified posts)
  pipeline.py             MCP → content → optional Canva → QA → approval → gateway
  approval_policy.py      human mode always pending; failed QA never auto-publishes
  image_qa.py             structural check, or DeepSeek Vision when configured
  integrations.py         resolve vision, festival MCP, and Canva
  job_manager.py          last-run records
  policies.py             what counts as published, post-time checks
festivals/
  india_festivals.py      data-driven India catalog (date source of truth)
  festival_service.py     campaign windows
  mcp.py                  FestivalMcp used by the pipeline
  mcp_tools.py            regional filter and stored lunar dates
  data/lunar_dates.json   annual lunar/Islamic date table — do not compute dates
```

Enable the loop with `SCHEDULER_ENABLED=true`. Use a single uvicorn worker so two processes cannot both tick.

## Pipeline

`DailyScheduler` and `FestivalScheduler` both call `CampaignPipeline.execute`:

1. Load festival context from the catalog (`festivals/mcp.py`). DeepSeek is not asked for dates.
2. Content Agent plans and generates an image. `published` stays false.
3. Optional Canva design, only when Canva is enabled and connected. If it is not, OpenAI image generation is unchanged.
4. Image QA. A configured DeepSeek vision provider reviews the file. Otherwise a structural check runs. The result is stored on `generated_images.qa_status` and `qa_json`.
5. `decide_approval`. Automatic publish requires every gate: QA passed, ownership, a catalog product when the plan features one, a logo only when required, a verifiable offer, and a valid festival date in festival mode.
6. On approval, `PublicationService.publish_generated_image` enqueues the Instagram Agent.

Failed QA, a malformed provider result, or a failed ownership check stops before Instagram. Human mode (`auto_daily_publish` / `auto_festival_publish` false) always stays `PENDING_APPROVAL`.

Independent MCP reads (festival, business, brand, product) are gathered in parallel before DeepSeek. Steps that need the plan or the image stay sequential.

## Daily flow

1. Skip if `daily_enabled` is false.
2. Skip until `daily_post_time` in the account timezone (`POST /automation/run-now` forces this).
3. Skip if a **PUBLISHED** `DAILY_RETAIL_POST` already exists for `(user, Instagram account, local date)`.
4. Claim `daily_post_slots` (unique per user/account/date). Restart reclaims `CLAIMED`/`FAILED` slots. It never creates a second published row. `AMBIGUOUS` is not reclaimed for another publish.
5. Run the pipeline above with `post_type=DAILY_RETAIL_POST` and `trigger=DAILY_AUTOMATION`.

Only a verified Instagram publication (`status = PUBLISHED` and media id present) counts. The daily count uses `scheduled_date`, the automation day in the user's timezone. `FAILED` can retry the same date. `AMBIGUOUS_PUBLICATION` is not published again (duplicate risk).

Database protection: partial unique index on successful daily posts plus the daily slot unique constraint.

## Festival flow

- Skip if `festival_enabled` is false.
- Default `required_posts = 2`: one post on the pre-festival day (`festival_date - pre_festival_days`, default 1) and one on the festival day.
- Same calendar day is used only when `allow_same_day_festival_posts` is set.
- Catch-up runs for up to 7 days after the festival, still one post per day. A failure does not reset `required_posts` or increment `published_posts`.
- Dates, including lunar dates, come from `festivals/india_festivals.py` and `festivals/data/lunar_dates.json`. Regional filters (for example West Bengal / Kolkata) use that catalog. Diwali 2026 in the catalog is 2026-11-08.
- A second run the same day does not create a duplicate campaign or a second publish after timeout.

## APIs

| Method | Path |
| --- | --- |
| GET | `/api/v1/automation` |
| PUT | `/api/v1/automation` |
| POST | `/api/v1/automation/run-now` |

`AutomationRunner.tick(user_id=)` is what `run-now` calls. It resolves vision, festival MCP, and Canva, then runs the daily scheduler and the festival scheduler.
