# Changelog

Documentation history for this repository. Application code was not changed for the 2026-09-24 documentation consolidation.

## 2026-09-24 — Documentation tree

All project Markdown was consolidated under `md/`. Pages were rewritten from the working tree: FastAPI routes, `db/models.py`, MCP allowlist, agents, providers, festival catalog, and tests.

Corrections recorded in [documentation audit](FINAL_DOCUMENTATION_AUDIT.md):

- Studio planning is DeepSeek only when `DEEPSEEK_API_KEY` is set. The scheduler still follows `LLM_PROVIDER` (default `openai`).
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
