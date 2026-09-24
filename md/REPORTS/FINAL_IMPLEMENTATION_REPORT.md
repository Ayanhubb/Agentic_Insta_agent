# Final implementation report

Verified against the working tree on 2026-09-24. This replaces the 21 September snapshot that predated MCP, DeepSeek planning, Canva, brand assets, and trend storage.

Related: [Integration audit](FINAL_INTEGRATION_AUDIT.md), [Architecture](../ARCHITECTURE.md).

## Stack

| Layer | Implementation |
| --- | --- |
| UI | React 19, Vite 7, routes in `frontend/src/App.tsx` |
| API | FastAPI `2.0.0`, prefix `/api/v1` |
| Auth | JWT HS256, cookie `access_token`, bcrypt, `sessions` table |
| Data | SQLAlchemy models in `db/models.py`, Alembic `001`–`006`, SQLite by default |
| MCP | In-process allowlist of 22 read tools in `backend/mcp/` |
| Studio planner | `DeepSeekCreativeClient` when `DEEPSEEK_API_KEY` is set, else `GroundedCreativeModel` |
| Scheduler planner | `ContentAgent` plus `LLM_PROVIDER` (default `openai`) |
| Images | OpenAI, or `MockImageGenerationProvider` |
| Vision | DeepSeek when the vision factory loads, else structural QA |
| Publisher | `InstagramAgent` only |
| Canva | Optional remote MCP, off unless `CANVA_ENABLED` |
| Scheduler | In-process loop, off unless `SCHEDULER_ENABLED` |

## Publishing

`POST /api/v1/instagram/publish` and `POST /api/v1/generation/{id}/approve` and the scheduler pipeline all reach Meta through the Instagram Agent tools in `tools/instagram_media.py`. MCP rejects publish tool names. Content orchestration returns `published: false`.

Only `PUBLISHED` with `instagram_media_id` counts. Unknown publish certainty is not retried.

## Trends

`TrendResearcher` fetches explicit URLs on an allowlist of Indian news and government hosts. `DeepSeekTrendAnalyst` writes a brief from stored evidence and is not a browser. Opportunities are recommendations. The trend tick does not publish by itself.

## Festivals

47 named entries in `festivals/india_festivals.py`, with year-keyed civil dates and `lunar_dates.json` overrides. Default campaign size is 2 posts. Timezone `Asia/Kolkata`.

## Not in this build

- DeepSeek image generation
- Reels, stories, carousels
- Instagram OAuth code flow
- A live `real_deepseek` test
- Semantic similarity beyond token overlap
- `LLMPlanner` as a production planner
