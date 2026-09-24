# AI Architecture

Backend-only OpenAI integration for Instagram Agentic AI.

The LLM supplies intelligence. The FastAPI backend keeps deterministic control.
The existing Instagram Agent remains the only publisher.

## OpenAI integration

The official OpenAI Python SDK runs inside the FastAPI process.

```text
React
  → FastAPI
    → Content Agent
      → LLMProvider
        → OpenAI chat completions
```

```text
Content Agent
  → ImageGenerationProvider
    → OpenAI image generation
      → Storage service
        → GeneratedImage record
```

The client is created with `OPENAI_API_KEY` from process environment. It is
never created in React, never stored in the database, and never returned by an
API. `NEXT_PUBLIC_*` and `VITE_*` variables are not used for this secret.

Model names are configuration, not code constants. Providers read
`settings.llm_model` and `settings.image_model`.

## Provider abstraction

```text
ai/
  __init__.py
  llm_client.py              LLMProvider + get_llm_provider()
  openai_llm.py              OpenAILLMProvider
  image_generator.py         ImageGenerationProvider + storage contract
  openai_image_generator.py  OpenAIImageGenerationProvider
  schemas.py                 ContentPlan, GeneratedImage, request models
```

| Interface | OpenAI implementation | Factory |
| --- | --- | --- |
| `LLMProvider` | `OpenAILLMProvider` | `get_llm_provider(settings)` |
| `ImageGenerationProvider` | `OpenAIImageGenerationProvider` | `get_image_generation_provider(settings)` |

The Content Agent and FastAPI depend on the interfaces. They must not import
OpenAI types in route handlers. A non-OpenAI implementation can be substituted
in tests or later providers without changing agent code.

`create_app()` attaches:

- `app.state.llm_provider`
- `app.state.image_provider`

Startup does not call OpenAI. A missing key only fails when generation is
requested (`OPENAI_CONFIGURATION_ERROR`). Instagram publishing still works
without OpenAI.

## Prompt flow

The backend, not the model, assembles context:

1. User prompt (optional)
2. Business profile (name, type, products, services, brand style, audience, location, language)
3. Festival context (name, date, year)
4. Recent content history (themes, types, prompts)
5. Current date and a reason hint (`user_prompt`, `daily_automation`, `festival_campaign`)

`OpenAILLMProvider.generate_content_plan()` sends that payload to the configured
chat model with JSON-object response formatting. The model must:

1. Understand the user prompt
2. Improve it into a detailed image-generation prompt
3. Ground the plan in the business profile
4. Use festival context when present (business-specific, not generic greeting art)
5. Avoid recent themes
6. Return schema-validated JSON

Validated output:

```json
{
  "content_type": "product_promotion",
  "theme": "festive_collection",
  "image_prompt": "...",
  "business_context": "...",
  "reason": "festival_campaign"
}
```

Unknown extra fields such as `command`, `token`, or `url` are ignored. They are
never executed. The provider does not register OpenAI tool calls, does not run
Python or shell, and does not call Instagram.

If the JSON is malformed or fails schema validation, the provider retries
within `LLM_MAX_ATTEMPTS` and then raises `OPENAI_INVALID_RESPONSE`.

## Image generation flow

1. Content Agent (or another backend caller) passes a validated `image_prompt`.
2. `OpenAIImageGenerationProvider` calls the configured OpenAI image model.
3. Image bytes are taken from `b64_json` or downloaded from the returned URL.
4. Pillow confirms the bytes are a readable JPEG or PNG.
5. `GeneratedImageStore.save()` writes
   `storage/generated/{user_id}/img_<uuid>.png` (path traversal is rejected).
6. The provider returns an internal `GeneratedImage` record
   (`GENERATED`, `approval_status=PENDING`).

The image provider does **not** publish to Instagram, create a media container,
or touch OAuth tokens. Publishing stays on the Instagram Agent after approval
or automation policy.

## Security boundary

| Rule | Enforcement |
| --- | --- |
| Key only on the backend | Read from `OPENAI_API_KEY`; never a frontend env prefix |
| Never store the key in the database | `GeneratedImage` stores provider/model names only |
| Never return the key through an API | `Settings.public_ai_status()` omits it; records have `public_dict()` |
| Never log the key | `services.logging` redacts `OPENAI_API_KEY`, `openai_api_key`, `api_key`, and `sk-` material |
| LLM cannot publish | No Instagram imports or publish methods on AI providers |
| LLM cannot bypass ToolRegistry | AI providers are not Instagram tools and are not in `ALLOWED_TOOLS` |
| LLM cannot execute code | Output is `json.loads` + Pydantic only |

## Configuration

From `.env` / `.env.example`:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Backend secret for the official SDK |
| `DEEPSEEK_API_KEY` | Backend secret for the official DeepSeek API |
| `DEEPSEEK_MODEL` | DeepSeek chat and vision model (default `deepseek-flash`) |
| `LLM_PROVIDER` | Provider id (`openai` or `deepseek`) |
| `LLM_MODEL` | Chat model name |
| `IMAGE_PROVIDER` | Image provider id (`openai`) |
| `IMAGE_MODEL` | Image model name |
| `LLM_MAX_ATTEMPTS` | Bounded LLM retries (default 3) |
| `IMAGE_MAX_ATTEMPTS` | Bounded image retries (default 3) |
| `IMAGE_SIZE` | Image size sent to the provider |
| `LLM_TEMPERATURE` | Chat temperature |
| `OPENAI_RETRY_DELAY_SECONDS` | Delay between bounded retries |

Do not put real keys in source, Dockerfiles, or the React app.

## Error handling

| Code | When |
| --- | --- |
| `OPENAI_CONFIGURATION_ERROR` | Missing API key, missing model, unsupported provider, or authentication failure |
| `OPENAI_API_ERROR` | OpenAI transport/status failure after bounded retries |
| `OPENAI_INVALID_RESPONSE` | Empty, non-JSON, schema-invalid, or unreadable image payload |
| `STORAGE_FAILURE` | Generated bytes could not be written |

Retries are capped by `LLM_MAX_ATTEMPTS` / `IMAGE_MAX_ATTEMPTS`. Configuration
errors and storage failures are not retried indefinitely. Rate limits and
timeouts are retried only inside that budget.

## Testing

Default `pytest` is mocked and never constructs `AsyncOpenAI`.

```bash
pytest
```

Coverage:

- Successful LLM generation
- Malformed LLM output
- OpenAI failure
- Missing API key
- Image generation failure
- Image storage failure
- Provider abstraction / factories
- Secret redaction and FastAPI wiring

Real OpenAI calls are opt-in only:

```bash
RUN_OPENAI_INTEGRATION=1 pytest -m real_openai
```

Requirements: `OPENAI_API_KEY` plus configured `LLM_MODEL` / `IMAGE_MODEL`.
Presence of a key in `.env` is not enough. `pytest.ini` uses
`-m "not real_openai and not real_deepseek"` so the default suite cannot spend API credits.

## DeepSeek integration

DeepSeek is a text and vision provider. It does not generate images and it does not publish.

```text
Content Agent
  → LLMProvider
    → DeepSeekLLMProvider
      → POST https://api.deepseek.com/chat/completions
```

```text
DeepSeekVisionProvider
  → same official chat API, model deepseek-flash
    → image understanding / image QA JSON
```

`LLM_PROVIDER=deepseek` selects `DeepSeekLLMProvider` from `get_llm_provider()`.
`VISION_PROVIDER=deepseek` selects `DeepSeekVisionProvider` from `get_vision_provider()`.
OpenAI remains the image-generation provider.

| Variable | Purpose |
| --- | --- |
| `DEEPSEEK_API_KEY` | Backend secret. Never returned, stored, or logged |
| `DEEPSEEK_MODEL` | Chat and vision model. Default `deepseek-flash` |
| `DEEPSEEK_BASE_URL` | Official root. Allowlist: `api.deepseek.com`, `localhost`, `127.0.0.1` |
| `DEEPSEEK_TIMEOUT_SECONDS` | Request timeout (default 30) |
| `VISION_PROVIDER` | Vision provider id (`deepseek`) |

Capabilities: text reasoning (thinking mode), structured JSON, creative planning, captions, hashtags, festival strategy, image understanding, and image QA.

Vision inputs:

| Input | How it is sent |
| --- | --- |
| Public HTTPS URL | Passed as `image_url`. No base64 |
| Authorized storage reference | Owner-scoped path under `media_root/{stage}/{user_id}/`. Read locally, then base64 |
| Local image | File must sit inside media, temp, or input storage and include that user's id. Then base64 |
| Base64 | Used only when the API cannot fetch the image, or when the caller has no URL or file |

Cross-tenant paths and private URLs (`localhost`, loopback, private IPs) are rejected before any API call.

| Code | When |
| --- | --- |
| `DEEPSEEK_CONFIGURATION_ERROR` | Missing API key, rejected base URL, or HTTP 401 |
| `DEEPSEEK_TIMEOUT` | Timeout. Retried within `LLM_MAX_ATTEMPTS` |
| `DEEPSEEK_RATE_LIMITED` | HTTP 429. Retried within the same budget |
| `DEEPSEEK_UNAVAILABLE` | HTTP 500/503 or connection failure for text |
| `VISION_PROVIDER_ERROR` | Vision provider down (HTTP 500/503 or connection failure) |
| `DEEPSEEK_INVALID_RESPONSE` | Empty, truncated, non-JSON, or schema-invalid output. Malformed JSON is retried |
| `DEEPSEEK_API_ERROR` | Rejected request (HTTP 400/422). Not retried |

HTTP 400, 401, 402, and 422 are not retried. The API key is omitted from `public_ai_status()`, error messages, and logs (`DEEPSEEK_API_KEY`, `deepseek_api_key`).

Default pytest mocks the DeepSeek HTTP client. Live calls are opt-in:

```bash
RUN_DEEPSEEK_INTEGRATION=1 pytest -m real_deepseek
```

## Agentic boundary

The LLM plans content. It does not:

- call Instagram
- execute shell commands
- execute arbitrary Python
- read database credentials
- read OAuth tokens
- bypass `ToolRegistry`
- publish content

The Content Agent (separate work) should call `LLMProvider` then
`ImageGenerationProvider`, persist the `GeneratedImage` record, and wait for
approval or automation policy. Only then may the Instagram Agent publish.

This layer does not implement the scheduler or React.
