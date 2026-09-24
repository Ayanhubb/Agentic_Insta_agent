# Trend intelligence architecture

Related: [Trend research](TREND_RESEARCH.md), [Trend agent](../AGENTS/TREND_INTELLIGENCE_AGENT.md), [Scheduler](../AUTOMATION/SCHEDULER.md), [Database schema](../DATABASE/DATABASE_SCHEMA.md).

This page replaces the design note that lived in `docs/TREND_INTELLIGENCE_ARCHITECTURE.md`. Where that note described modules that were not in the tree, this page follows the modules that are.

## Pipeline

```text
trend_sources (user URLs, enabled)
        ↓
TrendResearcher.collect  — DISCOVERED pages, allowlisted hosts
        ↓
trend_observations + trend_evidence  — OBSERVED
        ↓
TrendIntelligence packet (MCP reads only)
        ↓
DeepSeekTrendAnalyst  — INFERRED TrendBrief
        ↓
trend_reports
        ↓
content_opportunities  — RECOMMENDED
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

Account MCP tools may be included as evidence when a token exists. Missing insights stay unavailable. The analyst is not given permission to fill them.

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
