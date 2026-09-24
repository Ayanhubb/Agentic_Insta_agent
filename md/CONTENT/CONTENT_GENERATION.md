# Content generation

Related: [Content agent](../AGENTS/CONTENT_AGENT.md), [Image generation](../AI/IMAGE_GENERATION.md), [Image QA](IMAGE_QA.md), [Approval in the scheduler](../AUTOMATION/SCHEDULER.md).

## Studio request

`POST /api/v1/generation` body (`GenerateBody`): `prompt` (minimum 3 characters), optional `product_id`, `offer_id`, `festival`, `use_canva`.

`ContentOrchestrator.run` requires a business profile with `business_name`. DeepSeek plans when `DEEPSEEK_API_KEY` is set (**IMPLEMENTED — NOT CONFIGURED** otherwise: the local planner runs and startup still succeeds). It loads MCP facts, including a logo or product image when one has been uploaded. Those production files are not uploaded yet. OpenAI then generates or edits the image (**IMPLEMENTED — NOT CONFIGURED** while `OPENAI_API_KEY` is empty). QA runs, and the response includes:

`task`, `plan`, `creative_plan`, `image`, `generated_image`, `approval_status`, `published: false`.

Regenerate (`POST /api/v1/generation/{image_id}/regenerate`) runs the orchestrator again for an owned image. Reject sets `approval_status` to `REJECTED`. Approve (`POST .../approve`) is the publish handoff through `PublicationService`.

List and get are owner-scoped. A missing or foreign id is 404.

## Scheduler request

Daily and festival ticks call `ContentAgent`, then `CampaignPipeline`: content → optional Canva → image QA → `decide_approval` → publish only when `publish` is true.

## Offers

`GET /api/v1/offers` lists products that have offer text. `POST /api/v1/offers` creates one. MCP `get_active_offers` is a different set: approved promotion **images**, not the offer rows themselves.

## Canva during generation

Canva is queried only when the request sets `use_canva` or the prompt asks for it.

The creative plan then chooses the renderer:

- `canva_action: none` — OpenAI generates the image.
- `apply_template` or `use_reference` — Canva creates and exports a design from the connected account's own templates and assets.

That export uses the same QA and approval path as an OpenAI image. `published` stays false. If Canva was requested and the user has not connected it, the response is `CANVA_NOT_CONNECTED` and no image is generated. A missing Canva connection does not block startup or generations that did not ask for Canva. See [Canva](../MCP/CANVA_MCP.md).
