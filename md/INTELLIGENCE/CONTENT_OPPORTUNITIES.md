# Content opportunities

Related: [Trend research](TREND_RESEARCH.md), [Trend MCP](../MCP/TREND_MCP.md), [Content agent](../AGENTS/CONTENT_AGENT.md).

## Stored rows

Table `content_opportunities` (`db/models.py`): `user_id`, `business_id`, `trend_id`, `title`, `why_now`, `creative_direction`, `recommended_format`, optional `product_id`, optional `festival_id`, `confidence`, `expires_at`, `status`, timestamps.

These rows are **RECOMMENDED**. They point at a `trend_id` when the scheduler saved one. They are not themselves evidence.

## How they are produced

1. `TrendIntelligenceScheduler` writes opportunities after a report.
2. `get_content_opportunities` (MCP) builds gaps from `instagram_posts` aggregates: a post type with no verified publish in range becomes a gap. **INFERRED**.
3. `get_business_opportunities` builds product, offer, and brand gaps from catalog tables. **INFERRED**.
4. `get_festival_opportunities` lists upcoming catalog festivals and whether this tenant has a campaign. The festival date is **OBSERVED** from the catalog. The "you have no campaign" flag is **INFERRED**.
5. `OpportunityEngine.evaluate` ranks a `TrendBrief` into one `ContentOrchestrationRequest`. `OpportunityBridge.published` is always false.

`recommend_format` marks carousel, reel, and story as not publishable by the Instagram Agent. The bridge text says the agent can publish one still image after approval.

Low confidence or `instagram_can_publish` false forces `auto_daily_publish` and `auto_festival_publish` off for that decision.

## API

| Method | Path | Effect |
| --- | --- | --- |
| GET | `/api/v1/trends/opportunities` | List for this user. Optional `industry`, `region` |
| POST | `/api/v1/trends/opportunities/{id}/dismiss` | Status change. 404 if not owned |
| POST | `/api/v1/trends/opportunities/{id}/save` | Status change. 404 if missing |
| GET | `/api/v1/trends/opportunities/{id}/evidence` | Linked evidence plus a note |
| POST | `/api/v1/trends/opportunities/{id}/create-content` | Returns `path: "/generate"`, a prompt, and a disclaimer. Expired rows 404. Does not generate or publish |

## Tests

`tests/test_opportunity_engine.py`, `tests/test_trends_api.py`, `tests/test_trend_scheduler.py`.
