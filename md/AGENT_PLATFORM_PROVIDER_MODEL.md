# Provider model

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [AI architecture](AI_ARCHITECTURE.md), [DeepSeek](AI/DEEPSEEK.md), [OpenAI](AI/OPENAI.md), [Image generation](AI/IMAGE_GENERATION.md), [Runtime](AGENT_PLATFORM_RUNTIME.md).

DeepSeek and OpenAI are provider implementations. They are not the architecture. The runtime depends on roles and on the factories that already select an implementation from environment variables.

## Roles

**IMPLEMENTED** roles and the code that fills them.

| Role | Interface | Selection | Default implementation |
| --- | --- | --- | --- |
| Reasoning, scheduler plans and trend briefs | `LLMProvider.generate_content_plan` | `select_reasoning_provider` / `LLM_PROVIDER` | `deepseek` |
| Reasoning, studio creative plan | `get_creative_model` | `LLM_PROVIDER` plus whether `DEEPSEEK_API_KEY` is non-empty | `DeepSeekCreativeClient` or `GroundedCreativeModel` |
| Image pixels | `ImageGenerationProvider.generate` | `select_image_provider` / `IMAGE_PROVIDER` | `openai` |
| Image edit | `OpenAIImageProvider` in `backend/ai/image/` | Same OpenAI image configuration, when MCP loaded logo or product bytes | `openai` |
| Vision QA | `get_vision_provider` | `VISION_PROVIDER` | `deepseek` |
| Creative export | `CanvaAdapter` | `CANVA_ENABLED` and a user connection | off |

These are not one interface. `ContentPlan`, `CreativePlan`, and `TrendBrief` are different schemas. A platform registry keeps that split. It does not force studio planning through `generate_content_plan`.

Canva is an integration with its own OAuth tokens. It is not a model provider. It is bound as creative export. See [MCP](AGENT_PLATFORM_MCP.md).

## Reasoning selection

**IMPLEMENTED.** `ai/llm_client.py`.

- `LLM_PROVIDER=deepseek` (default) and key plus `DEEPSEEK_MODEL` → `DeepSeekLLMProvider` for the scheduler path.
- `LLM_PROVIDER=deepseek` without a key → `MockLLMProvider` at startup. The process still starts. `get_llm_provider` still returns DeepSeek, and a live call raises `DEEPSEEK_CONFIGURATION_ERROR`.
- Studio path without a key uses `GroundedCreativeModel`, which builds a `CreativePlan` from MCP facts and does not use HTTP. Its `review_image` only checks that Pillow can open the bytes and that both sides are at least 64 pixels.
- `LLM_PROVIDER=openai` and key plus `LLM_MODEL` → `OpenAILLMProvider`. Explicit opt-in only. An OpenAI key does not take over planning.
- `LLM_PROVIDER=mock`, or an unknown name at startup → `MockLLMProvider`. `get_llm_provider` raises `OPENAI_CONFIGURATION_ERROR` for an unknown name.

OpenAI chat and DeepSeek text both request `response_format: {"type": "json_object"}` and validate into Pydantic models. Invalid JSON is rejected and retried up to `LLM_MAX_ATTEMPTS` (default 3).

The trend stage uses this reasoning provider. It does not construct a separate DeepSeek HTTP client.

DeepSeek has no image endpoint. DeepSeek does not draw pixels. That is not a missing feature to add inside the image role.

## Image selection

**IMPLEMENTED.** `ai/image_generator.py` and `backend/ai/image/`.

- `IMAGE_PROVIDER=openai` and key plus an image model → `OpenAIImageGenerationProvider`.
- Missing key or model, `IMAGE_PROVIDER=mock`, or a non-image name such as `deepseek` → `MockImageGenerationProvider` at startup.
- `get_image_generation_provider` returns OpenAI for `openai`. `generate` without a key raises `OPENAI_CONFIGURATION_ERROR`. Any other live name, including `deepseek`, raises that configuration error and does not generate pixels.

Text prompts use generation. Logo and product bytes from MCP use the edit provider. A missing file does not become a generated logo.

`GeneratedImageStore` / `LocalGeneratedImageStore` writes under the media root for that `user_id`. The image provider stores pixels. It does not publish.

Both `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are **IMPLEMENTED — NOT CONFIGURED** while empty. Startup logs `LLM provider: deepseek` and `Image provider: openai` and does not log keys.

## Vision selection

**IMPLEMENTED.** `scheduler.integrations.resolve_vision` uses `ai.vision.deepseek.get_vision_provider`. `DeepSeekVisionProvider` runs when the key is set. Without it, studio QA is structural and the scheduler records `provider=structural`.

`VISION_PROVIDER` default is `deepseek`. OpenAI vision QA is not wired. It is not a product feature. The platform registry does not add an OpenAI vision implementation in this proposal.

## Registry

**IMPLEMENTED** facade in `ai/model_registry`. It reads the same functions. It does not change defaults. The contract is [Model registry](AGENT_PLATFORM_MODEL_REGISTRY.md).

```text
ProviderRegistry
  role reasoning.scheduler  → select_reasoning_provider(settings)
  role reasoning.studio     → get_creative_model(settings)
  role reasoning.trend      → the same object as reasoning.scheduler
  role image.generate       → select_image_provider(settings)
  role image.edit           → the OpenAI edit provider when the image role is openai and references exist
  role vision.qa            → get_vision_provider(settings)
```

Each entry exposes `name`, `model`, and `configured: bool`, which is what `Settings.public_ai_status()` already returns for `GET /api/v1/ai/status`.

**PROPOSED** rules for a new implementation:

1. Implement the existing abstract method (`generate_content_plan` or `generate`). Do not add a method that publishes or that accepts a token.
2. Select it with the existing env var (`LLM_PROVIDER` or `IMAGE_PROVIDER`). Unknown names keep today's error behavior.
3. Construction must not require a key and must not call the network. That is the current factory contract.
4. Structured output stays Pydantic validation on the server.
5. Logs include the provider name only.

Per-tenant API keys are not in the schema. This proposal does not add them. Every tenant in the process shares the configured provider keys. Isolation is on data (rows, files, Instagram tokens), not on separate vendor accounts.

## What providers must not receive

**IMPLEMENTED** and required of every future implementation:

- Meta access tokens, Instagram account tokens, Canva tokens, `JWT_SECRET`, `TOKEN_ENCRYPTION_KEY`.
- The six Instagram tool definitions.
- A callback that can call `publish_instagram_media`.
- Filesystem paths in tool results (`sanitize_payload` already strips them before MCP returns).

Creative-plan validation rejects plans that try to select a publish tool or carry credentials. Provider modules do not import the Instagram tools.

`GroundedCreativeModel` is the offline reasoning implementation for the studio. It stays in the registry under the studio role when the DeepSeek key is empty. The platform does not delete it in favor of a hard failure at startup. Startup must keep succeeding with empty keys.

## Image provider abstraction

The image role is "produce still-image bytes and store them for this user". Inputs that exist today:

- a prompt from a validated plan
- optional reference bytes resolved by the server from owned assets
- `user_id` for the storage path

Outputs that exist today: `GeneratedImage` / stored file metadata (`filename`, `storage_path`, `mime_type`, dimensions where the pipeline records them).

The abstraction does not include a destination social network. Publishing is a later workflow step on a different runtime.

Mock image generation is a real implementation used when the key or model is missing. Tests depend on it. The registry lists `mock` as an implementation name, not as an error.

## Binding from an agent

**PROPOSED.** `instagram_marketing` binds:

| Role | Binding |
| --- | --- |
| `reasoning.studio` | `LLM_PROVIDER` (default deepseek) |
| `reasoning.scheduler` | same |
| `reasoning.trend` | same |
| `image.generate` and `image.edit` | `IMAGE_PROVIDER` (default openai) |
| `vision.qa` | `VISION_PROVIDER` (default deepseek) |
| `creative.export` | Canva when enabled for that user |

A future agent uses the same roles or a subset. It does not embed `import openai` in a workflow step. If it needs a new modality the tree does not have (video, speech), that modality is out of scope until an interface exists. This proposal does not create one.
