# Architecture

Instagram Agentic AI is a multi-user still-image studio. React talks only to FastAPI. FastAPI owns authentication, tenant scope, the Content Agent, MCP context, provider calls, local storage, approval, and the Instagram Agent.

The Instagram Agent is the only publisher. MCP, DeepSeek, OpenAI, and Canva never publish to Meta.

```text
React
  → FastAPI
    → Content Agent
      → MCP (in-process, tenant-scoped)
        → DeepSeek          creative plan
          → OpenAI Image    still image
            → DeepSeek Vision  QA
              → Approval
                → Instagram Agent
                  → Meta Graph
```

Independent MCP reads run together: festival, business, brand, offers, content rules, and logo. Festival details and a named product are fetched after that bundle. DeepSeek runs only after the context exists.

V1 upload publishing stays on the same deterministic path and still requires a logged-in user:

```text
POST /api/v1/instagram/publish
  → validate_image
    → prepare_image
      → upload_image (public JPEG URL)
        → create_instagram_media
          → publish_instagram_media
            → verify_publication
```

The multipart contract and JSON result shape are unchanged. Caption is optional (max 2200).

## Who may publish

| Component | May call Meta `media_publish` |
| --- | --- |
| Instagram Agent (`agent/agent.py`, six allowlisted tools) | yes |
| Content Agent, Content Orchestrator | no |
| MCP tools | no |
| DeepSeek (plan and vision) | no |
| OpenAI (image) | no |
| Canva | no |
| React | no |

After a publish timeout or an unknown `media_publish` result, recovery is `VERIFY_FIRST`. The same task is not published again. `AMBIGUOUS_PUBLICATION` does not count and is not retried.

## Runtime pieces

| Layer | Location | Role |
| --- | --- | --- |
| React studio | `frontend/` | Auth, generate, approve, brand, products, assets, campaigns, AI/MCP status, Instagram |
| FastAPI | `api/app.py`, `api/platform_routes.py`, `api/routes.py`, `api/asset_routes.py`, `api/canva_routes.py`, `api/auth_routes.py` | HTTP API |
| Auth | `auth/` | bcrypt, JWT + httpOnly cookie, revocable sessions, admin gate |
| Content Agent | `agent/content_agent.py` | Plans and generates. Never publishes. Product plans without a name take the first catalog product. |
| MCP | `backend/mcp/` | Allowlisted in-process tools. Tenant id comes from the authenticated user or scheduler, never from the model. |
| DeepSeek | `ai/llm/deepseek.py`, `ai/vision/deepseek.py` | Plans, captions, festival strategy, image QA. Missing key returns no live provider. |
| OpenAI image | `ai/openai_image_generator.py`, `backend/ai/image/` | Still images. Missing key uses mocks. |
| Canva | `backend/integrations/canva/` | Optional. Official remote MCP `https://mcp.canva.com/mcp`. Disabled by default. |
| Festivals | `festivals/` | India calendar, regional filter, lunar dates from data. DeepSeek is not the date source. |
| QA + approval | `scheduler/image_qa.py`, `scheduler/approval_policy.py`, `scheduler/pipeline.py` | QA persisted on `generated_images`. Failed QA cannot auto-publish. |
| Storage | `services/media_storage.py`, `services/asset_paths.py` | Owner-scoped generated files and brand/product assets |
| Publication gateway | `services/publication.py` | Sole enqueue into the Instagram Agent |
| Instagram Agent | `agent/agent.py` | validate → prepare → upload → create → publish → verify |
| Scheduler | `scheduler/` | Daily + festival ticks. Default timezone `Asia/Kolkata`. One uvicorn worker when enabled. |
| Database | `db/` | SQLAlchemy. SQLite default. Alembic `001`–`004`. |

## Providers

Server environment only. `public_ai_status()` and `GET /api/v1/ai/status` return flags, never keys.

| Variable | Role |
| --- | --- |
| `LLM_PROVIDER` | `deepseek` or `openai` |
| `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` | Chat and vision. Default model `deepseek-flash` |
| `VISION_PROVIDER` | `deepseek` |
| `OPENAI_API_KEY`, `OPENAI_IMAGE_MODEL`, `IMAGE_PROVIDER` | Image generation. Default model `gpt-image-2.5-sunburst` |

Default pytest mocks every provider. `real_openai` and `real_deepseek` are excluded unless opted in.

## Control vs intelligence

DeepSeek returns a structured plan. FastAPI still decides whether the plan is allowed, whether the image is stored, whether QA passed, whether a human must approve, and whether the Instagram Agent may run.

User-prompt content is never auto-published. Automatic daily or festival publish requires every approval gate: QA passed, ownership, a catalog product when the plan is a product post, a logo only when the plan requires one, a verifiable offer, and a catalog festival date.

## Publication counting

Only `status = PUBLISHED` with a verified `instagram_media_id` increments dashboard, daily, and festival counts. A daily post counts on its `scheduled_date` (the automation day), not on a different clock date of `published_at`.

| Outcome | Counts as published | Same-day retry |
| --- | --- | --- |
| `PUBLISHED` | yes | no (daily) / next campaign slot (festival) |
| `FAILED` | no | yes |
| `AMBIGUOUS_PUBLICATION` | no | no (do not publish again) |

## User isolation

Every product row carries `user_id`. Repositories load with `get_owned(user_id, id)`. A user cannot read another user's logo, product, campaign, generated image, Canva connection, or Instagram account. Missing and foreign ids are 404.

MCP product and logo tools still read the business profile and generated-image records. A product that exists only in the `products` table is not automatically that MCP product until it is also on the profile.

## Process model

Use a single uvicorn worker when `SCHEDULER_ENABLED=true`. SQLite uses WAL and a busy timeout so the scheduler session can commit before the Instagram Agent opens its own connection.

Fresh databases from `init_db` match current models. Existing databases need Alembic through `004_generated_image_qa` (`qa_status`, `qa_json`, plus brand, product, and Canva tables from `002` and `003`).

## Related documents

- [brain.md](brain.md)
- [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md)
- [CONTENT_AGENT.md](CONTENT_AGENT.md)
- [MEDIA_STORAGE.md](MEDIA_STORAGE.md)
- [SCHEDULER.md](SCHEDULER.md)
- [FESTIVAL_AUTOMATION.md](FESTIVAL_AUTOMATION.md)
- [INSTAGRAM_AGENT.md](INSTAGRAM_AGENT.md)
- [FRONTEND.md](FRONTEND.md)
- [DATABASE.md](DATABASE.md)
- [AUTHENTICATION.md](AUTHENTICATION.md)
- [CANVA.md](CANVA.md)
