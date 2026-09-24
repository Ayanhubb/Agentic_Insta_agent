# Content agent

Related: [Instagram agent](INSTAGRAM_AGENT.md), [Content generation](../CONTENT/CONTENT_GENERATION.md), [AI architecture](../AI_ARCHITECTURE.md).

Two classes share this name in conversation. They are different.

## `ContentOrchestrator` (`agent/content_orchestrator.py`)

Used by `POST /api/v1/generation` and regenerate.

Flow:

1. Require an authenticated `user_id`.
2. Call MCP: `get_business_profile` (required), products, offers, brand, festival, and Canva only when the request asks for it.
3. Ask the creative model for a `CreativePlan`.
4. Generate an image, then run QA.
5. Store the image and return `published=False` with `handoff_to_instagram=False`.

The orchestrator does not call the Instagram Agent.

Plan retries are capped at 3 (`PLAN_ATTEMPT_CAP`). QA retries are capped at 3. Constructors default to 2 attempts, then clamp into that cap.

Creative model (`ai/creative_model.py`):

- `DEEPSEEK_API_KEY` set → `DeepSeekCreativeClient`
- otherwise → `GroundedCreativeModel`

## `ContentAgent` (`agent/content_agent.py`)

Used by the daily and festival schedulers (`scheduler/scheduler.py`).

`create_plan` / `run` call `LLMProvider.generate_content_plan`. The provider is `OpenAILLMProvider` or `DeepSeekLLMProvider` according to `LLM_PROVIDER`, or `MockLLMProvider` when that provider is not configured (`api/app.py`).

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
