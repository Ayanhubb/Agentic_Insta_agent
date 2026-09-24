# Content agent

Related: [Instagram agent](INSTAGRAM_AGENT.md), [Content generation](../CONTENT/CONTENT_GENERATION.md), [AI architecture](../AI_ARCHITECTURE.md).

Two classes share this name in conversation. They are different.

## `ContentOrchestrator` (`agent/content_orchestrator.py`)

Used by `POST /api/v1/generation` and regenerate.

Flow:

1. Require an authenticated `user_id`.
2. Call MCP: `get_business_profile` (required), products, offers, brand, festival, and Canva only when the request asks for it.
3. Ask the creative model for a `CreativePlan`.
4. If `canva_action` is `apply_template` or `use_reference`, export a Canva design for that user. Otherwise generate the image with OpenAI.
5. Run the same vision QA on those bytes, then store the image as `PENDING_APPROVAL`.
6. Return `published=False` with `handoff_to_instagram=False`.

A request that asks for Canva when the user is not connected returns `CANVA_NOT_CONNECTED` and does not call OpenAI. The scheduler `ContentAgent` uses the same choice when its plan sets `canva_action`. Canva never publishes.

The orchestrator does not call the Instagram Agent.

Plan retries are capped at 3 (`PLAN_ATTEMPT_CAP`). QA retries are capped at 3. Constructors default to 2 attempts, then clamp into that cap.

Creative model (`ai/creative_model.py`):

- `LLM_PROVIDER=deepseek` and `DEEPSEEK_API_KEY` set → `DeepSeekCreativeClient`
- otherwise → `GroundedCreativeModel`

An OpenAI key does not select the studio planner. DeepSeek planning is **IMPLEMENTED — NOT CONFIGURED**: a missing `DEEPSEEK_API_KEY` uses `GroundedCreativeModel` and does not crash startup. OpenAI drawing is **IMPLEMENTED — NOT CONFIGURED** the same way. Owned logo and product bytes, when uploaded, go MCP → OpenAI edit. Those production files are not uploaded yet. A missing logo is not invented.

## `ContentAgent` (`agent/content_agent.py`)

Used by the daily and festival schedulers (`scheduler/scheduler.py`).

`create_plan` / `run` call `LLMProvider.generate_content_plan`. The scheduler passes `select_reasoning_provider`. The default `LLM_PROVIDER` is `deepseek`: DeepSeek when the key is set, `MockLLMProvider` when that key is missing. `OpenAILLMProvider` is used only when `LLM_PROVIDER=openai` is set explicitly. Logo and product references use the same MCP resolver as the studio path before any OpenAI edit.

`DiversityPolicy` rejects repeated themes inside a session. Plan attempts default to 3.

`published` on the strategy result stays false. Publishing is a later step in `scheduler/pipeline.py`.

### Partial pieces

| Item | Status |
| --- | --- |
| `SemanticSimilarityBackend` | NOT IMPLEMENTED. `enabled` is false and enabling it raises |
| `SessionContextStore.get_festival_context` | PARTIAL. Always returns `None` |
| `SessionContextStore.save_content_task` | PARTIAL. Returns the task and does not persist it |

## Opportunity handoff

`OpportunityEngine.evaluate` (`agent/opportunity_engine.py`) builds a `ContentOrchestrationRequest` and an `OpportunityBridge` with `published=False`. It does not call `ContentAgent.run`. Carousel, reel, and story recommendations are labeled concept-only because the Instagram Agent publishes one still image.
