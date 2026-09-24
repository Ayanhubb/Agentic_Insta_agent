# Final integration audit

Date: 2026-09-24. Ratings are `PASS`, `PARTIAL`, `FAIL`, or `NOT IMPLEMENTED`. Subjective scores are not used.

Related: [Architecture](../ARCHITECTURE.md), [AI architecture](../AI_ARCHITECTURE.md).

## Summary

| Area | Rating |
| --- | --- |
| Instagram Agent is the only Meta publisher | PASS |
| MCP cannot publish | PASS |
| DeepSeek cannot publish | PASS |
| OpenAI cannot publish | PASS |
| Canva cannot publish | PASS |
| OpenAI image generation | PASS |
| DeepSeek text reasoning | PARTIAL |
| DeepSeek vision QA | PARTIAL |
| Image bytes as references | PARTIAL |
| Account intelligence metrics | PARTIAL |
| Trend research with evidence | PASS |
| Trend-only auto publish | PASS (does not happen) |
| India festival catalog and campaigns | PASS |
| Coverage-only festival names | NOT IMPLEMENTED as scheduled festivals |
| Auth, bcrypt, cookie sessions | PASS |
| Fernet for Meta and Canva tokens | PASS |
| Tenant-scoped MCP and files | PASS |
| Global Meta token fallback | PARTIAL |
| Scheduler daily, festival, trend | PASS |
| Database tables and Alembic 001–006 | PASS |
| FastAPI route surface | PARTIAL |
| Frontend routes | PASS |
| Canva optional integration | PARTIAL |
| Default pytest | See [documentation audit](FINAL_DOCUMENTATION_AUDIT.md) |
| Live DeepSeek tests | NOT IMPLEMENTED |
| Live OpenAI tests | PASS as an opt-in file (`tests/test_real_openai.py`), excluded from default pytest |

## PARTIAL and NOT IMPLEMENTED detail

### DeepSeek text reasoning — PARTIAL

Current: studio generation uses DeepSeek only when `DEEPSEEK_API_KEY` is set (`ai/creative_model.py`). Otherwise `GroundedCreativeModel` plans locally. The scheduler uses `LLM_PROVIDER`, which defaults to `openai` (`config.py`, `.env.example`).

Expected by the product split: DeepSeek plans, OpenAI draws.

Files: `ai/creative_model.py`, `ai/llm_client.py`, `api/app.py`, `scheduler/scheduler.py`.

Remaining: set `LLM_PROVIDER=deepseek` for the scheduler if that split is required, or change the default. This audit does not change that code.

### DeepSeek vision QA — PARTIAL

Current: `DeepSeekVisionProvider` reviews images when the factory loads. `ImageQAService` records a structural pass when no vision client is attached. `GroundedCreativeModel.review_image` only checks that Pillow can open the file and that both sides are at least 64 pixels.

Expected: a vision review of brand and product fidelity before auto-publish.

Files: `ai/vision/deepseek.py`, `scheduler/image_qa.py`, `ai/creative_model.py`.

Remaining: configure `DEEPSEEK_API_KEY` so the vision factory succeeds. Structural pass remains the fallback.

### Image reference grounding — PARTIAL

Current: `OpenAIImageProvider.edit` sends file bytes. `OpenAIImageGenerationProvider.generate` puts asset labels in the text prompt only.

Expected: logo and product photos ground the image when they exist.

Files: `backend/ai/image/openai.py`, `ai/openai_image_generator.py`, `services/image_reference.py`.

Remaining: keep the edit path on every generation that has references. The text-only provider must not be described as seeing pixels.

### Instagram metrics — PARTIAL

Current: profile, media, and the insight names in [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md) are requested. Omitted or rejected fields are stored as unavailable. `media_snapshots.shares` is not requested from Graph.

Expected: only real Meta fields, which is what the reader does. Shares as a live metric is not implemented.

Files: `services/instagram_reader.py`, `db/models.py`.

Remaining: add `shares` to the insight request only if Meta documents it for these media types. Do not fill the column from a guess.

### FastAPI surface — PARTIAL

Current: `GET /api/v1/admin/users` is declared twice. The auth router is mounted first and is the one that runs (`list_all`). `api/instagram_account_routes.py` is never included.

Expected: one handler per path.

Files: `api/auth_routes.py`, `api/platform_routes.py`, `api/app.py`, `api/instagram_account_routes.py`.

Remaining: delete or stop mounting the duplicate. Not done in this documentation task.

### Global Meta token — PARTIAL

Current: `META_ACCESS_TOKEN` and `INSTAGRAM_ACCOUNT_ID` can publish when the user has no `instagram_accounts` row.

Expected: each tenant uses only their encrypted token.

Files: `config.py`, `services/instagram_client.py`.

Remaining: ignore the process token when a user id is present and no account is connected, if strict isolation is required.

### Canva — PARTIAL

Current: OAuth, encrypted tokens, and a catalog of remote design tools exist. `CanvaAdapter.apply` returns `applied: false` unless a capability was explicitly selected. Disabled by default.

Expected: optional design help, no Instagram publish. The no-publish rule is met. Automatic apply during generation is not fully wired.

Files: `backend/integrations/canva/adapter.py`, `backend/integrations/canva/catalog.py`.

Remaining: call a discovered capability from the orchestrator when the user asked for Canva and the account is connected. Publishing from Canva should stay absent.

### Content agent extras — NOT IMPLEMENTED

`SemanticSimilarityBackend` raises if enabled. `LLMPlanner` is a stub. `SessionContextStore.get_festival_context` returns `None`. `SessionContextStore.save_content_task` does not persist.

Files: `agent/content_agent.py`, `agent/planner.py`.

### Live DeepSeek test — NOT IMPLEMENTED

Marker `real_deepseek` is in `pytest.ini`. No test uses it.

### Instagram OAuth — NOT IMPLEMENTED

Connect accepts an access token. There is no Meta OAuth redirect for Instagram.

### DeepSeek image generation — NOT IMPLEMENTED

No image endpoint. This is intentional in the current code. Do not document it as available.

## FAIL

No integration was rated FAIL. The publisher boundary holds, and the partial items above are present but narrower than a single-sentence architecture slogan.
