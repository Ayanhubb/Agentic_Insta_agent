# AI architecture

Related: [Architecture](ARCHITECTURE.md), [DeepSeek](AI/DEEPSEEK.md), [OpenAI](AI/OPENAI.md), [Image generation](AI/IMAGE_GENERATION.md), [Content agent](AGENTS/CONTENT_AGENT.md).

## Split that the code implements

| Job | Implementation | Status |
| --- | --- | --- |
| Studio creative plan (`POST /api/v1/generation`) | `get_creative_model` in `ai/creative_model.py`. `LLM_PROVIDER=deepseek` and a non-empty `DEEPSEEK_API_KEY` → `DeepSeekCreativeClient`. Otherwise `GroundedCreativeModel` (local template, no HTTP) | **IMPLEMENTED — NOT CONFIGURED** |
| Scheduler content plan and trend briefs | `ContentAgent` and `DeepSeekTrendAnalyst` use `select_reasoning_provider` / `get_llm_provider`. `LLM_PROVIDER` default is `deepseek` | **IMPLEMENTED — NOT CONFIGURED** |
| Image pixels | `IMAGE_PROVIDER=openai`. Text prompts use `OpenAIImageGenerationProvider`. Logo and product bytes from MCP use `OpenAIImageProvider` edit | **IMPLEMENTED — NOT CONFIGURED** |
| Vision QA | `DeepSeekVisionProvider` when the key is set. Without it, studio QA is structural and the scheduler records `provider=structural` | **IMPLEMENTED — NOT CONFIGURED** |
| DeepSeek image generation | No image endpoint. DeepSeek does not draw pixels | Not a product feature |
| OpenAI vision QA | Not wired. `VISION_PROVIDER` default is `deepseek` | Not a product feature |

`DeepSeek = reasoning, trend analysis, and content planning.` `OpenAI = professional image generation and editing.` `LLM_PROVIDER` defaults to `deepseek`. `IMAGE_PROVIDER` defaults to `openai`. An OpenAI key does not become the reasoning provider. `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are **IMPLEMENTED — NOT CONFIGURED**. Startup logs the provider names, does not log keys, and does not fail. A live call without a key returns a configuration error. Production logos and product images are not uploaded yet. When a file is later uploaded, MCP resolves it and the OpenAI edit path receives the bytes. A missing file does not become a fake company logo.

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
