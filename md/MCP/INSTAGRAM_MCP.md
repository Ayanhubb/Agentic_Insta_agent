# Instagram account MCP

Related: [MCP architecture](MCP_ARCHITECTURE.md), [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md), [Account intelligence](../AGENTS/ACCOUNT_INTELLIGENCE_AGENT.md).

Server string: `account`. Module: `backend/mcp/servers/account.py`.

There is no tool that creates or publishes media. Publish names are rejected before the handler runs. The analysis path is Meta Graph → Instagram intelligence → these MCP tools → DeepSeek. DeepSeek does not call Graph. The reasoning key is **IMPLEMENTED — NOT CONFIGURED**.

Authentication: trusted tenant. The Graph token is the Fernet-encrypted token on `instagram_accounts` for that user. Tools do not accept a token argument and do not return one.

## Shared shapes

Live Graph tools, on success: `found`, `source=instagram_account_intelligence`, `trend_context`, `tenant_id`.

The handler does not pass through the full service payload. Raw captions are omitted from `get_recent_media`.

Unavailable: `found: false`, `source`, `metric`, `status: "unavailable"`, `reason`.

`reason` is the `AppError` code (`INSTAGRAM_NOT_CONNECTED`, `AUTHENTICATION_ERROR`, `TIMEOUT`, `API_ERROR`) or a reader failure reason.

## Tools

| Tool | Input | Data source | Read/write | Can publish |
| --- | --- | --- | --- | --- |
| `get_account_summary` | none | `AccountIntelligenceService.account` (Graph profile + derived sample) | read | no |
| `get_recent_media` | optional `limit` 1–25 | Graph media sample | read | no |
| `get_account_insights` | none | Graph insights, including metrics Meta omitted | read | no |
| `get_top_content` | none | Highest and lowest engagement in the returned sample | read | no |
| `get_content_performance` | none | Calls `account` and compares the sample | read | no |
| `get_publishing_history` | optional `limit` 1–25 | `instagram_posts` only. `live_graph: false` | read | no |

`get_publishing_history` success keys: `found`, `source=instagram_posts`, `live_graph`, `account_status`, `instagram_account_id`, `truncated`, `posts`. If the user has no owned account, reason `INSTAGRAM_NOT_CONNECTED`.

`trend_context.captured_at` is the time of the Graph read. Metrics Meta did not return use `status=unavailable` and `value=null`. The trend packet copies only `status=available` metrics. It does not fill a missing number.

The model cannot select an account. `user_id`, `account_id`, and `instagram_account_id` are stripped before the handler runs. The service then checks that the Graph profile id matches the tenant's connected account.

These tools are on the live registry from `build_registry`. `backend/trends/packet.py` is the caller that forwards the normalized context to DeepSeek. None of these tools publish.

## Tests

`tests/test_mcp_account_intelligence.py`, `tests/test_instagram_intelligence.py`, `tests/test_instagram_mcp_deepseek.py`.
