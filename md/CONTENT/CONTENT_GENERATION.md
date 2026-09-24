# Content generation

Related: [Content agent](../AGENTS/CONTENT_AGENT.md), [Image generation](../AI/IMAGE_GENERATION.md), [Image QA](IMAGE_QA.md), [Approval in the scheduler](../AUTOMATION/SCHEDULER.md).

## Studio request

`POST /api/v1/generation` body (`GenerateBody`): `prompt` (minimum 3 characters), optional `product_id`, `offer_id`, `festival`, `use_canva`.

`ContentOrchestrator.run` requires a business profile with `business_name`. It loads MCP facts, plans, generates, runs QA, and returns:

`task`, `plan`, `creative_plan`, `image`, `generated_image`, `approval_status`, `published: false`.

Regenerate (`POST /api/v1/generation/{image_id}/regenerate`) runs the orchestrator again for an owned image. Reject sets `approval_status` to `REJECTED`. Approve (`POST .../approve`) is the publish handoff through `PublicationService`.

List and get are owner-scoped. A missing or foreign id is 404.

## Scheduler request

Daily and festival ticks call `ContentAgent`, then `CampaignPipeline`: content → optional Canva → image QA → `decide_approval` → publish only when `publish` is true.

## Offers

`GET /api/v1/offers` lists products that have offer text. `POST /api/v1/offers` creates one. MCP `get_active_offers` is a different set: approved promotion **images**, not the offer rows themselves.

## Canva during generation

Canva runs only when the request sets `use_canva` or the prompt asks for it, and a client exists. `DisabledCanva` returns action `none`. See [Canva](../MCP/CANVA_MCP.md).
