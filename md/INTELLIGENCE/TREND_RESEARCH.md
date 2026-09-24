# Trend research

Related: [Trend architecture](TREND_INTELLIGENCE_ARCHITECTURE.md), [Trend MCP](../MCP/TREND_MCP.md), [DeepSeek](../AI/DEEPSEEK.md), [Content opportunities](CONTENT_OPPORTUNITIES.md).

## What is current

A trend is **current** only when `TrendResearcher` has fetched an allowlisted URL and stored an observation whose freshness window still includes `as_of`. MCP `get_current_trends` drops rows outside that window (`stale_excluded`). DeepSeek is not asked to browse, and a model sentence is not treated as a source.

| Label | What the code stores |
| --- | --- |
| DISCOVERED | HTML fetched by `HttpSourceFetcher` from an explicit URL |
| OBSERVED | `trend_observations` plus `trend_evidence` excerpt, `observed_at`, optional `published_at`, `valid_until` |
| INFERRED | Analyst `TrendBrief`, or gap tools that compare stored posts and catalog fields |
| RECOMMENDED | `content_opportunities` and creative direction. Not a publish |

If the user has no saved source URLs, the scheduler has nothing to fetch. Empty research is not filled with invented headlines.

## Fetcher

`backend/trends/research.py`.

- User-Agent: `YottoTrendResearch/1.0`
- GET only, after a robots.txt check
- Hosts in `PERMITTED_HOSTS`: `thehindu.com`, `indianexpress.com`, `timesofindia.indiatimes.com`, `economictimes.indiatimes.com`, `livemint.com`, `business-standard.com`, `hindustantimes.com`, `ndtv.com`, `pib.gov.in`, `india.gov.in`, `wb.gov.in`
- Blocked suffixes include Instagram and Facebook CDNs
- Query keys `access_token`, `api_key`, `password`, `token`, `sessionid` are stripped
- `TrendResearcher.collect(urls)` accepts explicit URLs. The scheduler passes the user's saved `trend_sources` URLs

Place strings in the page text can be tagged with region codes such as `IN-WB` or `IN-WB-KOLKATA`. That tag is a match against `_PLACES` in the fetched text. It is not a live regional API.

## Freshness

Settings: `TREND_FAST_HOURS` (6), `TREND_CURRENT_DAYS` (7), `TREND_SEASONAL_DAYS` (60), `TREND_EVERGREEN_DAYS` (180). `FreshnessPolicy` uses those windows. Stale observations stay in the database and are excluded from the current tool.

## India, retail, and F&B

Research is India-oriented because the allowlist is Indian news and government hosts, and the festival catalog is Indian. Retail and F&B relevance is **INFERRED** by the analyst and by `festivals/intelligence.py` coverage metadata (retail/F&B flags). The fetcher does not call a retail or F&B dataset.

`festivals/data/coverage.json` adds place aliases and scope labels. Names that appear only there (for example Hariyali Teej) are not in `FESTIVALS` and are not scheduled. See [Festival automation](../AUTOMATION/FESTIVAL_AUTOMATION.md).

## Storage and API

Tables: `trend_sources`, `trend_observations`, `trend_evidence`, `trend_reports`. See [Schema](../DATABASE/DATABASE_SCHEMA.md).

Routes under `/api/v1/trends` are listed in [API reference](../API/API_REFERENCE.md). They are owner-scoped reads plus dismiss, save, and create-content. Create-content returns a prompt for `/generate`. It does not publish.

## DeepSeek

`DeepSeekTrendAnalyst.analyze` calls `DeepSeekLLMProvider.generate_structured` and validates a `TrendBrief`. It rejects briefs that invent numbers, ids, or dates not present in the packet. Retries use `llm_max_attempts` with the rejection text as feedback.

Optional `DeepSeekCreativeVision.describe_creative` can describe a creative asset. That description is not a trend source.

## Tests

`tests/test_trend_research.py`, `tests/test_trend_analysis.py` (mocked DeepSeek), `tests/test_trend_storage.py`, `tests/test_trends_api.py`, `tests/test_trend_scheduler.py`.
