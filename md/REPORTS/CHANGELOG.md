# Changelog

Documentation history for this repository. Application code was not changed for the 2026-09-24 documentation updates.

## 2026-09-24 — Provider and asset status

Pages under `md/` were aligned with HEAD `8ad7dc4`:

- DeepSeek is the reasoning, trend-analysis, and content-planning provider. `LLM_PROVIDER` defaults to `deepseek`, not `openai`.
- OpenAI is image generation and editing. Logo and product bytes go MCP → OpenAI edit when a file exists.
- `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are **IMPLEMENTED — NOT CONFIGURED**.
- Production logos and product images are not uploaded yet.
- Instagram intelligence is Meta Graph → MCP → DeepSeek.
- Canva is creative-only and never publishes.
- The Instagram Agent remains the only Meta publisher.
- `META_ACCESS_TOKEN` is off unless `APP_ENV` is `development`, `dev`, or `local` and `INSTAGRAM_LEGACY_ENV_FALLBACK=true`.

## 2026-09-24 — Documentation tree

All project Markdown was consolidated under `md/`. Pages were rewritten from the working tree: FastAPI routes, `db/models.py`, MCP allowlist, agents, providers, festival catalog, and tests.

Corrections recorded in [documentation audit](FINAL_DOCUMENTATION_AUDIT.md):

- Studio planning is DeepSeek when `DEEPSEEK_API_KEY` is set. The scheduler follows `LLM_PROVIDER`, whose default is `deepseek`. An earlier note that the default was `openai` is obsolete.
- MCP allowlist size is 22 tools.
- Image bytes are sent on the OpenAI edit path, not on text-only `images.generate`.
- The festival scheduler uses the 47 `FESTIVALS` entries, not every name in `coverage.json`.
- Older root reports that described trends or MCP as absent are removed.

## 2026-09-24 — Implementation already in the tree (code, not this doc change)

Uncommitted relative to git `6c69ffe` at the time of writing, and present in modules this documentation cites:

- Alembic `002`–`006`: brand assets, Canva tokens, image QA columns, trend research, trend intelligence.
- In-process MCP, trend researcher, trend scheduler, account intelligence routes.
- Content orchestrator, opportunity engine, optional Canva adapter.

## 2026-09-21 — Earlier implementation report

`FINAL_IMPLEMENTATION_REPORT.md` at the repository root described the upload agent, auth, and OpenAI content plans. It did not describe MCP, DeepSeek-as-studio-planner, Canva, or trend tables. That file is retired. The current report is [FINAL_IMPLEMENTATION_REPORT.md](FINAL_IMPLEMENTATION_REPORT.md).
