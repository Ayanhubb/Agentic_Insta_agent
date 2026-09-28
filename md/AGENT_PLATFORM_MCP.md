# MCP and tools

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [MCP overview](MCP.md), [MCP architecture](MCP/MCP_ARCHITECTURE.md), [Tool registry](AGENT_PLATFORM_TOOL_REGISTRY.md), [MCP runtime](AGENT_PLATFORM_MCP_RUNTIME.md), [Security](AGENT_PLATFORM_SECURITY.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md).

MCP in this repository is an allowlisted tool registry inside the API process. It is not a network server the model can extend. Tools are capabilities registered by server code. They are not arbitrary code the model sends.

## Tool definition

**IMPLEMENTED** for MCP: `MCPTool` in `backend/mcp/registry.py`.

| Field | Rule |
| --- | --- |
| `name` | Must pass `assert_tool_allowed` |
| `description` | Shown by `MCPRegistry.discover` |
| `input_schema` | Object schema. `additionalProperties` is false on the built-in tools |
| `server` | Group name: `business`, `brand`, `asset`, `product`, `festival`, `content`, `account`, `trend` |
| `handler` | `(TenantContext, arguments) -> dict` |

**IMPLEMENTED** for the Instagram agent: `tools.base.Tool`, registered only if `name` is in `ALLOWED_TOOL_SET`.

**PROPOSED** catalog fields that describe both registries without merging them:

| Field | Values in this tree |
| --- | --- |
| `plane` | `mcp_read`, `instagram_publish`, `canva_remote`, `festival_scheduler` |
| `side_effect` | `none` for MCP reads. `instagram_publish` for the six agent tools. `creative_export` for Canva. Festival scheduler tools are reads |
| `secret_class` | `none` on MCP results. `instagram_token` resolved inside the Graph client, never an argument. `canva_token` resolved inside `CanvaAdapter` |
| `tenant_source` | `TenantContext` or the session user. Never model arguments |

A tool the catalog does not list cannot be invoked. Discovery returns name, description, server, and input schema, sorted by name.

## Four registries that already exist

They stay separate. **PROPOSED:** one read-only catalog module lists them so an agent grant can name a plane. The catalog does not register a tool onto a second plane.

### 1. In-process MCP — plane `mcp_read`

**IMPLEMENTED.** `build_registry` in `backend/mcp/servers/__init__.py`. 22 tools.

| Server | Tools |
| --- | --- |
| `business` | `get_business_profile` |
| `brand` | `get_brand_guidelines` |
| `asset` | `get_company_logo` |
| `product` | `get_product`, `get_product_image`, `get_active_offers` |
| `festival` | `get_upcoming_festivals`, `get_festival_details` |
| `content` | `get_content_rules` |
| `account` | `get_account_summary`, `get_recent_media`, `get_account_insights`, `get_top_content`, `get_content_performance`, `get_publishing_history` |
| `trend` | `get_current_trends`, `get_trend_evidence`, `get_regional_trends`, `get_festival_opportunities`, `get_business_opportunities`, `get_content_opportunities`, `get_latest_trend_report` |

Every handler reads repositories or Graph GET. None insert posts, none call `PublicationService`, and none accept a publish flag.

`MCPClient.invoke`:

1. `require_trusted_tenant`.
2. `strip_model_tenant_arguments` removes `tenant_id`, `user_id`, `account_id`, `instagram_account_id`, `business_id`, and similar keys.
3. `registry.get` — name must be on `MCP_TOOL_ALLOWLIST`.
4. `validate_arguments`.
5. `asyncio.wait_for` — default 5 seconds.
6. `sanitize_payload`, then the client injects `tenant_id`.

| Error | When |
| --- | --- |
| `TENANT_CONTEXT_REQUIRED` | No trusted tenant |
| `UNKNOWN_TOOL` | Name not on the allowlist |
| `TOOL_NOT_ALLOWED` | Publish alias or an Instagram agent tool name |
| `MALFORMED_ARGUMENTS` | Schema failure |
| `TIMEOUT` | Handler exceeded the client timeout |
| `ASSET_ACCESS_DENIED` | Asset id is not owned by the tenant |
| `INTERNAL_ERROR` / `MCP_ERROR` | Unexpected handler failure |

Publish names rejected before any handler: `publish`, `media_publish`, `instagram_publish`, `post_to_instagram`, `create_media`, `publish_media`, plus `validate_image`, `prepare_image`, `upload_image`, `create_instagram_media`, `publish_instagram_media`, `verify_publication`.

`RepositoryGateway` opens a session and scopes queries with `tenant.tenant_id`. Account Graph tools use `AccountIntelligenceService` with the tenant's stored token. Trend tools read stored observations. They do not fetch URLs.

### 2. Instagram agent tools — plane `instagram_publish`

**IMPLEMENTED.** `agent/agent.py` `build_registry` and `agent/registry.py`.

| Tool | Step |
| --- | --- |
| `validate_image` | Type and size |
| `prepare_image` | Resize and encode |
| `upload_image` | File Meta can fetch |
| `create_instagram_media` | Graph media container |
| `publish_instagram_media` | Graph publish |
| `verify_publication` | Read the media back |

Only `InstagramAgent` holds this registry. `enqueue_instagram_publication` is the handoff. Content agent, scheduler, MCP, Canva, DeepSeek, OpenAI, and React do not import these tools to publish.

### 3. Canva — plane `canva_remote`

**IMPLEMENTED — NOT CONFIGURED.** `CANVA_ENABLED` defaults to false. Remote MCP at `https://mcp.canva.com/mcp`. Not on `MCP_TOOL_ALLOWLIST`.

`CanvaAdapter` uses the signed-in user's encrypted token. The content workflow calls `query` for plan facts and `produce` when the creative plan selects `apply_template` or `use_reference`. Export bytes go to vision QA and approval. The adapter has no Instagram publish tool. Tool names that contain `publish` or `instagram` are refused before the remote call.

Canva tokens stay in `canva_connections` and in the adapter session. They are not sent to DeepSeek, OpenAI, React, MCP tool results, or logs. A Canva export download does not attach the access token.

### 4. Festival scheduler adapter — plane `festival_scheduler`

**IMPLEMENTED.** `festivals/mcp.py` and `festivals/mcp_tools.py` (`FestivalMcp`). Not on `MCP_TOOL_ALLOWLIST`. Extra tools: `get_regional_festivals`, `get_festival_campaign`, `get_business_relevance`. They do not publish.

The platform catalog lists them as scheduler-only. It does not add them to the model-facing allowlist unless a later reviewed change does that on purpose. This proposal does not.

## Grants

**PROPOSED.** An agent's tool grant is a set of names on `mcp_read`, plus a boolean `canva` that follows `CANVA_ENABLED` and the user's connection. The grant cannot include `instagram_publish` names. The publish workflow does not consult the grant. It uses `ALLOWED_TOOL_SET` inside the agent.

`get_content_rules` stays a model-facing tool and keeps `publishing.available: false` and `user_prompt_auto_publish: false`.

## What models receive

Models receive tool results the orchestrator or the trend packet already assembled, or a creative plan schema. They do not receive a general tool-calling channel.

`LLMProvider.generate_content_plan` returns a `ContentPlan`. The creative model returns a `CreativePlan`. The trend analyst returns a brief distinguished as OBSERVED, INFERRED, and RECOMMENDED. None of those interfaces register tools.

**PROPOSED** and unchanged in spirit: if a later provider API supports tool calls, the control plane may expose only the granted `mcp_read` names, must strip tenant arguments, and must reject publish names with `TOOL_NOT_ALLOWED` before a handler runs. The model still does not execute the handler. `MCPClient.invoke` does. This mode is not in the code today. `LLMPlanner` is the stub that would have proposed publish tools, and it stays disabled.

## Adding a tool

**PROPOSED** procedure, matching how tools are added now:

1. Write a handler that takes `TenantContext` and a dict.
2. Declare an object schema with `additionalProperties: false`.
3. Add the name to `MCP_TOOL_ALLOWLIST` only if the handler is a read and does not call Graph create/publish/verify.
4. Register it in `build_registry`.
5. Cover cross-tenant denial in the style of `tests/test_mcp.py`.

A side-effect tool follows the Instagram pattern: its own registry, its own gateway, no MCP registration.

Remote MCP servers other than Canva are not in the tree. Adding one is a new integration with the same rules as Canva: user token encrypted at rest, no publish, no token in model context. This proposal does not add one.

## Tests that lock the boundary

**IMPLEMENTED:** `tests/test_mcp.py`, `tests/test_mcp_account_intelligence.py`, `tests/test_trend_mcp.py`, `tests/test_canva_mcp.py`, `tests/test_instagram_mcp_deepseek.py`. Migration must keep the publish-alias tests green. See [migration](AGENT_PLATFORM_MIGRATION_PLAN.md).
