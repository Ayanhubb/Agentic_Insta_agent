# AI + MCP Integration Plan

**Role:** Agent 1 — Principal Architect / Integration Coordinator  
**Date:** 2026-09-24  
**Status:** Binding contract for sequenced implementation. Do not rewrite the application.  
**Repo:** Instagram Agentic AI (`agentic`). FastAPI lives at the repository root. There is no `backend/` package.

This document is the contract for every subsequent agent. Existing V1/V2 behavior stays unless a section explicitly changes it.

Related: [ARCHITECTURE.md](../ARCHITECTURE.md), [AI_ARCHITECTURE.md](../AI_ARCHITECTURE.md), [CONTENT_AGENT.md](../CONTENT_AGENT.md), [INSTAGRAM_AGENT.md](../INSTAGRAM_AGENT.md), [brain.md](../brain.md).

---

## 0. Inspection summary (as-found, 2026-09-24)

Read: architecture docs, `.env.example`, `requirements.txt`, `docker-compose.yml`, `config.py`, `api/app.py`, `ai/*`, `agent/content_agent.py`, `agent/agent.py`, `db/models.py`, `db/repositories.py`, `db/uow.py`, `scheduler/*`, `festivals/*`, `services/publication.py`, `services/media_paths.py`, `models/errors.py`, `tests/conftest.py`, `tests/test_ai_providers.py`, frontend generation/business APIs.

### 0.1 Already implemented — preserve

| Capability | Location | Rule |
| --- | --- | --- |
| React → FastAPI only | `frontend/`, `api/` | React has no provider keys. Only `VITE_API_BASE` (optional URL, not a secret). |
| Auth + `user_id` isolation | `auth/`, `get_owned` | Tenant from JWT/session, never from the model. |
| Provider ABCs + factories | `ai/llm_client.py`, `ai/image_generator.py` | Extend. Do not delete names. |
| OpenAI LLM + images + mocks | `ai/openai_llm.py`, `ai/openai_image_generator.py`, `ai/mocks.py` | Keep OpenAI **image** path. Keep OpenAI **LLM** as fallback. |
| Content Agent (never publishes) | `agent/content_agent.py` | Dual path: legacy `_plan` stays; new pipeline is additive. |
| Instagram Agent (only Meta publisher) | `agent/agent.py` + six tools | **Frozen.** |
| Publication gateway | `services/publication.py` | Sole enqueue. AMBIGUOUS never republishes. |
| Owner-scoped media | `services/media_storage.py`, `media_paths.py` | Extend stages; do not bypass path safety. |
| India festival catalog + lunar table | `festivals/india_festivals.py`, `festivals/data/lunar_dates.json` | **Date source of truth.** |
| Daily + festival scheduler | `scheduler/` | Still Content Agent → `publish_generated_image` only. |
| Approval / auto-approve | Content Agent + `automation_settings` | User-prompt never auto-publishes. |
| `POST /api/v1/instagram/publish` | `api/routes.py` | Path + JSON shape unchanged. |
| Manual Generate → Approve & Post | `POST /generation` then `/approve` | `{ "prompt" }` remains sufficient. |
| Default pytest mocks | `tests/conftest.py` blocks `AsyncOpenAI` | Extend to DeepSeek HTTP. |

### 0.2 Gaps this upgrade fills

1. `get_llm_provider()` only accepts `openai` (or empty). Unknown provider → `OPENAI_CONFIGURATION_ERROR`.
2. `create_app()` treats **`OPENAI_API_KEY` + model name** as the sole “AI is live” signal (`settings.openai_configured`). DeepSeek cannot go live until this split exists.
3. `Settings.llm_provider` / `.env.example` default to `openai`. Target default becomes `deepseek` in `.env.example` only; factories still accept `openai`.
4. `business_profiles.products` is JSON labels, not a catalog with photos/offers. Business UI is a textarea.
5. No logos, brand guidelines table, vision QA, MCP, or Canva.
6. `ErrorCode` has `OPENAI_*` and `CONTENT_*` only. No DeepSeek / vision / MCP codes.
7. Content Agent `_call_llm` tries `generate_structured` first, then `generate_content_plan`. Failures are mapped to `OPENAI_API_ERROR` / `OPENAI_CONFIGURATION_ERROR`.
8. `media_paths.STORAGE_STAGES` is `{generated, prepared, published}` and filenames are `img_<32 hex>.(png|jpg|jpeg)` only.
9. Alembic `001_initial_schema` is `Base.metadata.create_all`. Additive **columns** on existing tables need explicit `002` ALTER; `create_all` will not add them.
10. Dockerfile copies `ai/` but not `mcp/`.
11. `conftest.py` monkeypatches only `ai.openai_llm.AsyncOpenAI` and `ai.openai_image_generator.AsyncOpenAI`.
12. Frontend `generationApi.create` sends only `{ prompt }`. That must keep working.

### 0.3 Frozen (do not redesign)

Instagram publishing sequence and endpoint:

```text
POST /api/v1/instagram/publish
  validate_image → prepare_image → upload_image
    → create_instagram_media → publish_instagram_media → verify_publication
```

Files in §4.2 must not change except a proven publish regression.

---

## 1. Architecture decision record

### ADR-001 — Package layout stays at repo root (no `backend/` tree)

**Decision:** Map the requested `backend/ai/` and `backend/mcp/` trees onto **existing top-level packages**:

```text
ai/llm/       ← requested backend/ai/llm/
ai/vision/    ← requested backend/ai/vision/
ai/image/     ← requested backend/ai/image/
mcp/          ← requested backend/mcp/
```

**Why:** `Dockerfile` `COPY ai ./ai`, `pytest.ini` `pythonpath = .`, `from ai import get_llm_provider`, and every test import assume this tree. A `backend/` package would force a rewrite. That violates “do not rewrite.”

Compatibility shims remain at `ai/llm_client.py` and `ai/image_generator.py`.

### ADR-002 — DeepSeek plans; OpenAI pixels; DeepSeek sees; Instagram Agent publishes

```text
React
  → FastAPI
    → Authentication          user_id / business_id from JWT+session
      → Content Agent
        → MCP Client          tenant-scoped context; optional Canva compose
          → DeepSeek LLM      structured CreativeBrief
            → OpenAI Image    generate/edit with real logo + product bytes
              → DeepSeek Vision QA
                → store asset (MediaStorageService)
                  → human approval OR automation auto-approve
                    → PublicationGateway
                      → Instagram Agent   (six tools, VERIFY_FIRST)
                        → Database / Dashboard
```

Forbidden:

- DeepSeek, OpenAI, Canva, and MCP **must not** import Graph clients, `tools.instagram_media`, or call `media_publish`.
- MCP is not the database, not the image generator, and not the publisher.
- The LLM must not invent festival dates, prices, offers, legal names, or logos when real records exist.
- `LLMPlanner` must never be pointed at DeepSeek (or OpenAI) with Instagram tools in scope.

### ADR-003 — Tenant identity is application context

- `user_id` = authenticated user from `require_password_ok` / `get_current_user`.
- `business_id` = `business_profiles.id` for that user (1:1 today).
- MCP tools receive `McpContext(user_id=..., business_id=...)` injected by FastAPI/Content Agent.
- If a model or tool argument includes `user_id` or `business_id`, **ignore it**.

### ADR-004 — Dual-path Content Agent; freeze Instagram Agent

Content Agent constructor stays backward compatible:

```python
ContentAgent(llm, image_generator, context_store=None, *, session=None, mcp=None, vision=None, ...)
```

- If `mcp` **and** `vision` are injected → `CreativePipeline` (new).
- If either is missing → existing `_plan()` / `_call_llm` / `_generate_image` (**required** so `tests/test_content_agent.py` stays green).

`ContentStrategyResult.published` remains always `false`.  
Instagram Agent files in §4.2 are out of scope.

### ADR-005 — Real assets over hallucinated brand

When a logo or product photo exists, the image request **must attach those bytes**. Prompts must say: place the provided logo/product; do not redraw a substitute wordmark.

If no logo exists, generate without inventing a fake mark. Vision QA **fails** images that introduce a made-up logo when a real one was supplied.

### ADR-006 — Festival dates stay in `festivals/`

MCP `festival` tools wrap `FestivalService.catalog` / `festivals_for_year()` / owned campaigns. DeepSeek may interpret *how* to merchandize a festival. It may not decide *when* it occurs. Pipeline overwrites any model-supplied date with the catalog date.

### ADR-007 — Additive APIs and schema

Existing JSON keys stay. New fields are optional. `POST /generation` with only `{ "prompt" }` remains valid. Default pytest must pass with mocks when catalog rows are absent (legacy path).

### ADR-008 — Typed provider errors; do not overload `OPENAI_*`

Do not reuse `OPENAI_CONFIGURATION_ERROR` for a missing DeepSeek key. Add codes in §10.4. OpenAI implementations keep emitting `OPENAI_*`. Content Agent pipeline maps DeepSeek/vision/MCP codes through; **legacy** `_call_llm` may keep mapping to `OPENAI_*` so existing tests do not churn.

### ADR-009 — Canva is optional composition, not a publisher

`MCP_CANVA_ENABLED=false` by default. When enabled, Canva may return a composed PNG into the same media lifecycle. That PNG still goes through Vision QA, approval, and the Instagram Agent. Timeout → skip Canva, fall back to OpenAI compose (do not fail the job).

### ADR-010 — Duplicate generation ≠ duplicate publication

Publication duplicate rules stay in `PublicationGateway` + SQL unique indexes.  
Generation adds `creative_jobs` fingerprint so daily/festival ticks do not spawn two OpenAI jobs for the same `(user, account, local_date, campaign_slot, product_id)`.

Ambiguous Instagram publication: **no automatic `media_publish` retry**. Unchanged.

### ADR-011 — Backend chooses the product, not the model

If the catalog has active products, FastAPI/scheduler/MCP context_builder selects the product (least-recently-featured among active, else first active). DeepSeek receives that product’s name, description, and “use the provided product photo.” It cannot pick another tenant’s product or invent a SKU.

### ADR-012 — DeepSeek over HTTP; OpenAI SDK stays for images

DeepSeek chat + vision: OpenAI-compatible HTTP via existing `httpx` (host allowlist `api.deepseek.com` + localhost). Do not add a new SDK unless a later agent proves it is required. OpenAI **images** keep `openai>=1.40.0`. `requirements.txt` stays as-is unless httpx is insufficient.

---

## 2. Target runtime architecture

```text
                    ┌─────────────────────────────────────────┐
                    │  React  (no DeepSeek/OpenAI/Canva/MCP    │
                    │  keys; no VITE secrets)                  │
                    └──────────────────┬──────────────────────┘
                                       │ /api/v1  cookie+Bearer
                    ┌──────────────────▼──────────────────────┐
                    │  FastAPI  create_app()                  │
                    │  app.state: llm, vision, image, mcp     │
                    └──────────────────┬──────────────────────┘
           ┌───────────────┬───────────┼────────────┬─────────┐
           ▼               ▼           ▼            ▼         ▼
        auth/           mcp/        ai/llm       ai/image   db/
     user_id           │            DeepSeek     OpenAI     repos
     business_id       │            (+ OpenAI    gpt-image  only
                       │             LLM fallback)
                       ├─ festival ──► festivals/ (dates SoT)
                       ├─ business ──► business_profiles
                       ├─ product  ──► products / offers
                       ├─ brand    ──► brand_guidelines
                       ├─ asset    ──► brand_assets + media_paths
                       ├─ content  ──► recent posts/images
                       └─ canva    ──► external MCP (optional)
                                       │
                                Content Agent
                         mcp+vision? → CreativePipeline
                         else        → legacy _plan()
                                       │
                         CreativeBrief (Pydantic)
                                       │
                         OpenAI Image generate/edit
                                       │
                         DeepSeek Vision QA
                           pass → store GENERATED
                           fail → regenerate once
                           fail2 → QA_FAILED, no auto-publish
                                       │
                         existing approval policy
                                       │
                         PublicationGateway → Instagram Agent
                                       │
                         verified PUBLISHED only counts
```

Process rules (unchanged):

- Single uvicorn worker if `SCHEDULER_ENABLED=true`.
- Scheduler never constructs `InstagramGraphClient`.
- SQLite: scheduler commits before the Agent opens another connection.

---

## 3. Exact files to create

### 3.1 Providers (Agent B — AI)

| Path | Purpose |
| --- | --- |
| `ai/llm/__init__.py` | export `LLMProvider`, `get_llm_provider` |
| `ai/llm/base.py` | `LLMProvider` ABC (moved from `ai/llm_client.py`) |
| `ai/llm/deepseek.py` | DeepSeek chat → schema-validated brief |
| `ai/llm/openai.py` | current `OpenAILLMProvider` body |
| `ai/llm/factory.py` | `LLM_PROVIDER=deepseek\|openai` |
| `ai/vision/__init__.py` | |
| `ai/vision/base.py` | `VisionProvider` ABC |
| `ai/vision/deepseek.py` | image QA |
| `ai/vision/factory.py` | `VISION_PROVIDER=deepseek` |
| `ai/image/__init__.py` | |
| `ai/image/base.py` | `ImageGenerationProvider` + optional `edit` |
| `ai/image/openai.py` | current OpenAI image provider body |
| `ai/image/factory.py` | `IMAGE_PROVIDER=openai` |

Keep `ai/openai_llm.py` and `ai/openai_image_generator.py` as **thin re-exports** for one release so `conftest.py` and `tests/test_openai_*.py` keep importing the old paths.

Extend existing `ai/mocks.py` (do not replace): `MockVisionProvider`, `generate_creative_brief` on `MockLLMProvider`, scripted QA fail.

### 3.2 MCP (Agent C)

| Path | Purpose |
| --- | --- |
| `mcp/__init__.py` | |
| `mcp/client.py` | `McpClient.call` + timeout |
| `mcp/registry.py` | allowlisted tool name → handler |
| `mcp/context_builder.py` | `CreativeContext` for Content Agent |
| `mcp/permissions.py` | capability ACL |
| `mcp/tenant_isolation.py` | bind context; strip model tenant ids; `get_owned` |
| `mcp/errors.py` | `MCP_TIMEOUT`, `MCP_UNAVAILABLE`, `MCP_FORBIDDEN` |
| `mcp/tools/festival.py` | catalog + campaign remaining; no date invention |
| `mcp/tools/business.py` | profile + vertical playbook |
| `mcp/tools/product.py` | products, images, offers |
| `mcp/tools/brand.py` | guidelines |
| `mcp/tools/asset.py` | logos / brand files metadata |
| `mcp/tools/content.py` | recent themes |
| `mcp/tools/canva.py` | external Canva adapter |

### 3.3 Pipeline + domain (Agent D)

| Path | Purpose |
| --- | --- |
| `agent/creative_pipeline.py` | MCP → brief → image → QA → persist |
| `models/creative.py` | `CreativeBrief`, `VisionQaResult`, `AssetRef`, `CreativeContext` |

### 3.4 Persistence (Agent A — DB)

| Path | Purpose |
| --- | --- |
| `db/migrations/versions/002_brand_catalog_and_creative.py` | explicit `create_table` + `add_column` |

Models/repos stay in existing `db/models.py`, `db/repositories.py`, `db/schemas.py`, `db/enums.py`, `db/uow.py`.

### 3.5 API (Agent E)

| Path | Purpose |
| --- | --- |
| `api/catalog_routes.py` | brand, products, offers; mount in `api/app.py` under `/api/v1` |

### 3.6 Tests

| Path | Purpose |
| --- | --- |
| `tests/test_deepseek_llm.py` | JSON, retries, missing key, timeout |
| `tests/test_deepseek_vision.py` | pass / fail / invalid QA |
| `tests/test_mcp_isolation.py` | user A cannot read user B via MCP |
| `tests/test_mcp_festival_source_of_truth.py` | dates from catalog, not model |
| `tests/test_creative_pipeline.py` | logo attach, QA regenerate, auto-approve blocked on QA fail |
| `tests/test_catalog_api.py` | CRUD + isolation + path traversal |
| `tests/test_canva_mcp.py` | timeout, disabled flag, no publish |

### 3.7 Docs

| Path | Purpose |
| --- | --- |
| `docs/AI_MCP_INTEGRATION_PLAN.md` | this file (Agent 1) |
| `AI_ARCHITECTURE.md` | Agent B/D update **after** implementation, not during this ADR |

---

## 4. Exact files to modify

Modify only what integration requires. No drive-by refactors.

### 4.1 In scope

| File | Change | Must not break |
| --- | --- | --- |
| `ai/__init__.py` | export `get_vision_provider`; keep old names | `from ai import get_llm_provider` |
| `ai/llm_client.py` | re-export from `ai.llm` | Content Agent duck-typing |
| `ai/image_generator.py` | re-export from `ai.image` | image tests |
| `ai/openai_llm.py` | re-export `OpenAILLMProvider` | `conftest` + `test_openai_llm.py` |
| `ai/openai_image_generator.py` | re-export | `conftest` + `test_openai_image.py` |
| `ai/mocks.py` | `MockVisionProvider`; `generate_creative_brief` | default pytest |
| `ai/schemas.py` | additive brief/QA/edit schemas | existing `ContentPlan` tests |
| `config.py` | new env; `deepseek_configured`; `llm_live` / `image_live` / `vision_live`; `public_ai_status()` never keys | `test_ai_providers.py` (`openai_configured` key **stays**) |
| `.env.example` | DeepSeek + vision + image model + MCP/Canva flags | no real secrets |
| `api/app.py` | inject vision + mcp; split live-provider selection from `openai_configured` | `create_app(..., llm_provider=)` |
| `api/platform_routes.py` | optional `product_id` / `offer_id` / `use_canva` on generate (`extra` ignore); pass mcp/vision into Content Agent | `POST /generation` `{prompt}` |
| `agent/content_agent.py` | optional `mcp`, `vision`; `run()` dispatches to pipeline or `_plan` | `tests/test_content_agent.py` |
| `models/content.py` | optional `product_id`, `offer_id` on request/result | `extra=ignore` already |
| `models/errors.py` | new ErrorCode values; **do not rename existing** | |
| `services/logging.py` | redact `DEEPSEEK_API_KEY`, `CANVA_MCP_TOKEN` | redaction tests |
| `services/media_paths.py` | stage `brand`; logo filename rules (see §8.5) | generated-image path tests |
| `db/models.py` | new tables + nullable columns on `generated_images` | existing tables remain |
| `db/enums.py` | vertical, asset, offer, QA, `ImageSource.CANVA` | |
| `db/repositories.py` | new repos + `get_owned` | |
| `db/schemas.py` | DTOs; never `storage_path` to React | |
| `db/uow.py` | `db.products`, `db.brand_assets`, … | |
| `db/__init__.py` | export new types | |
| `scheduler/daily_scheduler.py` | select product_id via catalog policy; still `publish_generated_image` | double-tick → one post |
| `scheduler/festival_scheduler.py` | same | remaining_posts math |
| `tests/conftest.py` | block DeepSeek HTTP **and** new OpenAI import paths | `real_openai` / add `real_deepseek` |
| `pytest.ini` | `-m "not real_openai and not real_deepseek"` | |
| `Dockerfile` | `COPY mcp ./mcp` | image build |
| `frontend/src/services/api/endpoints.ts` | additive catalog paths | existing paths |
| `frontend/src/types/api.ts` | additive optional fields | |
| `frontend` Business + new catalog UI | logos, products, offers | generate `{prompt}` + Approve & Post |

### 4.2 Do not modify (Instagram Agent freeze)

- `agent/agent.py`, `agent/planner.py`, `agent/registry.py`, `agent/executor.py`, `agent/recovery.py`, `agent/state_machine.py`
- `tools/instagram_media.py`, `tools/instagram_verifier.py`, `tools/image_validator.py`, `tools/image_preparer.py`, `tools/image_storage.py` (caption WIP already in flight may finish **without** changing the allowlist)
- `services/publication.py` duplicate / AMBIGUOUS / VERIFY_FIRST rules
- `auth/passwords.py`, `auth/tokens.py` — only consume `user.id`
- `festivals/india_festivals.py` and `festivals/data/lunar_dates.json` except adding a year row when a new year is published

---

## 5. Environment variables

Backend only. Never `VITE_*` / `NEXT_PUBLIC_*`. Never stored in DB. Never returned by `/settings` or `/auth/me`.

```dotenv
# --- LLM (primary: DeepSeek) ---
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TIMEOUT_SECONDS=30
LLM_MAX_ATTEMPTS=3
LLM_TEMPERATURE=0.4

# Optional LLM fallback
# LLM_PROVIDER=openai
OPENAI_API_KEY=
LLM_MODEL=

# --- Vision ---
VISION_PROVIDER=deepseek
DEEPSEEK_VISION_MODEL=deepseek-flash
VISION_MAX_ATTEMPTS=2
VISION_TIMEOUT_SECONDS=30

# --- Image generation / edit ---
IMAGE_PROVIDER=openai
OPENAI_IMAGE_MODEL=gpt-image-2.5-sunburst
IMAGE_MODEL=gpt-image-2.5-sunburst
IMAGE_MAX_ATTEMPTS=3
IMAGE_SIZE=1024x1024
OPENAI_RETRY_DELAY_SECONDS=0.4

# --- MCP ---
MCP_ENABLED=true
MCP_TIMEOUT_SECONDS=10
MCP_CANVA_ENABLED=false
CANVA_MCP_URL=
CANVA_MCP_TOKEN=
CANVA_TIMEOUT_SECONDS=20
```

Existing vars unchanged: `META_*`, `DATABASE_URL`, `JWT_*`, `TOKEN_ENCRYPTION_KEY`, `SCHEDULER_*`, `DEFAULT_TIMEZONE`, `IMAGE_PUBLIC_BASE_URL`, `CORS_ORIGINS`, …

### 5.1 Settings fields

| Attribute | Default | Notes |
| --- | --- | --- |
| `llm_provider` | code default may stay `openai` for `test_settings`; `.env.example` = `deepseek` | factory accepts both |
| `deepseek_api_key` | `""` | |
| `deepseek_model` | `""` | required for live DeepSeek |
| `deepseek_base_url` | `https://api.deepseek.com` | host allowlist |
| `vision_provider` | `deepseek` | |
| `deepseek_vision_model` | fallback `deepseek_model` | |
| `openai_image_model` | `""` | if set, fills `image_model` |
| `mcp_enabled` | `true` | false → do not construct live MCP; Content Agent still works legacy |
| `mcp_timeout_seconds` | `10` | |
| `mcp_canva_enabled` | `false` | |
| `canva_mcp_url` / `canva_mcp_token` | empty | token redacted |

Keep `openai_configured` as `bool(openai_api_key)` so `test_ai_providers.py` continues to assert that key. Add:

```text
deepseek_configured = bool(deepseek_api_key.strip())
llm_live =
    (llm_provider in {"deepseek"} and deepseek_api_key and deepseek_model)
    or (llm_provider in {"", "openai"} and openai_api_key and llm_model)
image_live = image_provider in {"", "openai"} and openai_api_key and (image_model or openai_image_model)
vision_live = vision_provider in {"", "deepseek"} and deepseek_api_key and (deepseek_vision_model or deepseek_model)
```

### 5.2 `create_app()` selection (normative)

```text
if llm_provider kwarg injected: use it
elif llm_live: factory(settings)
else: MockLLMProvider()

if image_provider kwarg injected: use it
elif image_live: get_image_generation_provider(settings, storage=media_service)
else: MockImageGenerationProvider(media_service)

if vision_provider kwarg injected: use it
elif vision_live: get_vision_provider(settings)
else: MockVisionProvider()

if mcp_client kwarg injected: use it
elif mcp_enabled: McpClient(...)
else: None

app.state.llm_provider / image_provider / vision_provider / mcp_client
```

Startup still must not call DeepSeek or OpenAI. Missing keys only fail when generation is requested.

`public_ai_status()` adds `vision_provider`, `deepseek_configured` (bool), `mcp_enabled`, `canva_enabled`. Never keys, never `canva_mcp_token`, never base URLs with credentials.

Logging: add `DEEPSEEK_API_KEY`, `deepseek_api_key`, `canva_mcp_token`, `CANVA_MCP_TOKEN` to `_SECRET_KEYS` and a `DEEPSEEK_API_KEY=` regex (same pattern as `OPENAI_API_KEY`).

---

## 6. Provider interfaces

Dependency-injected. Route handlers must not import DeepSeek or OpenAI SDK types.

### 6.1 LLM — `ai/llm/base.py`

```python
class LLMProvider(ABC):
    provider_id: str  # "deepseek" | "openai" | "mock"

    async def generate_content_plan(self, request: ContentPlanRequest) -> ContentPlan:
        """Existing contract. Required. Content Agent legacy path and tests."""

    async def generate_creative_brief(self, request: CreativeBriefRequest) -> CreativeBrief:
        """Preferred pipeline path. Default: wrap generate_content_plan + empty composition."""
```

`MockLLMProvider` and `OpenAILLMProvider` must keep `generate_content_plan`. DeepSeek implements both.

Factory:

```text
deepseek → DeepSeekLLMProvider
openai / "" → OpenAILLMProvider
else → LLM_PROVIDER_UNAVAILABLE 503
```

DeepSeek HTTP:

- `POST {DEEPSEEK_BASE_URL}/chat/completions` (OpenAI-compatible).
- `response_format: json_object` when supported; else parse fenced JSON (reuse `extract_json_object` from current OpenAI LLM).
- Timeout `DEEPSEEK_TIMEOUT_SECONDS`.
- Auth / missing key / missing model → `DEEPSEEK_CONFIGURATION_ERROR` 503.
- Transport after `LLM_MAX_ATTEMPTS` → `DEEPSEEK_API_ERROR` 502 / 504.
- Bad JSON / schema → `DEEPSEEK_INVALID_RESPONSE` 502, retryable within budget.
- Host allowlist: `api.deepseek.com`, `127.0.0.1`, `localhost`.
- No tool_calls. If the response includes tool_calls → invalid response (same as OpenAI LLM).

### 6.2 CreativeBrief (superset of ContentPlan)

All current `ContentPlan` required fields stay. Additive:

```json
{
  "content_type": "PRODUCT",
  "theme": "diwali_kundan_window",
  "image_prompt": "... composition using PROVIDED logo and product photo ...",
  "business_context": "...",
  "reason": "festival_campaign",
  "caption_hint": "optional",
  "featured_product_or_service": "optional",
  "must_use_asset_ids": ["asset_..."],
  "must_not_invent": ["logo", "price", "offer", "company_name", "festival_date"],
  "offer_text": null,
  "festival_date": null,
  "composition": {
    "logo_placement": "bottom_right",
    "product_treatment": "hero_unchanged",
    "background": "generated",
    "canva_template_hint": null
  }
}
```

`extra=ignore`. Forbidden keys unchanged (`command`, `token`, `tools`, Instagram tool names, `eval`, `exec`).

Integrity validator (pipeline, not the model):

- If MCP supplied `offer.body` / `offer.price_text`, brief `offer_text` must equal it or be null. Difference → `CONTENT_POLICY_VIOLATION`.
- If MCP supplied festival date, brief `festival_date` is overwritten from MCP (log if the model differed; do not fail solely on that).
- `must_use_asset_ids` must be a subset of context asset ids.

### 6.3 Vision — `ai/vision/base.py`

```python
class VisionProvider(ABC):
    provider_id: str

    async def review_image(self, request: VisionQaRequest) -> VisionQaResult: ...
```

`VisionQaRequest`: `user_id`, image bytes **or** owner-scoped storage path already resolved by the pipeline (never a user-supplied path), `CreativeBrief`, `CreativeContext`.

`VisionQaResult`:

```json
{
  "passed": true,
  "score": 0.0,
  "reasons": [],
  "violations": [],
  "requires_regenerate": false
}
```

Hard-fail if any:

- Invented logo when `logo_asset` was provided
- Wrong product vs selected product image
- Invented price / offer text
- Generic festival greeting with no business grounding
- Unreadable / not a still photo suitable for IG feed

Mock vision **always passes** unless the test injects failure. Transport failure → `VISION_PROVIDER_ERROR` 503; do not auto-publish.

### 6.4 Image — `ai/image/base.py`

Keep:

```python
class ImageGenerationProvider(ABC):
    async def generate(self, request: ImageGenerationRequest) -> GeneratedImage: ...
```

Add optional:

```python
    async def edit(self, request: ImageEditRequest) -> GeneratedImage: ...
```

`ImageEditRequest`: `prompt`, `user_id`, `source`, `reference_images: list[ReferenceImage]` with `role` in `{logo, product, other}` and bytes already owner-checked.

OpenAI provider:

- Model from `OPENAI_IMAGE_MODEL` / `IMAGE_MODEL` (config, not a code constant).
- Writes through existing `GeneratedImageStore.save()` / `MediaStorageService`.
- Never publishes.
- If `edit` is unimplemented by the API for a given model, fall back to `generate` **only when no reference images were supplied**. If references exist and edit is unsupported → `OPENAI_API_ERROR` (do not silently drop the logo).

Mocks: `edit` may call `generate` after recording `reference_images` so tests can assert logo attach.

### 6.5 Content Agent duck-typing (legacy path)

Current `_call_llm` order:

1. `generate_structured(system_prompt=, user_prompt=, schema=)`
2. `generate_content_plan(ContentPlanRequest)`

Do not remove this. Pipeline uses `generate_creative_brief` first, then `generate_content_plan`.

Current `_generate_image` duck-types `generate(prompt=, user_id=, source=, metadata=)` — keep that for the legacy path.

---

## 7. MCP contracts

MCP is an **in-process allowlisted tool bus**. It is not SQLAlchemy. Handlers call repositories.

The LLM does **not** choose MCP tool names in this phase. `context_builder.build_creative_context` is the Content Agent’s single planning entry and may call multiple internal tools. Tools are still registered so later phases can expose them carefully.

### 7.1 Context (injected)

```python
@dataclass(frozen=True)
class McpContext:
    user_id: str
    business_id: str
    request_id: str | None
    capabilities: frozenset[str]
```

Capabilities: `festival`, `business`, `product`, `brand`, `asset`, `content`, `canva`.

`tenant_isolation.bind_context(user, profile) -> McpContext`.  
`tenant_isolation.strip_tenant_args(arguments)` drops `user_id` / `business_id`.  
`authorize_resource(context, row.user_id)` fail-closed 404 (same as `get_owned`).

### 7.2 Client

```python
class McpClient:
    async def call(self, tool: str, arguments: dict, context: McpContext) -> dict: ...
    async def build_creative_context(self, context: McpContext, **hints) -> CreativeContext: ...
```

| Failure | Code | HTTP |
| --- | --- | --- |
| Unknown tool | `TOOL_NOT_ALLOWED` | 400 |
| Deadline | `MCP_TIMEOUT` | 504 |
| Client/registry down | `MCP_UNAVAILABLE` | 503 |
| Missing capability | `MCP_FORBIDDEN` | 403 |
| Canva disabled / auth | `CANVA_UNAVAILABLE` | 503 |
| Canva deadline | `CANVA_TIMEOUT` | 504 |

No unbounded retries. One retry only for transient 5xx on **external** Canva, still inside `CANVA_TIMEOUT_SECONDS`.

### 7.3 Internal tools

| Tool | Capability | Input | Output (no filesystem secrets) |
| --- | --- | --- | --- |
| `festival.list` | festival | `year` | rows from `festivals_for_year` |
| `festival.current` | festival | `on_date` optional | due campaign + **canonical date from catalog/DB** |
| `festival.campaign` | festival | `campaign_id` | remaining/required/published; 404 if not owned |
| `business.profile` | business | none | name, type, category, vertical, location, language, audience |
| `business.vertical_playbook` | business | none | retail vs F&B constraints (static Python, not LLM memory) |
| `product.list` | product | `active_only` | id, name, description, category |
| `product.get` | product | `product_id` | + image asset ids |
| `product.active_offer` | product | `product_id` optional | offer text/price **from DB only** |
| `brand.guidelines` | brand | none | voice, colors, do/don’t |
| `asset.list` | asset | `kind` | metadata + `media_url` |
| `asset.get` | asset | `asset_id` | metadata; bytes only in-process for image/vision |
| `content.recent` | content | `limit` | themes, types, festival names (no tokens) |

Canva (optional, external JSON-RPC/HTTP MCP):

| Tool | Input | Output |
| --- | --- | --- |
| `canva.list_templates` | `hint` | template ids |
| `canva.compose` | `brief` fields + `asset_ids` | PNG stored via MediaStorage `source=CANVA`, then same QA path |
| `canva.export` | `design_id` | same |

Canva handler must not import `PublicationGateway` or Instagram tools.

### 7.4 CreativeContext (what DeepSeek may see)

Assembled by `context_builder.py`:

- business profile (no password, no Instagram token)
- vertical playbook
- selected product name/description + “use provided product photo”
- active offer **verbatim**
- logo asset id (binary attached later to the image call)
- festival **name + date from catalog** + campaign remaining
- recent themes
- current date/timezone from `Clock` / automation timezone

Never included: API keys, JWT, Meta tokens, other users’ ids, Canva secrets, filesystem paths in the LLM payload (use asset ids).

### 7.5 Retail + F&B playbook

Not a new model. Static map in `mcp/tools/business.py` (or `mcp/playbooks.py`):

| Vertical | Signals on `business_type` / `business_category` | Rules |
| --- | --- | --- |
| `RETAIL` | jewellery, jewelry, apparel, grocery, electronics, boutique, store | hero SKU; packshot respect; MRP/price only if offer row exists |
| `FNB` | restaurant, cafe, bakery, cloud kitchen, food, dining | plated dish / menu item; no fake FSSAI/hygiene claims; hours/offers only from DB |
| `OTHER` | fallback | treat as retail-safe: no invented claims |

`BusinessProfile.vertical` optional column. If null, infer with a deterministic lowercase substring map; default `RETAIL`.

### 7.6 Product selection policy (backend)

Used by context_builder and schedulers:

1. If request has `product_id` → `get_owned`; 404 if foreign/missing.
2. Else if active products exist → pick the active product whose id appears least recently on this user’s `generated_images.product_id` (nulls last). Tie-break: created_at, then name.
3. Else → no product; pipeline generates from profile labels only (legacy-compatible).

Offers: prefer `ACTIVE` offer for that product whose `starts_on`/`ends_on` contain today in the account timezone; else none. Never invent.

---

## 8. Database migration plan

**Revision:** `002_brand_catalog_and_creative`  
**Down-revision:** `001_initial_schema`  
**Important:** `001` uses `Base.metadata.create_all`. Fresh DBs get new tables from `init_db()` `create_all`. **Existing** SQLite files that already ran 001 will **not** gain new columns from `create_all`. Agent A must write **explicit** `op.create_table` / `op.add_column` / indexes in 002.

VARCHAR enums (`native_enum=False`) as today. SQLite + PostgreSQL.

Do **not** drop `business_profiles.products` JSON. Keep as denormalized labels. Catalog rows are source of truth when present.

### 8.1 New enums (`db/enums.py`)

- `BusinessVertical`: `RETAIL`, `FNB`, `OTHER`
- `AssetKind`: `LOGO`, `PRODUCT_PHOTO`, `PACKAGING`, `STOREFRONT`, `OTHER`
- `AssetStatus`: `ACTIVE`, `ARCHIVED`
- `OfferStatus`: `DRAFT`, `ACTIVE`, `EXPIRED`
- `QaStatus`: `PENDING`, `PASSED`, `FAILED`, `SKIPPED`
- `CreativeJobStatus`: `PLANNING`, `GENERATING`, `QA`, `READY`, `QA_FAILED`, `CANCELLED`
- `ImageSource`: add `CANVA` (existing values stay)

### 8.2 Additive on `business_profiles`

- `vertical` VARCHAR nullable

### 8.3 New tables (all `user_id` FK → `users.id` ON DELETE CASCADE)

**`brand_guidelines`** (1:1 user)

- `id`, `user_id`, `business_profile_id`
- `voice`, `tone`, `primary_colors` JSON, `fonts` JSON, `do_not` Text, `legal_name`
- timestamps

**`brand_assets`**

- `id`, `user_id`, `business_profile_id`
- `kind`, `filename`, `storage_path`, `mime_type`, `width`, `height`
- `is_primary_logo` bool
- `status`
- Partial unique: one primary logo per user (`uq_brand_assets_primary_logo` WHERE `is_primary_logo` AND `kind='LOGO'` AND `status='ACTIVE'`)

**`products`**

- `id`, `user_id`, `business_profile_id`
- `name`, `description`, `category`, `sku` nullable
- `is_active` bool default true
- Unique `(user_id, sku)` when sku is not null (partial unique)

**`product_images`**

- `id`, `product_id` FK, `user_id`
- `asset_id` FK `brand_assets`
- `is_primary` bool

**`offers`**

- `id`, `user_id`, `business_profile_id`, `product_id` nullable
- `title`, `body` (verbatim creative text)
- `price_text` nullable (string; never invented)
- `status`, `starts_on`, `ends_on` dates

**`creative_jobs`**

- `id`, `user_id`, `source`
- `product_id`, `offer_id`, `festival_campaign_id`, `local_date`
- `status`, `generated_image_id`, `qa_status`, `qa_json`
- `fingerprint` string not null
- Unique `(user_id, fingerprint)` WHERE status IN (`PLANNING`,`GENERATING`,`QA`) — SQLite/Postgres partial unique

### 8.4 Additive on `generated_images` (all nullable)

- `qa_status`, `qa_json`
- `product_id`, `offer_id`
- `logo_asset_id`
- `creative_job_id`

Old rows remain valid. Counting still ignores these fields.

### 8.5 Brand media paths

Extend `services/media_paths.py`:

- Add stage `brand` to allowed stages.
- Brand originals: `storage/brand/{user_id}/asset_<32 hex>.(png|jpg|jpeg|svg)`
- SVG **only** for `AssetKind.LOGO` originals. Instagram publish still requires raster JPEG (existing validator). Compose/edit step rasterizes logo onto the generated canvas before handoff.
- Path traversal rules unchanged (`assert_safe_user_id`, resolve inside `media_root`).

### 8.6 Repositories

`BrandGuidelineRepository`, `BrandAssetRepository`, `ProductRepository`, `ProductImageRepository`, `OfferRepository`, `CreativeJobRepository`.

Every read/write: `get_owned(user_id, id)`. MCP tools use repos only — no raw SQL in `mcp/tools`.

`Database` UoW grows matching attributes + aliases (`db.products`, `db.brand_assets`, `db.offers`, `db.guidelines`, `db.creative_jobs`).

### 8.7 Counting (unchanged)

Festival/daily counts increment **only** on verified Instagram `PUBLISHED`. QA failure, Canva export, and `GENERATED` do not count as posts.

---

## 9. Image pipeline (normative)

Implemented in `agent/creative_pipeline.py`, invoked by Content Agent when mcp+vision are present:

1. Resolve tenant from authenticated request (`user_id`, `business_id`). No profile → `CONTENT_PROFILE_MISSING`.
2. Select product/offer (ADR-011).
3. `mcp.build_creative_context(...)`.
4. Duplicate-generation check (`creative_jobs` fingerprint). In-flight or same-day daily fingerprint → skip (`CONFLICT` / scheduler skip). Publication duplicates remain the gateway’s job.
5. DeepSeek `generate_creative_brief`. Existing policy + diversity + festival-business-specificity. Copy offer/festival date from context.
6. Build OpenAI `edit` (preferred) or `generate`: attach real logo + product bytes; prompt forbids redrawing them.
7. Optional Canva compose if enabled **and** brief `canva_template_hint` is set; on timeout, fall back to step 6.
8. Persist bytes via existing `MediaStorageService`.
9. DeepSeek Vision QA.  
   - fail → one regenerate from step 5/6.  
   - fail again → `qa_status=FAILED`, `handoff.ready=false`, no auto-publish.
10. Existing approval policy: USER_PROMPT always pending; daily/festival auto flags; **auto-approve requires QA passed**.
11. Handoff data only. Instagram Agent runs after approve / auto-approve via `PublicationService.publish_generated_image`.

Integrity:

- Do not ask OpenAI to recreate the company logo if `logo_asset` exists.
- Do not invent product prices, offers, names, logos, festival dates, brand claims.
- User-prompt mode still never auto-publishes.

---

## 10. API compatibility plan

### 10.1 Frozen contracts

| Method | Path | Rule |
| --- | --- | --- |
| POST | `/api/v1/instagram/publish` | multipart JPEG/PNG; optional caption already in WIP; JSON `PublishResponse` unchanged |
| POST | `/api/v1/generation` | body `{ "prompt": "..." }` still valid |
| POST | `/api/v1/generation/{id}/approve` | still Instagram Agent |
| GET/PUT | `/api/v1/business` | existing fields required; `vertical` optional |
| GET/PUT | `/api/v1/automation` | unchanged |
| POST | `/api/v1/automation/run-now` | unchanged semantics |
| GET | `/api/v1/festivals*` | catalog still from `festivals/` |
| GET | `/api/v1/health` | public |
| Auth | `/api/v1/auth/*` | unchanged |

Frontend `generationApi.create(prompt)` continues to POST `{ prompt }` only.

### 10.2 Additive request fields

`POST /generation`:

```json
{ "prompt": "...", "product_id": null, "offer_id": null, "use_canva": false }
```

Pydantic `extra=ignore`. Unknown keys ignored.

`GET /generation/{id}` / list: may include `qa_status`, `product_id`, `media_url`. Clients that ignore unknown keys keep working.

### 10.3 New routes (all `require_password_ok`, owner-scoped)

| Method | Path |
| --- | --- |
| GET/PUT | `/api/v1/brand/guidelines` |
| GET/POST | `/api/v1/brand/assets` |
| POST | `/api/v1/brand/assets` multipart (`kind=LOGO\|PRODUCT_PHOTO|...`) |
| GET | `/api/v1/brand/assets/{id}/media` |
| DELETE | `/api/v1/brand/assets/{id}` |
| GET/POST | `/api/v1/products` |
| GET/PUT/DELETE | `/api/v1/products/{id}` |
| POST | `/api/v1/products/{id}/images` |
| GET/POST | `/api/v1/offers` |
| GET/PUT | `/api/v1/offers/{id}` |

Responses never include `storage_path`. Use `media_url` only.

### 10.4 Error codes (add, do not remove)

| Code | When | HTTP |
| --- | --- | --- |
| `DEEPSEEK_CONFIGURATION_ERROR` | missing key/model | 503 |
| `DEEPSEEK_API_ERROR` | transport after retries | 502 |
| `DEEPSEEK_INVALID_RESPONSE` | bad JSON/schema | 502 |
| `VISION_QA_REJECTED` | QA failed after retry | 422 |
| `VISION_PROVIDER_ERROR` | vision down | 503 |
| `MCP_TIMEOUT` | tool deadline | 504 |
| `MCP_UNAVAILABLE` | registry/client down | 503 |
| `MCP_FORBIDDEN` | capability missing | 403 |
| `CANVA_TIMEOUT` | Canva MCP | 504 |
| `CANVA_UNAVAILABLE` | disabled or auth | 503 |
| `LLM_PROVIDER_UNAVAILABLE` | unknown provider id | 503 |

Keep `OPENAI_*` for OpenAI implementations. Keep `CONTENT_*` for Content Agent policy/diversity/profile.

---

## 11. Failure safety

| Failure | Behavior |
| --- | --- |
| Provider timeout | bounded retries then typed error; no Meta call |
| Invalid API key | 503 configuration; V1 Instagram upload still works |
| Image generation failure | `CONTENT_IMAGE_FAILED`; `handoff.ready=false` |
| Vision transport failure | QA not passed; do not auto-publish |
| Vision QA reject | one regenerate; then `QA_FAILED` |
| MCP timeout | fail generation; scheduler marks daily slot `FAILED` (retryable next tick) |
| Canva timeout | skip Canva, OpenAI compose fallback |
| Duplicate generation | `CONFLICT`; scheduler skip |
| Duplicate festival/daily post | existing SQL + gateway |
| Ambiguous Meta publication | **never auto retry `media_publish`** |
| User A MCP fetch of User B asset | 404 |

---

## 12. Test strategy

### 12.1 Must keep green

Default `pytest` (mocked) + `cd frontend && npm test`.

Priority: `tests/test_ai_providers.py`, `test_content_agent.py`, `test_generation_api.py`, `test_platform_flows.py`, `test_scheduler.py`, `test_instagram_agent_integration.py`, `test_auth.py`, `test_api.py`, `test_openai_llm.py`, `test_openai_image.py`.

`conftest.py` autouse fixture must also patch:

- `ai.llm.openai.AsyncOpenAI` (and keep `ai.openai_llm.AsyncOpenAI`)
- `ai.image.openai.AsyncOpenAI` (and keep `ai.openai_image_generator.AsyncOpenAI`)
- DeepSeek: block `httpx.AsyncClient.send` **or** inject a factory; a `RuntimeError("Default pytest must never make real DeepSeek requests")` if `real_deepseek` marker is absent

`pytest.ini`:

```text
addopts = -q -m "not real_openai and not real_deepseek"
markers =
    real_openai: ...
    real_deepseek: optional tests that perform real DeepSeek API calls
```

### 12.2 New coverage (default suite, mocked)

- Factory: `LLM_PROVIDER=deepseek` → DeepSeek class; `openai` → OpenAI class.
- Missing DeepSeek key → `create_app` uses mocks, does not crash; forced live generate → 503.
- MCP isolation: user B `asset.get` / `product.get` of A’s ids → 404.
- Festival MCP returns catalog date matching `india_festivals.py` / `lunar_dates.json` even if mock LLM returns another date (pipeline overwrites).
- Creative pipeline calls image `edit` with a `logo` reference when a primary logo exists.
- QA fail then pass → one regenerate, pending approval.
- QA fail twice + `auto_daily_publish` → no `handoff.ready`, Instagram client never called.
- Canva disabled: `canva.compose` → `CANVA_UNAVAILABLE`; no publish.
- Catalog API isolation + logo upload path traversal rejected.
- V1 `POST /instagram/publish` still mocked-green.
- `/settings` and `public_ai_status()` omit DeepSeek/Canva keys; logs redact them.
- `POST /generation` with only `{prompt}` still 200 with mocks.

### 12.3 Opt-in live

```bash
RUN_DEEPSEEK_INTEGRATION=1 pytest -m real_deepseek
RUN_OPENAI_INTEGRATION=1 pytest -m real_openai
```

Never in default CI. Presence of keys in `.env` is not enough (same pattern as current OpenAI).

---

## 13. Integration sequence for other agents

Work **serially** against this document. Do not redesign frozen layers.

```text
Agent A (DB)
    → Agent B (AI providers)     [parallel with A after enums exist if B uses only Settings]
    → Agent C (MCP)              [depends on A]
    → Agent D (Content pipeline) [depends on A, B, C]
    → Agent E (Platform API)     [depends on A–D]
    → Agent F (Scheduler)        [depends on D]
    → Agent G (Frontend)         [depends on E]
Agent H (Instagram)              verify only; no feature work
Agent 1                          re-read plan vs diff after land
```

B may start in parallel with A **if** it does not persist catalog rows (mocks + Settings only). C must not start before A’s repos exist.

### Agent A — Database

**Owns:** `db/models.py`, `enums`, `repositories`, `schemas`, `uow`, Alembic `002_*`, `media_paths` brand stage.  
**Must not:** Instagram Agent, FastAPI product routes, MCP tool logic.  
**Done when:** `init_db` + Alembic 002 create tables; `get_owned` tests for products/assets; old DB tests pass.

### Agent B — AI providers

**Owns:** `ai/llm/*`, `ai/vision/*`, `ai/image/*`, shims, `config.py` env fields, `ai/mocks.py`, `tests/test_deepseek_*.py`, `conftest` blocks.  
**Must not:** MCP tools, Instagram tools, frontend.  
**Done when:** factories respect provider env vars; `test_ai_providers.py` and `test_openai_*.py` pass; `openai_configured` still in `public_ai_status()`.

### Agent C — MCP

**Owns:** `mcp/` package, isolation + festival SoT tests.  
**Depends on:** A.  
**Must not:** call OpenAI/DeepSeek or PublicationGateway.  
**Done when:** `build_creative_context` is tenant-safe; festival dates from catalog; Canva disabled by default.

### Agent D — Content Agent + creative pipeline

**Owns:** `agent/content_agent.py`, `agent/creative_pipeline.py`, `models/creative.py`, pipeline tests.  
**Depends on:** A, B, C.  
**Must not:** import Instagram tools.  
**Done when:** constructing `ContentAgent(llm, images)` (no mcp) still passes `test_content_agent.py`; pipeline does logo+QA; user-prompt never auto-publishes; QA fail blocks auto-approve.

### Agent E — Platform API + wiring

**Owns:** `api/catalog_routes.py`, `api/app.py` injection, optional generate body fields, logging redaction, Dockerfile `COPY mcp`.  
**Depends on:** A–D.  
**Must not:** change publish JSON.  
**Done when:** additive routes isolated; `POST /generation` with only `{prompt}` works; `/settings` has no keys.

### Agent F — Scheduler

**Owns:** `daily_scheduler.py`, `festival_scheduler.py` (minimal: product selection + fingerprint).  
**Depends on:** D.  
**Must not:** Graph client.  
**Done when:** double daily tick still one `PUBLISHED`; festival remaining math unchanged; fingerprint prevents double generate.

### Agent G — Frontend

**Owns:** catalog UI (logos, products, offers), optional product picker on generate, types/endpoints.  
**Must not:** DeepSeek/OpenAI/Canva keys. Keep `generationApi.create(prompt)` signature working. Manual Approve & Post remains. Business textarea `products` field stays (denormalized labels).  
**Done when:** Vitest + existing page tests pass; browser-verify generate/approve/instagram plus new catalog.

### Agent H — Instagram Agent

**Owns:** nothing in this upgrade.  
**Verify only:** `POST /instagram/publish`, recovery, AMBIGUOUS, VERIFY_FIRST.  
If caption WIP is uncommitted, finish it without changing the tool allowlist.

### Agent 1 — after others land

- Re-read this plan vs git diff.
- Update `AI_ARCHITECTURE.md` + `brain.md` to match shipped code.
- Do not expand scope (no Reels, no Postgres cutover, no embeddings).

---

## 14. Compatibility checklist (exit criteria)

- [ ] Default `pytest` green
- [ ] Frontend `npm test` + `npm run build` green
- [ ] `POST /api/v1/instagram/publish` contract unchanged
- [ ] Generate → Approve & Post still the manual path
- [ ] `POST /generation` `{ "prompt": "..." }` still valid
- [ ] User-prompt never auto-publishes
- [ ] Daily double-tick → one verified post
- [ ] Festival required/published/remaining rules unchanged
- [ ] Ambiguous publication never republishes
- [ ] User A cannot access User B logos/products/offers/Canva/MCP
- [ ] No `VITE_*` secrets; `/settings` has no keys
- [ ] Festival dates still from `festivals/` not the model
- [ ] Instagram Agent still the only Meta publisher
- [ ] Missing OpenAI/DeepSeek keys still boot the app on mocks

---

## 15. Out of scope

- Rewriting the app into a `backend/` monorepo folder
- Multi-business-per-user orgs (schema may carry `business_profile_id`; APIs stay 1:1)
- Semantic embeddings diversity
- Reels / Stories / carousels
- Making Canva or DeepSeek a publisher
- Switching production DB to Postgres / Supabase (parked unless requested)
- Pointing `LLMPlanner` at DeepSeek with Instagram tools in scope (**forbidden**)
- Deleting `business_profiles.products` JSON or the Business page textarea
- Changing V1 multipart field names (`image` / `file`)

---

## 16. Agent 1 sign-off

Inspection confirms the current system already has the right control boundary: FastAPI owns decisions, Content Agent never publishes, Instagram Agent is the only Meta path, festivals are data-driven, and users are isolated by `user_id`.

This upgrade **inserts** MCP context, DeepSeek reasoning, OpenAI image edit-with-assets, and DeepSeek Vision QA **in front of** the existing approval + PublicationGateway chain. It does not replace that chain.

Subsequent agents implement only their owned files in §3–§4. If a change is required in a frozen file, stop and return to Agent 1.
