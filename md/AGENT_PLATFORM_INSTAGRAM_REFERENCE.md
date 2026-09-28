# Instagram marketing agent — reference implementation

> **Status on 2026-09-28.** Agents, versions, and executions are in Alembic `007_platform_agents`. Shadow mode can run `instagram.reference` and cannot publish. The generic runtime does not own `POST /api/v1/instagram/publish`. Knowledge documents, asset records, and trigger state are still not separate tables. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say agents are not stored, or that a package does not exist yet, are historical.

Related: [Instagram agent](AGENTS/INSTAGRAM_AGENT.md), [Content agent](AGENTS/CONTENT_AGENT.md), [Trend intelligence](INTELLIGENCE/TREND_INTELLIGENCE_ARCHITECTURE.md), [Scheduler](AUTOMATION/SCHEDULER.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md).

The Instagram marketing agent is the product that already ships. On the platform its id is `instagram_marketing`. Registration is **PROPOSED**. Behavior in this page is **IMPLEMENTED**.

The generic runtime must not grow Instagram tool names, Graph hosts, festival dates, or caption limits. Those stay in the modules below. The platform calls them.

## Identity

| Field | Value | Status |
| --- | --- | --- |
| Id | `instagram_marketing` | **PROPOSED** name for existing behavior |
| Owner | `system` | **PROPOSED** |
| Product surface | React studio and `/api/v1` routes documented in [API reference](API/API_REFERENCE.md) | **IMPLEMENTED** |
| Publish authority | `InstagramAgent` | **IMPLEMENTED** |
| Planner for publish | `DeterministicPlanner` | **IMPLEMENTED** |
| LLM publish planner | `LLMPlanner` | **PARTIAL** stub, not wired |

There is no second publisher under `backend/`. Platform code must not add one.

## Workflows

| Id | Implementation | Publishes |
| --- | --- | --- |
| `studio.generate` | `ContentOrchestrator` | No |
| `studio.regenerate` | regenerate route | No |
| `studio.approve` | approve route → `PublicationService` | Yes, via the agent |
| `direct.publish` | `POST /instagram/publish` | Yes, via the agent |
| `scheduler.campaign` | `CampaignPipeline` | Only when `ApprovalDecision.publish` is true |
| `trend.cycle` | `TrendIntelligenceScheduler` | Only by calling `scheduler.campaign` under existing flags |
| `instagram.publish` | `InstagramAgent` six tools | Yes |

Details of each step are in [workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md).

## Provider bindings

| Role | Binding | Status |
| --- | --- | --- |
| Studio plan | `get_creative_model` | **IMPLEMENTED — NOT CONFIGURED** without `DEEPSEEK_API_KEY` |
| Scheduler plan and trend brief | `select_reasoning_provider` | **IMPLEMENTED — NOT CONFIGURED** |
| Pixels | `IMAGE_PROVIDER=openai`, edit when references exist | **IMPLEMENTED — NOT CONFIGURED** |
| Vision | DeepSeek, else structural | **IMPLEMENTED — NOT CONFIGURED** |
| Creative export | Canva | **IMPLEMENTED — NOT CONFIGURED.** Default off |

`GroundedCreativeModel` is the studio planner when the DeepSeek key is empty. It is part of the reference agent, not a temporary hack to delete.

## Tool bindings

- Context: the 22 MCP read tools, tenant from the server.
- Creative export: Canva adapter, off the MCP allowlist.
- Festival reads inside the scheduler: `FestivalMcp`, off the MCP allowlist.
- Publish: six tools inside `InstagramAgent` only.

`get_content_rules` tells the model that publishing is unavailable. That stays true after the agent is "on a platform".

## Knowledge bindings

All of these are **IMPLEMENTED** and are this agent's context, not platform globals.

| Source | Used by |
| --- | --- |
| `business_profiles` | Studio plan, approval catalog match, trend packet |
| Brand profile and guidelines | Studio plan, logo requirement gate |
| Products and offers | Studio plan, `product_reference_missing`, `offer_cannot_be_verified` |
| Festival catalog and `festival_campaigns` | Festival MCP, festival scheduler, `festival_context_invalid` |
| Account and media snapshots, Graph GET | Trend packet, intelligence routes |
| Trend observations, evidence, reports, opportunities | Trend MCP and `trend.cycle` |
| `automation_settings` | Scheduler gates and `get_content_rules` |

The festival catalog is code plus `festivals/data/lunar_dates.json`. There is no `festivals` table. Dates use `Asia/Kolkata`. A future agent does not inherit this catalog unless it binds the festival source.

Account metrics the Graph reader does not request stay gaps in the packet. `media_snapshots.shares` is a column. The reader does not currently request a shares metric. The analyst must label OBSERVED and INFERRED the way [trend architecture](INTELLIGENCE/TREND_INTELLIGENCE_ARCHITECTURE.md) describes. A recommendation is not a fact. DeepSeek web browsing is **NOT IMPLEMENTED**.

`POST /trends/opportunities/{id}/create-content` returns a prompt and `path: "/generate"`. It does not publish. Expired opportunities return 404.

## Asset bindings

**IMPLEMENTED.**

- Brand logos and other brand files on `business_assets`.
- Product images through `product_assets`.
- Generated still images on `generated_images`, stored per user.
- Prepared public file for Meta on the media route.

Production logos and product images are not uploaded yet. MCP returns `found: false`. The reference agent must keep that behavior when it is registered. Registration does not upload sample files.

## Trigger bindings

**IMPLEMENTED.**

| Trigger | Workflow |
| --- | --- |
| Signed-in `POST /generation` | `studio.generate` |
| `POST /generation/{id}/regenerate` | `studio.regenerate` |
| `POST /generation/{id}/approve` | `studio.approve` |
| `POST /generation/{id}/reject` | stops at rejected |
| `POST /instagram/publish` | `direct.publish` |
| `PUT /automation` and festival settings | writes `automation_settings` |
| `POST /automation/run-now` | one tick: daily, festival, trend |
| `scheduler_loop` when `SCHEDULER_ENABLED` | same tick for each due user |

`daily_posts_per_day` is forced to 1 when the client sends it. The reference agent does not gain a multi-post day through platform configuration.

Manual lanes never auto-publish. Automatic lanes still stop on the gates in [security](AGENT_PLATFORM_SECURITY.md).

## Approval binding

Policy id **PROPOSED:** `instagram.still_image`.

Function **IMPLEMENTED:** `decide_approval`.

Human approve on a stored image is a separate trigger. It does not skip the Instagram Agent.

## Execution binding

**IMPLEMENTED** inside `InstagramAgent` and `RecoveryPolicy`:

- Timeout default 120 seconds.
- Storage retries default 3. Verification retries default 5. Those two are class defaults. `Settings.from_env` does not read environment variables for them.
- Unknown publish certainty sets `skip_publish` and does not start a second publish.
- Success requires verification and an Instagram media id.
- Reels, stories, and carousels are not published. Caption on the upload route is at most 2200 characters.

Credentials: connected account for that user, token decrypted for the request. `META_ACCESS_TOKEN` is not this path. The scheduler fails with `INSTAGRAM_NOT_CONNECTED` when the business has no connected account.

## Tasks and UI

Publish progress uses `agent_tasks`, `agent_events`, and the SSE route. The React pages (dashboard, generate, images, posts, automation, festivals, business, brand, products, assets, Instagram, trends) are the reference agent's UI. **IMPLEMENTED.**

Error JSON includes `"platform": "instagram"`. That field names this product's HTTP envelope. It is not a platform selector.

## What registration changes

**PLANNED** when implementation starts, and not before:

- A catalog entry points `instagram_marketing` at the handlers above.
- Routes keep their paths, status codes, and auth.
- No new Meta client.
- No change to allowlists.
- Tests listed in the migration plan stay green without edits that weaken assertions.

Until that entry exists, the reference agent is the code itself. Operators run the same process they run today.

## How another agent may use this one

**PROPOSED.** A future agent that needs an Instagram still image published ends its workflow with `enqueue_instagram_publication` after the same approval gates, or it does not publish. It does not import `InstagramPublisher`. It does not add tools to `ALLOWED_TOOL_SET`. It does not set `AgentState.platform` to another literal. That field stays `instagram` because the publish runtime is Instagram.

A future agent that does not publish does not depend on `InstagramAgent` at all. It binds its own workflows to MCP reads, providers, and local writes.

## Partial pieces that registration must not "finish" by invention

| Piece | Status | Platform stance |
| --- | --- | --- |
| `LLMPlanner` | **PARTIAL** stub | Leave disabled |
| `SemanticSimilarityBackend` | **NOT IMPLEMENTED** | Leave disabled |
| `SessionContextStore` festival context and task save | **PARTIAL** | Leave as no-ops until a real store is designed |
| `api/instagram_account_routes.py` | Not mounted | Do not mount a second connect API |
| Duplicate `GET /admin/users` | **PARTIAL** | Leave the auth handler as the live one |
| OpenAI vision, DeepSeek images, DeepSeek browsing | Not product features | Do not add them to the reference bindings |
| Global `META_ACCESS_TOKEN` | Development fallback only | Do not promote it to the reference credential |
