# Final documentation audit

Date: 2026-09-24. The inventory below was taken at git `6c69ffe`. Later the same day, HEAD `8ad7dc4` was documented again. Where this page still says the scheduler default is `openai`, or lists the process Meta token and missing provider keys as unfinished code, those sentences are superseded by [Final integration audit](FINAL_INTEGRATION_AUDIT.md) and the 2026-09-24 provider section in [Changelog](CHANGELOG.md). FastAPI version `2.0.0`.

## Documentation inventory

Project Markdown files found before consolidation, excluding `node_modules`, `.venv`, and `.pytest_cache`: **29**.

| File | Location before | Purpose | Duplicate of | Action |
| --- | --- | --- | --- | --- |
| `README.md` | root | Install and an OpenAI-only architecture diagram | Partly `ARCHITECTURE.md` | Kept as a short pointer |
| `ARCHITECTURE.md` | root | Architecture map, partly updated 2026-09-24 | `md/ARCHITECTURE.md` (new) | Moved into `md/` and corrected |
| `AI_ARCHITECTURE.md` | root | Provider split | `md/AI_ARCHITECTURE.md` | Moved and corrected |
| `CONTENT_AGENT.md` | root | Content agent | `md/AGENTS/CONTENT_AGENT.md` | Moved and split from the orchestrator |
| `INSTAGRAM_AGENT.md` | root | Publisher | `md/AGENTS/INSTAGRAM_AGENT.md` | Moved |
| `MCP.md` | `md/` | Allowlist overview | `md/MCP.md` | Rewritten in place |
| `CANVA.md` | root | Canva | `md/MCP/CANVA_MCP.md` | Merged |
| `md/CANVA_MCP.md` | `md/` | Canva pointer | `md/MCP/CANVA_MCP.md` | Merged, old path removed |
| `md/DEEPSEEK.md` | `md/` | DeepSeek | `md/AI/DEEPSEEK.md` | Moved |
| `md/OPENAI.md` | `md/` | OpenAI | `md/AI/OPENAI.md` | Moved |
| `md/TREND_RESEARCH.md` | `md/` | Trends | `md/INTELLIGENCE/TREND_RESEARCH.md` | Moved |
| `md/INSTAGRAM_INTELLIGENCE.md` | `md/` | Account reads | `md/INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md` | Moved |
| `md/BRAND_ASSETS.md` | `md/` | Assets | `md/CONTENT/BRAND_ASSETS.md` | Moved |
| `md/PRODUCTS.md` | `md/` | Products | `md/CONTENT/PRODUCTS.md` | Moved |
| `SCHEDULER.md` | root | Daily and festival only | `md/AUTOMATION/SCHEDULER.md` | Merged; trend tick added |
| `FESTIVAL_AUTOMATION.md` | root | Festival rules | `md/AUTOMATION/FESTIVAL_AUTOMATION.md` | Moved |
| `DATABASE.md` | root | Core tables, missing 002–006 | `md/DATABASE/` | Merged and extended |
| `MEDIA_STORAGE.md` | root | Generated files | `md/STORAGE/MEDIA_STORAGE.md` | Moved |
| `AUTHENTICATION.md` | root | Auth | `md/API/AUTHENTICATION.md` | Moved |
| `FRONTEND.md` | root | UI, missing `/trends` | `md/FRONTEND/FRONTEND.md` | Moved and extended |
| `FINAL_IMPLEMENTATION_REPORT.md` | root | 21 September snapshot | `md/REPORTS/FINAL_IMPLEMENTATION_REPORT.md` | Replaced |
| `FINAL_INTEGRATION_AUDIT.md` | root | Older audit | `md/REPORTS/FINAL_INTEGRATION_AUDIT.md` | Replaced |
| `ARCHITECTURE_AUDIT.md` | root | Audit notes | Integration audit | Merged, then removed |
| `docs/TREND_INTELLIGENCE_ARCHITECTURE.md` | `docs/` | Design contract, body was ahead of or behind code | `md/INTELLIGENCE/TREND_INTELLIGENCE_ARCHITECTURE.md` | Replaced from code |
| `docs/TREND_INTELLIGENCE_FINAL_AUDIT.md` | `docs/` | Marked trend modules absent | Integration audit | Removed as stale |
| `docs/AI_MCP_INTEGRATION_PLAN.md` | `docs/` | Plan, not a description of the tree | Architecture and MCP pages | Removed |
| `md/DIFF.md` | `md/` | Temporary drift note | This audit | Removed |
| `md/README.md` | `md/` | Old index | `md/README.md` | Rewritten |
| `brain.md` | root | Agent scratch pad pointing at deleted paths | Architecture | Removed |

## Final documentation tree

```text
README.md                          pointer to md/README.md
md/
├── README.md
├── ARCHITECTURE.md
├── AI_ARCHITECTURE.md
├── MCP.md
├── AGENTS/
│   ├── CONTENT_AGENT.md
│   ├── INSTAGRAM_AGENT.md
│   ├── TREND_INTELLIGENCE_AGENT.md
│   └── ACCOUNT_INTELLIGENCE_AGENT.md
├── AI/
│   ├── DEEPSEEK.md
│   ├── OPENAI.md
│   └── IMAGE_GENERATION.md
├── MCP/
│   ├── MCP_ARCHITECTURE.md
│   ├── CANVA_MCP.md
│   ├── TREND_MCP.md
│   ├── INSTAGRAM_MCP.md
│   ├── PRODUCT_MCP.md
│   ├── BRAND_ASSET_MCP.md
│   └── FESTIVAL_MCP.md
├── INTELLIGENCE/
│   ├── TREND_RESEARCH.md
│   ├── INSTAGRAM_INTELLIGENCE.md
│   ├── TREND_INTELLIGENCE_ARCHITECTURE.md
│   └── CONTENT_OPPORTUNITIES.md
├── CONTENT/
│   ├── CONTENT_GENERATION.md
│   ├── BRAND_ASSETS.md
│   ├── PRODUCTS.md
│   └── IMAGE_QA.md
├── AUTOMATION/
│   ├── SCHEDULER.md
│   ├── FESTIVAL_AUTOMATION.md
│   └── DAILY_AUTOMATION.md
├── DATABASE/
│   ├── DATABASE.md
│   ├── DATABASE_SCHEMA.md
│   └── MIGRATIONS.md
├── API/
│   ├── API_REFERENCE.md
│   ├── AUTHENTICATION.md
│   └── INSTAGRAM_API.md
├── FRONTEND/
│   └── FRONTEND.md
├── STORAGE/
│   └── MEDIA_STORAGE.md
├── SECURITY/
│   ├── SECURITY.md
│   ├── SECRETS.md
│   └── TENANT_ISOLATION.md
├── TESTING/
│   ├── TESTING.md
│   └── INTEGRATION_TESTS.md
└── REPORTS/
    ├── FINAL_IMPLEMENTATION_REPORT.md
    ├── FINAL_INTEGRATION_AUDIT.md
    ├── CHANGELOG.md
    └── FINAL_DOCUMENTATION_AUDIT.md
```

`node_modules`, `.venv`, and `.pytest_cache` still contain upstream README files. Those are dependency licenses, not project documentation.

## Files moved

Content now lives at the `md/` path. The old path was deleted.

| From | To |
| --- | --- |
| `ARCHITECTURE.md` | `md/ARCHITECTURE.md` |
| `AI_ARCHITECTURE.md` | `md/AI_ARCHITECTURE.md` |
| `CONTENT_AGENT.md` | `md/AGENTS/CONTENT_AGENT.md` |
| `INSTAGRAM_AGENT.md` | `md/AGENTS/INSTAGRAM_AGENT.md` |
| `md/DEEPSEEK.md` | `md/AI/DEEPSEEK.md` |
| `md/OPENAI.md` | `md/AI/OPENAI.md` |
| `md/TREND_RESEARCH.md` | `md/INTELLIGENCE/TREND_RESEARCH.md` |
| `md/INSTAGRAM_INTELLIGENCE.md` | `md/INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md` |
| `md/BRAND_ASSETS.md` | `md/CONTENT/BRAND_ASSETS.md` |
| `md/PRODUCTS.md` | `md/CONTENT/PRODUCTS.md` |
| `CANVA.md` and `md/CANVA_MCP.md` | `md/MCP/CANVA_MCP.md` |
| `SCHEDULER.md` | `md/AUTOMATION/SCHEDULER.md` |
| `FESTIVAL_AUTOMATION.md` | `md/AUTOMATION/FESTIVAL_AUTOMATION.md` |
| `DATABASE.md` | `md/DATABASE/DATABASE.md` and `DATABASE_SCHEMA.md` |
| `MEDIA_STORAGE.md` | `md/STORAGE/MEDIA_STORAGE.md` |
| `AUTHENTICATION.md` | `md/API/AUTHENTICATION.md` |
| `FRONTEND.md` | `md/FRONTEND/FRONTEND.md` |
| `FINAL_IMPLEMENTATION_REPORT.md` | `md/REPORTS/FINAL_IMPLEMENTATION_REPORT.md` |
| `FINAL_INTEGRATION_AUDIT.md` | `md/REPORTS/FINAL_INTEGRATION_AUDIT.md` |
| `docs/TREND_INTELLIGENCE_ARCHITECTURE.md` | `md/INTELLIGENCE/TREND_INTELLIGENCE_ARCHITECTURE.md` |

## Files merged

| Sources | Result |
| --- | --- |
| Root `CANVA.md`, `md/CANVA_MCP.md` | `md/MCP/CANVA_MCP.md` |
| Root `DATABASE.md` plus models and Alembic `002`–`006` | `md/DATABASE/*` |
| Root `SCHEDULER.md` plus `scheduler/trend_scheduler.py` | `md/AUTOMATION/SCHEDULER.md` and `DAILY_AUTOMATION.md` |
| `ARCHITECTURE_AUDIT.md`, `md/DIFF.md`, `docs/TREND_INTELLIGENCE_FINAL_AUDIT.md` | `md/REPORTS/FINAL_INTEGRATION_AUDIT.md` and this file |
| `docs/AI_MCP_INTEGRATION_PLAN.md` | Not kept. Implemented behavior is in `md/MCP.md` and `md/AI_ARCHITECTURE.md` |
| `brain.md` | Current facts folded into `md/ARCHITECTURE.md`. Scratch notes were not copied |

## Files removed

| File | Why |
| --- | --- |
| Root architecture, agent, auth, database, frontend, scheduler, festival, storage, and Canva pages | Replaced by `md/` |
| `brain.md` | Working memory that linked to `docs/` and `md/DIFF.md` |
| `ARCHITECTURE_AUDIT.md` | Superseded by the integration audit |
| `docs/AI_MCP_INTEGRATION_PLAN.md` | Plan, not the implementation |
| `docs/TREND_INTELLIGENCE_FINAL_AUDIT.md` | Said trend research, trend MCP, and opportunities were absent. Those modules exist |
| `docs/TREND_INTELLIGENCE_ARCHITECTURE.md` | Design body no longer matched storage, MCP, and the scheduler |
| `md/DIFF.md` | Temporary comparison written while docs were drifting |
| Previous `md/*.md` copies of DeepSeek, OpenAI, trends, products, brand, Canva, Instagram intelligence | Replaced by the subdirectory pages |

Root `README.md` stays. It points at `md/README.md` and keeps the install commands.

## Files created

Every file in the tree above except the rewritten `md/README.md`, `md/ARCHITECTURE.md`, `md/AI_ARCHITECTURE.md`, and `md/MCP.md` is new. Those four were rewritten in place. `md/REPORTS/CHANGELOG.md`, `md/REPORTS/FINAL_DOCUMENTATION_AUDIT.md`, and the agent, API, security, testing, content, and automation pages had no previous file at that path.

## Outdated claims corrected

| Old claim | What the code does |
| --- | --- |
| Root README: the only LLM is OpenAI | Studio and scheduler plans use DeepSeek. `LLM_PROVIDER` defaults to `deepseek`. A missing key is **IMPLEMENTED — NOT CONFIGURED**, not an OpenAI planner |
| Single pipeline "DeepSeek then OpenAI" with no branch | Two planner paths. Images are OpenAI or the mock. Vision is DeepSeek or a structural check |
| MCP allowlist of 10 or "22" guessed from an older note | `MCP_TOOL_ALLOWLIST` has 22 names. Confirmed by import on 2026-09-24 |
| Trend subsystem absent | `backend/trends/`, trend MCP, `/api/v1/trends`, Alembic `005` and `006` |
| Database docs stopped at the first schema | Brand, Canva, QA, and trend tables are in `db/models.py` |
| Frontend docs omitted `/trends` | `App.tsx` has `/trends` and four child routes |
| Festival list invented in prose | Names are the 47 `FESTIVALS` entries. `coverage.json` extras are not scheduled |
| Product and logo images always ground OpenAI | Bytes go out on `OpenAIImageProvider.edit`. Text-only `images.generate` receives labels |
| `STORAGE_RETRY_ATTEMPTS` as an environment variable | `Settings.storage_retry_attempts` is a class default. `from_env` does not read it |
| Canva publishes to Instagram | No publish capability. `CanvaAdapter.apply` returns `applied: false` without an explicit capability |
| `real_deepseek` tests exist because the marker exists | Marker is in `pytest.ini`. No test uses it |

## Implementation gaps

Documented with the ratings in [Final integration audit](FINAL_INTEGRATION_AUDIT.md).

Superseded on HEAD `8ad7dc4`. Do not treat the bullets below as the current provider story. Current status is in the integration audit.

Still true in code:

- Graph omits some insight fields; `shares` is a column and is not requested
- Duplicate `GET /api/v1/admin/users`; `api/instagram_account_routes.py` is not mounted
- `JWT_SECRET` falls back to `dev-change-me` when unset
- DeepSeek does not generate images
- `SemanticSimilarityBackend` and production `LLMPlanner` are not the live planners
- `SessionContextStore` festival context and task persistence are incomplete
- Live `real_deepseek` is excluded from default pytest on purpose
- Instagram OAuth code flow is not implemented; connect accepts a pasted token
- Festivals that exist only in `coverage.json` are not scheduled
- Reels, stories, and carousels are not published

No longer accurate as gaps:

- Scheduler reasoning defaults to `deepseek`, not `openai`
- Missing DeepSeek and OpenAI keys are **IMPLEMENTED — NOT CONFIGURED**
- Reference pixels go out on the OpenAI edit path when MCP has an `AVAILABLE` file. Production logos and product images are not uploaded yet
- `META_ACCESS_TOKEN` does not publish in production. It requires `INSTAGRAM_LEGACY_ENV_FALLBACK` and `APP_ENV` of `development`, `dev`, or `local`
- Canva `query` / `produce` is the creative path. Canva never publishes. It is **IMPLEMENTED — NOT CONFIGURED** because `CANVA_ENABLED` defaults to false

FAIL: none recorded. The publisher boundary holds.

## Verification

### Documentation link check

Relative links under `md/` were resolved on disk. The check covered 46 Markdown files and 207 relative links. Broken links: 0. Application code does not import the removed Markdown paths.

### API documentation check

Routes in `md/API/API_REFERENCE.md` were taken from the decorators in `api/app.py`, `api/auth_routes.py`, `api/routes.py`, `api/generation.py`, `api/platform_routes.py`, `api/asset_routes.py`, `api/canva_routes.py`, `api/trend_routes.py`, and `api/intelligence_routes.py`. `api/instagram_account_routes.py` is documented as unmounted.

### MCP documentation check

Tool names in `md/MCP.md` match `MCP_TOOL_ALLOWLIST` (22). Publish aliases are rejected in `backend/mcp/permissions.py`.

### Environment variable check

Names in the provider pages match `Settings.from_env` in `config.py`. Several of those names are not repeated in `.env.example` (`DEEPSEEK_BASE_URL`, `SCHEDULER_INTERVAL_SECONDS`, `LLM_MAX_ATTEMPTS`, `TREND_*`, `BCRYPT_ROUNDS`, `AGENT_TIMEOUT_SECONDS`, and the extra Canva URL overrides). They are real settings with code defaults. `.env.example` is a subset, not the full set.

`storage_retry_attempts` and `verification_retry_attempts` are not environment variables. The agent and storage pages say so.

### Architecture consistency check

`md/ARCHITECTURE.md` and `md/AI_ARCHITECTURE.md` both state the two planner paths and that only the Instagram Agent publishes. No remaining project Markdown file tells the reader that OpenAI is the only model or that MCP can publish.

### Test status

| Command | Result |
| --- | --- |
| `python -m pytest` on the Anaconda base interpreter | Failed at collection: `ModuleNotFoundError: No module named 'openai'` |
| `.venv\Scripts\python.exe -m pytest` at the first documentation pass | **372 passed, 4 skipped, 2 deselected** |
| `python -m pytest` on HEAD `8ad7dc4` | **421 passed, 4 skipped, 2 deselected**. Skips are Meta credential gates. Deselected tests are `real_openai` |
| `npm test` in `frontend/` | **13 files, 39 tests passed** |
| `npm run build` | Succeeded on the later pass |

The earlier collection failure on the Anaconda base interpreter (`ModuleNotFoundError: No module named 'openai'`) is an environment gap, not a product failure.

## Completion

The route, table, and tool inventory above still matches the modules checked on that pass. Provider, asset, Canva, and Meta-token status was corrected later the same day. Use [Final integration audit](FINAL_INTEGRATION_AUDIT.md) for those ratings. Missing DeepSeek and OpenAI keys, and missing production logos, are **IMPLEMENTED — NOT CONFIGURED**.
