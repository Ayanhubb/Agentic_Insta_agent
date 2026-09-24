# OpenAI

Related: [Image generation](IMAGE_GENERATION.md), [DeepSeek](DEEPSEEK.md), [AI architecture](../AI_ARCHITECTURE.md).

OpenAI is the professional image generation and editing provider. Status: **IMPLEMENTED — NOT CONFIGURED.** `OPENAI_API_KEY` is empty. It is not the default reasoning provider. It does not publish to Instagram and it is not the vision QA provider. An `OPENAI_API_KEY` does not move content planning, studio planning, or trend analysis onto OpenAI.

## Environment

| Variable | Default | Use |
| --- | --- | --- |
| `OPENAI_API_KEY` | empty | Backend only. Never `VITE_*` |
| `LLM_PROVIDER` | `deepseek` | Reasoning. `openai` selects `OpenAILLMProvider` only when set explicitly |
| `LLM_MODEL` | empty | Required for a live chat call. `.env.example` uses `<configured-model>` |
| `LLM_TEMPERATURE` | `0.4` | Chat sampling |
| `LLM_MAX_ATTEMPTS` | `3` | Retries |
| `OPENAI_RETRY_DELAY_SECONDS` | `0.4` | Delay between retries |
| `IMAGE_PROVIDER` | `openai` | Only `openai` (or empty) is accepted |
| `IMAGE_MODEL` / `OPENAI_IMAGE_MODEL` | empty | Image model. No code default. `.env.example` sets `OPENAI_IMAGE_MODEL=gpt-image-2.5-sunburst` |
| `IMAGE_SIZE` / `OPENAI_IMAGE_SIZE` | `1024x1024` | Size passed to the image API |
| `OPENAI_IMAGE_QUALITY` | empty | Optional quality argument |
| `OPENAI_IMAGE_OUTPUT_FORMAT` | empty | Optional output format |
| `IMAGE_MAX_ATTEMPTS` | `3` | Image retries |
| `REQUEST_TIMEOUT_SECONDS` | `30` | Client timeout |

Status is **IMPLEMENTED — NOT CONFIGURED**, not a failure. Startup does not require `OPENAI_API_KEY`. When `IMAGE_PROVIDER=openai` but the key or image model is empty, startup uses `MockImageGenerationProvider` and does not crash. `OpenAIImageGenerationProvider.generate` and the edit provider then raise `OPENAI_CONFIGURATION_ERROR` (HTTP 503).

`LLM_PROVIDER=openai` with a key and `LLM_MODEL` still selects `OpenAILLMProvider`. That path is opt-in. The default reasoning provider is DeepSeek.

## Chat (`ai/openai_llm.py`)

`OpenAILLMProvider.generate_content_plan` calls `client.chat.completions.create` with JSON object response format and validates a `ContentPlan`.

Retries: up to `llm_max_attempts`, including a retry when the theme repeats. Rate limits and timeouts are retryable.

The system text tells the model not to include publish instructions. The class has no tools.

This chat client is what `ContentAgent` uses only when `LLM_PROVIDER=openai`. The default scheduler and studio paths use DeepSeek, or a local mock / `GroundedCreativeModel` when the DeepSeek key is absent. See [AI architecture](../AI_ARCHITECTURE.md).

## Images

See [Image generation](IMAGE_GENERATION.md). Two classes exist:

- `OpenAIImageGenerationProvider` (`ai/openai_image_generator.py`) — `images.generate` from a text prompt when no reference file was loaded.
- `OpenAIImageProvider` (`backend/ai/image/openai.py`) — generation and edit. When MCP has marked a company logo or product image `AVAILABLE`, `submit_creative_image` sends those bytes on the edit call. Production logos and product images are not uploaded yet, so this path currently has no files to attach. A missing asset is not drawn and then stored as the company logo.

## Tests

`tests/test_openai_llm.py` and `tests/test_openai_image.py` mock the SDK. `tests/test_real_openai.py` is marked `real_openai` and is excluded from the default pytest run.
