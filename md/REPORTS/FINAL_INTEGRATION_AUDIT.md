# Final integration audit

Date: 2026-09-24. Git HEAD `8ad7dc4`. Working tree was clean before this report. No application code was changed. No API keys or logos were inserted.

Ratings:

| Rating | Meaning |
| --- | --- |
| PASS | The required path is implemented |
| PARTIAL | The path exists but a required step is incomplete in code |
| FAIL | The required path is missing or a forbidden path can publish |
| NOT CONFIGURED | The code is in place; this environment has no key, connection, or uploaded file |

A missing `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, logo, or product image is **IMPLEMENTED — NOT CONFIGURED**. It is not a code failure.

## Summary

| Area | Implementation | Runtime |
| --- | --- | --- |
| DeepSeek routing | PASS | **IMPLEMENTED — NOT CONFIGURED** |
| OpenAI image routing | PASS | **IMPLEMENTED — NOT CONFIGURED** |
| Asset grounding | PASS | **IMPLEMENTED — NOT CONFIGURED** |
| Instagram intelligence | PASS | Meta account not required for startup |
| MCP | PASS | Runs in-process |
| Canva | PASS | **IMPLEMENTED — NOT CONFIGURED** |
| Meta publishing boundary | PASS | Publishing needs a connected account |
| Tenant isolation | PASS | — |
| Missing credentials | PASS | **IMPLEMENTED — NOT CONFIGURED** |
| Missing assets | PASS | **IMPLEMENTED — NOT CONFIGURED** |
| Tests | PASS | Default suites; live provider tests stay optional |

No area is FAIL or PARTIAL.

## DeepSeek routing

**PASS.** Runtime keys: **NOT CONFIGURED.**

`LLM_PROVIDER` and `VISION_PROVIDER` default to `deepseek` (`config.py`, `.env.example`). An OpenAI key does not move reasoning onto OpenAI.

| Step | When `DEEPSEEK_API_KEY` is set | When the key is absent |
| --- | --- | --- |
| Content Agent / studio creative plan | `DeepSeekCreativeClient` via `get_creative_model` | `GroundedCreativeModel` (local, no network) |
| Scheduler content plans | `DeepSeekLLMProvider` via `select_reasoning_provider` | `MockLLMProvider` |
| Trend Analyst | `DeepSeekTrendAnalyst` in `backend/trends/analyst.py` | `get_llm_provider(...).generate_content_plan` raises `DEEPSEEK_CONFIGURATION_ERROR` (503) |
| Instagram intelligence analysis | MCP `trend_context` is the only model input; `TrendIntelligence.analyze` calls the analyst | Same configuration error on a live call |
| Festival content reasoning | Festival context is passed into the DeepSeek plan | Local planner; dates still come from the catalog |

Festival **dates** stay in the stored India catalog (`festivals/intelligence.py`, `festivals/mcp.py`). DeepSeek is not asked to calculate them. Festival **copy and creative direction** go through the DeepSeek plan.

`LLM_PROVIDER=openai` can still construct `OpenAILLMProvider`. That switch is off by default, and a present `OPENAI_API_KEY` does not select it. This audit left that override in place.

Startup on this machine logged `LLM provider: deepseek` and bound `MockLLMProvider`. A direct DeepSeek plan call returned `DEEPSEEK_CONFIGURATION_ERROR` / 503. Vision factory returns `None` without a key, so image QA stays structural until the key exists.

## OpenAI routing

**PASS.** Runtime key: **NOT CONFIGURED.**

`IMAGE_PROVIDER` defaults to `openai`. DeepSeek is not an image provider: `get_image_generation_provider` rejects any name other than `openai` or `mock`.

Creative plans are drawn by the OpenAI image provider:

- Text-only creatives use `OpenAIImageGenerationProvider`.
- Creatives that carry logo or product bytes use `backend.ai.image.openai.OpenAIImageProvider` through `submit_creative_image`.

Neither class imports the Graph client or a publish tool.

Startup bound `MockImageGenerationProvider` because `OPENAI_API_KEY` is empty. Direct text and reference calls returned `OPENAI_CONFIGURATION_ERROR` / 503. `select_image_provider` does not call the network when the key or image model is missing.

## Asset grounding

**PASS.** Runtime files: **NOT CONFIGURED.**

Product image and company logo are loaded only after MCP says this tenant owns them:

1. `get_product_image` / `get_company_logo` (`backend/mcp/servers/product.py`, `backend/mcp/servers/asset.py`).
2. `services/asset_resolution.py` classifies the file.
3. `services/image_reference.py` attaches bytes only for `AVAILABLE` rasters.
4. `submit_creative_image` sends those bytes to the OpenAI edit path.

Missing catalog rows return `{"found": false}` and `asset: null`. The resolver returns `AssetState.MISSING` and `image is None`. Generation continues from product metadata. The prompt is not rewritten into a stand-in such as "Use the company logo", and the missing file is not replaced with a generated mark stored as the company logo.

`create_app()` started with no uploaded brand files.

## Instagram intelligence

**PASS.**

```text
Meta Graph (GET only)
  → GraphInstagramReader / AccountIntelligenceService
  → MCP account tools (get_account_summary, get_recent_media, get_account_insights, get_top_content, get_content_performance)
  → trend packet
  → DeepSeekTrendAnalyst
```

`services/instagram_reader.py` issues GET requests only. MCP tools return `trend_context`. They do not return access tokens or raw Graph payloads, and they do not publish. Trend research (`backend/trends/research.py`) blocks `instagram.com`, `facebook.com`, and related hosts. It does not call Graph.

## MCP

**PASS.**

The allowlist in `backend/mcp/permissions.py` is read-only business, brand, product, festival, account, and trend tools. `publish_instagram_media`, `media_publish`, and the other Instagram Agent tools raise `ToolNotAllowed`. Tenant ids inside model arguments are stripped (`backend/mcp/tenant_isolation.py`). The bound tenant comes from authentication or the scheduler.

## Canva

**PASS.** Runtime: **NOT CONFIGURED.**

`CANVA_ENABLED` defaults to false. `get_canva_client` returns `None`, and the studio client is not attached. Image generation does not wait on Canva.

When Canva is enabled and the user is connected, the chain is:

```text
Content Agent
  → CanvaAdapter.query / produce (Canva MCP)
  → image bytes shaped by image_from_canva
  → QA (DeepSeek vision when configured, structural review otherwise)
  → approval
  → Instagram Agent only if approval publishes
```

Studio generation stores `published: false` until `POST /generation/{image_id}/approve`. That approve handler calls `PublicationService`, which builds an `InstagramAgent`. Canva export drops tokens and URLs. The adapter does not call Graph. A disabled or disconnected account returns `CANVA_DISABLED` or `CANVA_NOT_CONNECTED` instead of a design.

## Meta publishing boundary

**PASS.**

Graph `media_publish` lives in `tools/instagram_media.py` and is registered only on `InstagramAgent`. HTTP `POST /api/v1/instagram/publish` and approved generation both enqueue `PublicationGateway` / `PublicationService`, which construct that agent.

| Caller | Can call Meta publish |
| --- | --- |
| Instagram Agent | Yes |
| DeepSeek | No. No Graph import under `ai/` or `backend/ai/` |
| OpenAI | No. Image providers have no publish method |
| Canva | No. No Graph import under `backend/integrations/canva/` |
| MCP | No. Publish names are rejected. Account tools are GET-only |
| Trend Agent | No. Research blocks Meta hosts. Scheduler has no `publish_instagram_media` |
| React | No. The browser calls `/api/v1/instagram/publish`. It does not call `graph.facebook.com` or `graph.instagram.com` |

## Tenant isolation

**PASS.**

- MCP requires `TenantContext` from `authenticated`, `scheduler`, or `system`. Model-supplied `user_id`, `tenant_id`, and `account_id` are discarded.
- Logo and product tools deny another tenant's asset id (`AssetAccessDenied`).
- Approve-and-post loads the image with `get_owned` and the account with `get_primary` for that user. A mismatched account owner raises `PERMISSION_ERROR`.
- `META_ACCESS_TOKEN` is not used for publish unless `INSTAGRAM_LEGACY_ENV_FALLBACK` is set and `APP_ENV` is `development`, `dev`, or `local`. Unset `APP_ENV` is treated as production, so the fallback stays off.

## Missing credentials

**PASS.** Runtime: **NOT CONFIGURED.**

Checked without printing secrets: `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are empty. There is no `.env` file.

| Check | Result |
| --- | --- |
| `create_app()` | Succeeded |
| Bound providers | `MockLLMProvider`, `MockImageGenerationProvider`, vision `None` |
| Live DeepSeek plan | `DEEPSEEK_CONFIGURATION_ERROR`, HTTP 503 |
| Live OpenAI text image | `OPENAI_CONFIGURATION_ERROR`, HTTP 503 |
| Live OpenAI reference edit | `OPENAI_CONFIGURATION_ERROR`, HTTP 503 |

No fake credentials were added. Live provider tests are not part of default pytest (`-m "not real_openai and not real_deepseek"`).

## Missing assets

**PASS.** Runtime: **NOT CONFIGURED.**

Production logos and product images are not uploaded. The process still starts. Product generation with no product file continues from catalog metadata. Brand generation with no logo continues without attaching a logo and without storing a generated image as the company logo. MCP `get_company_logo` and `get_product_image` return `found: false` when nothing is on file.

## Tests

**PASS.**

| Command | Result |
| --- | --- |
| `pytest` | 421 passed, 4 skipped, 2 deselected, 120.85s |
| `cd frontend && npm test` | 13 files, 39 tests passed |
| `cd frontend && npm run build` | `tsc` and Vite build succeeded |

Pytest configuration was not changed. The 4 skips are Meta credential gates in `tests/test_instagram_integration.py` and `tests/test_real_instagram.py`. The 2 deselected tests are `tests/test_real_openai.py` (`real_openai`). The `real_deepseek` marker exists and has no test module; default pytest does not require a live DeepSeek call.

Default pytest blocks `DeepSeekTransport.chat`, so the green run did not contact DeepSeek or OpenAI.
