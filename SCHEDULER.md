# Autonomous scheduler

Default timezone: **Asia/Kolkata**. All “today” decisions use the user’s automation timezone. Timestamps are stored timezone-aware (UTC).

## Layout

```text
scheduler/
  scheduler.py            AutomationRunner + in-process loop
  daily_scheduler.py      1 verified daily post per account
  festival_scheduler.py   festival campaigns (default 2 verified posts)
  job_manager.py          last-run records
  policies.py             what counts as published, post-time checks
festivals/
  india_festivals.py      data-driven India catalog
  festival_service.py     campaign windows
  data/lunar_dates.json   annual lunar/Islamic date table
```

## Daily flow

1. Skip if `daily_enabled` is false.
2. Skip until `daily_post_time` in the account timezone (`POST /automation/run-now` forces this).
3. Skip if a **PUBLISHED** `DAILY_RETAIL_POST` already exists for `(user, Instagram account, local date)`.
4. Claim `daily_post_slots` (unique per user/account/date). Restart reclaims `CLAIMED`/`FAILED` slots; it never creates a second published row.
5. Create a Content Strategy Agent task → image generation → auto-approve when enabled → Instagram Agent → verification → database.

Only a verified Instagram publication (`status = PUBLISHED` and media id present) counts. `FAILED` can retry the same date. `AMBIGUOUS_PUBLICATION` is not published and is not retried the same day (duplicate risk).

Database protection: partial unique index on successful daily posts plus the daily slot unique constraint.

## APIs

| Method | Path |
| --- | --- |
| GET | `/api/v1/automation` |
| PUT | `/api/v1/automation` |
| POST | `/api/v1/automation/run-now` |

Enable the loop with `SCHEDULER_ENABLED=true`. Use a single uvicorn worker so two processes cannot both tick.
