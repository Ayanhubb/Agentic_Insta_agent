# Trend intelligence architecture

Related: [Trend research](TREND_RESEARCH.md), [Trend agent](../AGENTS/TREND_INTELLIGENCE_AGENT.md), [Scheduler](../AUTOMATION/SCHEDULER.md), [Database schema](../DATABASE/DATABASE_SCHEMA.md).

This page replaces the design note that lived in `docs/TREND_INTELLIGENCE_ARCHITECTURE.md`. Where that note described modules that were not in the tree, this page follows the modules that are.

## Pipeline

```text
Meta Graph API (GET only, authorized account)
        ↓
AccountIntelligenceService trend_context
        ↓
MCP account tools (authenticated tenant only)
        ↓
trend_sources → TrendResearcher.collect — DISCOVERED
        ↓
trend_observations + trend_evidence
        ↓
TrendIntelligence packet (MCP reads only)
        account summary, recent content, available metrics,
        historical performance, trend evidence,
        festival, business, and product context
        ↓
DeepSeekTrendAnalyst
        OBSERVED facts, INFERRED interpretations, RECOMMENDED actions
        ↓
trend_reports
        ↓
content_opportunities — RECOMMENDED
        ↓
optional CampaignPipeline when auto-publish flags already allow it
        ↓
Instagram Agent only if approval.publish is true
```

`backend/trends/packet.py` `TrendIntelligence` calls `MCP_EVIDENCE_TOOLS`. Those names are read tools. Publish tools are not in the packet.

## Scheduler behavior

`TrendIntelligenceScheduler` (`scheduler/trend_scheduler.py`):

- Runs from `AutomationRunner` when daily or festival automation is enabled.
- Expires opportunities past `expires_at`.
- Skips when the local post time has not arrived or a report for the day already exists.
- Saves the report and opportunities.
- `_maybe_generate` calls the content pipeline only when the existing auto-publish flag for that lane is on. A festival opportunity also needs `auto_festival_publish` and a real festival catalog item.
- The result dict includes `published` as a count of pipeline publishes and `published_because_trend_only: false`.

Trend tick failures do not roll back the daily and festival work. They return `{status: failed, reason: trend_run_failed}`.

## Account context inside the packet

`collect_mcp_evidence` calls `get_account_summary`, `get_recent_media`, `get_account_insights`, `get_top_content`, and `get_content_performance`. Those handlers ignore `user_id` and `account_id` arguments. The tenant comes from `TenantContext`.

`build_trend_request` keeps metrics whose `status` is `available`, stores `captured_at` on the insight, and records unavailable metrics as gaps. Derived sample comparisons are `instagram_inferences`. DeepSeek must cite an **OBSERVED** insight as OBSERVED and an **INFERRED** insight as INFERRED. A recommendation is not a fact.

Example the analyst is instructed to follow:

- OBSERVED: "5 posts were published during the selected period."
- INFERRED: "Product-focused posts represented a larger share of recent content."
- RECOMMENDED: "Consider testing another product-led creative."

Meta disconnects, expired tokens, missing permissions, empty samples, omitted metrics, timeouts, and rate limits stay inside the tool result. The scheduler's meta fetch and analyzer call catch those failures and still save a report.

## API

Read models and mutations are in [API reference](../API/API_REFERENCE.md) under `/api/v1/trends`. Dashboard `GET /api/v1/dashboard` adds a `trend_intelligence` object from `scheduler/trend_store.py`.

## Gaps

| Item | Status |
| --- | --- |
| Live fetch from allowlisted URLs | Implemented |
| DeepSeek web browsing | NOT IMPLEMENTED |
| Treating a brief as evidence | Rejected by the analyst |
| Publishing because a trend exists | NOT IMPLEMENTED. Auto-publish flags still apply |
| `real_deepseek` trend test | NOT IMPLEMENTED |
| Coverage-only festival names as scheduled campaigns | NOT IMPLEMENTED. Catalog is `FESTIVALS` only |
