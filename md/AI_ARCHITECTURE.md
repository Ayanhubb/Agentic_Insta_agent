# AI architecture

Related: [Architecture](ARCHITECTURE.md), [DeepSeek](AI/DEEPSEEK.md), [OpenAI](AI/OPENAI.md), [Image generation](AI/IMAGE_GENERATION.md), [Content agent](AGENTS/CONTENT_AGENT.md).

## Split that the code implements

| Job | Implementation | Status |
| --- | --- | --- |
| Studio creative plan (`POST /api/v1/generation`) | `get_creative_model` in `ai/creative_model.py`. Non-empty `DEEPSEEK_API_KEY` → `DeepSeekCreativeClient`. Empty key → `GroundedCreativeModel` (local template, no HTTP) | Implemented |
| Scheduler content plan | `ContentAgent` + `get_llm_provider` in `ai/llm_client.py`. `LLM_PROVIDER` default `openai` | Implemented |
| Image pixels | `get_image_generation_provider` returns `OpenAIImageGenerationProvider` only for `openai` or an empty name. Studio references with bytes use `backend.ai.image.OpenAIImageProvider.edit` | Implemented |
| Vision QA | `DeepSeekVisionProvider` in `ai/vision/deepseek.py` when the factory loads. Scheduler `ImageQAService` passes structurally if no vision client is attached | Implemented |
| DeepSeek image generation | No image endpoint, no `images.generate` call | NOT IMPLEMENTED |
| OpenAI vision QA | Not wired. `VISION_PROVIDER` default is `deepseek` | NOT IMPLEMENTED |

`DeepSeek = reasoning` and `OpenAI = images` is true for the studio path when a DeepSeek key is set. It is not true for the scheduler while `LLM_PROVIDER` stays `openai`, which is the default in `.env.example` and `config.py`.

## Provider selection in `create_app`

`api/app.py` `_select_llm`:

- `llm_provider == "deepseek"` and key plus `deepseek_model` → `get_llm_provider` → `DeepSeekLLMProvider`.
- Otherwise if OpenAI key and `llm_model` are set → `OpenAILLMProvider`.
- Otherwise → `MockLLMProvider`.

Images:

- OpenAI key and (`image_model` or `openai_image_model`) → `get_image_generation_provider`.
- Otherwise → `MockImageGenerationProvider`.

An unsupported `LLM_PROVIDER` or `IMAGE_PROVIDER` raises `OPENAI_CONFIGURATION_ERROR` (HTTP 503) from the factories. The app startup path avoids that for the LLM by falling back to the mock when the selected provider is not fully configured.

## Structured output

OpenAI chat (`ai/openai_llm.py`) and DeepSeek text (`ai/llm/deepseek.py`) both request `response_format: {"type": "json_object"}` and validate into Pydantic models (`ContentPlan`, `CreativePlan`, `TrendBrief`). Invalid JSON is rejected and retried up to `LLM_MAX_ATTEMPTS` (default 3).

`GroundedCreativeModel.create_plan` builds a `CreativePlan` from MCP facts without a model call. `review_image` on that class only checks that Pillow can open the bytes and that both sides are at least 64 pixels.

## What models are not allowed to do

LLM and vision modules do not receive Meta tokens, do not register Instagram tools, and do not call `publish_instagram_media`. Creative-plan validation (`agent/creative_validation.py`) rejects plans that try to select a publish tool or carry credentials.

## Diagnostics

`GET /api/v1/ai/status` returns `Settings.public_ai_status()`: provider names, model names, and booleans for whether keys are configured. It does not return key material.
