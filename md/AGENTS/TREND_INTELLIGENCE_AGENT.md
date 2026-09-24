# Trend intelligence agent

Related: [Trend research](../INTELLIGENCE/TREND_RESEARCH.md), [Trend architecture](../INTELLIGENCE/TREND_INTELLIGENCE_ARCHITECTURE.md), [Trend MCP](../MCP/TREND_MCP.md), [DeepSeek](../AI/DEEPSEEK.md).

There is no class named `TrendIntelligenceAgent`. The implemented pieces are:

| Piece | Module | Role |
| --- | --- | --- |
| Research | `backend/trends/research.py` `TrendResearcher` | HTTP GET of allowlisted news and government URLs. Does not call DeepSeek |
| Analyst | `backend/trends/analyst.py` `DeepSeekTrendAnalyst` | Turns MCP evidence, including Instagram `trend_context`, into a validated `TrendBrief`. **IMPLEMENTED — NOT CONFIGURED** until `DEEPSEEK_API_KEY` is set. Does not call Meta or publish |
| Packet | `backend/trends/packet.py` `TrendIntelligence` | Reads MCP evidence tools, then calls the analyst |
| Scheduler | `scheduler/trend_scheduler.py` `TrendIntelligenceScheduler` | Collects, analyzes, stores a report, may generate content |
| Opportunities | `agent/opportunity_engine.py` `OpportunityEngine` | Ranks a brief into a content request. Does not publish |

## Labels used in code

Research rows are observations with source URL, excerpt, and timestamps. The analyst is instructed to reject invented numbers, ids, and dates. Opportunity copy distinguishes:

| Label | Meaning in this repo |
| --- | --- |
| OBSERVED | A stored `trend_observations` / `trend_evidence` row, or a Graph metric the reader actually returned |
| DISCOVERED | A page fetched from an allowlisted URL during research |
| INFERRED | A `TrendBrief` or gap computed from stored rows (content-type gaps, business gaps) |
| RECOMMENDED | An opportunity or creative direction. Not a publish command |

A trend report is not a publish command. `TrendIntelligenceScheduler` sets `published_because_trend_only` to false. Content generation still goes through `CampaignPipeline` and the user's auto-publish flags.

## When the tick runs

`AutomationRunner.tick` runs the trend scheduler after daily and festival work, and only when daily or festival automation is enabled for that user. Failures are swallowed as `{status: failed, reason: trend_run_failed}` so the other ticks can commit.

See [Scheduler](../AUTOMATION/SCHEDULER.md).
