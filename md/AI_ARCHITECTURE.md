# AI architecture

Related: [Architecture](ARCHITECTURE.md), [DeepSeek](AI/DEEPSEEK.md), [OpenAI](AI/OPENAI.md), [Image generation](AI/IMAGE_GENERATION.md), [Content agent](AGENTS/CONTENT_AGENT.md).

## Split that the code implements

| Job | Implementation | Status |
| --- | --- | --- |
| Studio creative plan (`POST /api/v1/generation`) | `get_creative_model` in `ai/creative_model.py`. `LLM_PROVIDER=deepseek` and a non-empty `DEEPSEEK_API_KEY` → `DeepSeekCreativeClient`. Otherwise `GroundedCreativeModel` (local template, no HTTP) | Implemented |
| Scheduler content plan | `ContentAgent` + `select_reasoning_provider` in `ai/llm_client.py`. `LLM_PROVIDER` default `deepseek` | Implemented |
| Image pixels | `get_image_generation_provider` returns `OpenAIImageGenerationProvider` only for `openai` or an empty name. Studio references with bytes use `backend.ai.image.OpenAIImageProvider.edit` | Implemented |
| Vision QA | `DeepSeekVisionProvider` in `ai/vision/deepseek.py` when the factory loads. Scheduler `ImageQAService` passes structurally if no vision client is attached | Implemented |
| DeepSeek image generation | No image endpoint, no `images.generate` call | NOT IMPLEMENTED |
| OpenAI vision QA | Not wired. `VISION_PROVIDER` default is `deepseek` | NOT IMPLEMENTED |

`DeepSeek = reasoning` and `OpenAI = images`. `LLM_PROVIDER` defaults to `deepseek`. `IMAGE_PROVIDER` defaults to `openai`. An OpenAI key does not become the reasoning provider. Live `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` values are optional until a real call is requested. Startup logs the provider names and does not log keys.

## Provider selection in `create_app`

`select_reasoning_provider`:

- `LLM_PROVIDER=deepseek` (the default) and key plus `DEEPSEEK_MODEL` → `DeepSeekLLMProvider`.
- `LLM_PROVIDER=deepseek` without a key → `MockLLMProvider`. The process still starts. `get_llm_provider` still returns DeepSeek, and a live call raises `DEEPSEEK_CONFIGURATION_ERROR`.
- `LLM_PROVIDER=openai` and key plus `LLM_MODEL` → `OpenAILLMProvider`. This is explicit only.
- `LLM_PROVIDER=mock`, or an unknown name at startup → `MockLLMProvider`. `get_llm_provider` raises `OPENAI_CONFIGURATION_ERROR` for an unknown name.

`select_image_provider`:

- `IMAGE_PROVIDER=openai` and key plus an image model → `OpenAIImageGenerationProvider`.
- Missing key or model, `IMAGE_PROVIDER=mock`, or a non-image name such as `deepseek` → `MockImageGenerationProvider` at startup.
- `get_image_generation_provider` returns OpenAI for `openai`. `generate` without a key raises `OPENAI_CONFIGURATION_ERROR`. Any other live name, including `deepseek`, raises that configuration error and does not generate pixels.

## Structured output

OpenAI chat (`ai/openai_llm.py`) and DeepSeek text (`ai/llm/deepseek.py`) both request `response_format: {"type": "json_object"}` and validate into Pydantic models (`ContentPlan`, `CreativePlan`, `TrendBrief`). Invalid JSON is rejected and retried up to `LLM_MAX_ATTEMPTS` (default 3).

`GroundedCreativeModel.create_plan` builds a `CreativePlan` from MCP facts without a model call. `review_image` on that class only checks that Pillow can open the bytes and that both sides are at least 64 pixels.

## What models are not allowed to do

LLM and vision modules do not receive Meta tokens, do not register Instagram tools, and do not call `publish_instagram_media`. Creative-plan validation (`agent/creative_validation.py`) rejects plans that try to select a publish tool or carry credentials.

## Diagnostics

`GET /api/v1/ai/status` returns `Settings.public_ai_status()`: provider names, model names, and booleans for whether keys are configured. It does not return key material.
