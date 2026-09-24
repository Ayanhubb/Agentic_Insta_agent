# Account intelligence agent

Related: [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md), [Instagram MCP](../MCP/INSTAGRAM_MCP.md), [Instagram API](../API/INSTAGRAM_API.md).

There is no class named `AccountIntelligenceAgent`. Account intelligence is a read path:

```text
Connected Instagram account (instagram_accounts)
        ↓
Authorized Meta Graph API (services/instagram_reader.py, GET only)
        ↓
AccountIntelligenceService (services/instagram_intelligence.py)
        ↓
MCP account tools (trend_context only; no token, no publish)
        ↓
DeepSeek trend analysis (DeepSeekTrendAnalyst)
```

`GET /api/v1/intelligence/*` returns the same normalized metrics to the signed-in user and does not call DeepSeek by itself. Trend analysis is the path that sends MCP context to DeepSeek. That model is **IMPLEMENTED — NOT CONFIGURED** until `DEEPSEEK_API_KEY` is set. DeepSeek does not call Meta.

`AccountIntelligenceService` methods: `account`, `posts`, `insights`, `top_content`. It can persist `instagram_intelligence_records`. It does not create media or publish.

MCP wrappers in `backend/mcp/servers/account.py` (`server="account"`) return a reduced payload: `found`, `source`, `trend_context`. They do not return access tokens or raw captions from `get_recent_media`.

`get_publishing_history` reads `instagram_posts` for the tenant. It does not call Graph (`live_graph: false`).

If the user has no connected account, tools return `found: false`, `status: "unavailable"`, `reason: "INSTAGRAM_NOT_CONNECTED"`.

Derived fields computed in-process (not requested from Graph as their own metrics): `posting_frequency`, `posting_consistency`, `content_mix`, `engagement_rate`, `average_engagement`, `recent_performance`, and product/festival/offer content performance. Those are **INFERRED** from the media sample the reader returned.

Exact Graph fields are listed in [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md).
