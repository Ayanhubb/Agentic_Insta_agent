# Festival automation

Each enabled festival campaign defaults to **2 verified Instagram publications**.

```text
required_posts      default 2
generated_posts     images produced (including unpublished)
published_posts     VERIFIED publications only
remaining_posts     required_posts - published_posts
```

If `required = 2` and `published = 1`, `remaining = 1`. The next scheduler tick continues the same campaign. Failures and ambiguous publishes do **not** reset `required_posts` or count as published.

## Timing

Default slots:

1. **pre-festival** — `festival_date - pre_festival_days` (default 1 day)
2. **festival-day** — `festival_date`

Both are not published on the same calendar day unless `allow_same_day_festival_posts` is enabled (`PUT /api/v1/festivals/settings`).

Catch-up is allowed for at most 7 days after the festival, still one post per day.

## Calendar

`festivals/india_festivals.py` lists national and regional festivals. Gregorian-fixed dates use month/day. Lunar, lunisolar, and Islamic dates are stored **per year** (see `festivals/data/lunar_dates.json`). Do not compute those dates. Update the table when a new year is published. Moon-sighting may shift Islamic dates by one day.

## APIs

| Method | Path |
| --- | --- |
| GET | `/api/v1/festivals` |
| GET | `/api/v1/festivals/campaigns` |
| PUT | `/api/v1/festivals/settings` |
