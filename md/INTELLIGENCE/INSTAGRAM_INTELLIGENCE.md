# Instagram intelligence

Related: [Account intelligence](../AGENTS/ACCOUNT_INTELLIGENCE_AGENT.md), [Instagram MCP](../MCP/INSTAGRAM_MCP.md), [Instagram API](../API/INSTAGRAM_API.md).

## Flow

```text
Connected Instagram account
        ↓
Authorized Meta Graph API (GET only)
        ↓
InstagramIntelligenceReader
        ↓
AccountIntelligenceService
        ↓
Account metrics (available or unavailable per field)
        ↓
Stored instagram_intelligence_records and trend_context
        ↓
DeepSeek only if a later analyst or creative call is made
```

Reader: `services/instagram_reader.py`. Service: `services/instagram_intelligence.py`. Routes: `api/intelligence_routes.py`.

The reader never calls create or publish.

## HTTP routes

All require `require_password_ok` and use `user.id`.

| Method | Path | Service method |
| --- | --- | --- |
| GET | `/api/v1/intelligence/account` | `account` |
| GET | `/api/v1/intelligence/account/posts` | `posts` |
| GET | `/api/v1/intelligence/account/insights` | `insights` |
| GET | `/api/v1/intelligence/account/top-content` | `top_content` |

`GET /api/v1/trends/account` returns the same style of observed account payload for the trend UI.

## Profile fields requested

`id`, `username`, `name`, `biography`, `followers_count`, `follows_count`, `media_count`, `website`.

If Meta omits a field, that field is marked unavailable. Status: **AVAILABLE** when present in the response, **UNAVAILABLE** when omitted. No extra permission probe is implemented beyond the call itself.

## Media fields requested

`id`, `caption`, `media_type`, `media_product_type`, `timestamp`, `permalink`, `like_count`, `comments_count`.

`like_count` and `comments_count` are **UNAVAILABLE** when Meta omits them. MCP `get_recent_media` does not return raw captions.

## Account insights requested

Each metric uses period `day`. A failed metric is recorded unavailable and does not fail the whole account read, except `rate_limited` and `timeout`, which stop the loop and blank the remaining insights with that reason.

| Metric | Status |
| --- | --- |
| `reach` | Requested. **AVAILABLE** if Meta returns it, else **UNAVAILABLE** |
| `impressions` | Requested. Same. Some professional accounts no longer receive impressions; the code still asks and records the failure |
| `profile_views` | Requested. Same |
| `follower_count` | Requested. Same |
| `accounts_engaged` | Requested. Same |

Insights that need `instagram_manage_insights` (or the current equivalent) and are rejected by Meta are **REQUIRES_PERMISSION** in practice: the reader stores the error reason and does not invent a number. The code does not map that Graph error onto a separate enum named `REQUIRES_PERMISSION`.

## Media insights requested

First three media items. Period `lifetime`.

| Metric | Status |
| --- | --- |
| `reach` | Requested per media. Optional |
| `saved` | Requested per media. Optional |
| `total_interactions` | Requested per media. Optional |

## Not requested

The reader does not request stories, reels-only insight sets, demographics, online followers, profile link taps, follows/unfollows breakdowns, or paid promotion metrics. Those are **NOT IMPLEMENTED**.

Shares are a column on `media_snapshots` but are not in the media-insight request list above. Do not treat `shares` as a live Graph metric unless a later reader change adds it.

## Derived (INFERRED)

Computed from the sample, not from a Graph metric of the same name: `posting_frequency`, `posting_consistency`, `content_mix`, `engagement_rate`, `average_engagement`, `recent_performance`, `{product,festival,offer}_content_performance`.

## Persistence

`account_snapshots`, `media_snapshots`, `insight_snapshots`, and `instagram_intelligence_records` store what was returned. They are not a second source of truth for numbers Meta did not send.
