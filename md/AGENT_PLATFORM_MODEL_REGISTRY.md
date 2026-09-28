# Model registry

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Provider model](AGENT_PLATFORM_PROVIDER_MODEL.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Security](AGENT_PLATFORM_SECURITY.md), [AI architecture](AI_ARCHITECTURE.md).

**IMPLEMENTED.** `ai/model_registry`.

The registry is a facade. `LLMProvider`, `ImageGenerationProvider`, and the factories in `ai/llm_client.py`, `ai/image_generator.py`, `ai/creative_model.py`, `ai/vision/deepseek.py`, and `backend/ai/image/factory.py` stay the constructors. The registry does not replace them and it does not publish to Meta.

```text
Agent
  ↓
ModelBinding          role + implementation name + optional model id
  ↓
ProviderRegistry      private Settings copy, then the existing factory
  ↓
DeepSeek / OpenAI / mock / future provider
```

## Credentials

Keys stay on process `Settings`, loaded from the environment.

| Rule | Where it is enforced |
| --- | --- |
| Backend-only keys | `DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are read by the existing providers. The registry has no key parameter |
| No secrets in the database | No binding table is added. `ModelBinding` has `role`, `implementation`, and `model` |
| No secrets in React | This module is not imported by the frontend. `GET /api/v1/ai/status` is unchanged |
| No `VITE_*` or `NEXT_PUBLIC_*` secrets | The registry does not read or define those names |

`ModelBinding` rejects extra fields and rejects values that contain `api_key`, `sk-`, `secret`, `token`, `password`, `credential`, `authorization`, or `bearer`. A published status dict is `ProviderStatus.public_dict()`. It lists the role, the implementation name, the model id, availability, `configured`, and timeouts. It does not list keys.

Per-tenant provider keys stay rejected. Every tenant in the process shares the configured keys. Isolation stays on rows, files, and Instagram tokens.

## Interfaces

| Interface | Module | Live implementation |
| --- | --- | --- |
| `LLMProvider` | `ai.llm_client.LLMProvider` | `DeepSeekLLMProvider`, `OpenAILLMProvider`, `MockLLMProvider` |
| `ImageGenerationProvider` | `ai.image_generator.ImageGenerationProvider` | `OpenAIImageGenerationProvider`, `MockImageGenerationProvider` |
| `VisionProvider` | `ai.model_registry.interfaces.VisionProvider` | `DeepSeekVisionProvider` |
| `EmbeddingProvider` | `ai.model_registry.interfaces.EmbeddingProvider` | None. Interface only |
| `ModerationProvider` | `ai.model_registry.interfaces.ModerationProvider` | None. Interface only |

`VisionProvider` is the protocol `DeepSeekVisionProvider` already satisfies: `understand_image`, `answer_image_question`, `review_image`. OpenAI vision is not added.

`EmbeddingProvider.embed` and `ModerationProvider.moderate` cannot be constructed. Resolving either role raises `CONFIGURATION_ERROR` with `reason=interface_only`. `describe` reports `availability=unavailable` and does not call a vendor.

Image edit is the existing `OpenAIImageProvider` (`ImageProvider.edit` in `backend/ai/image/`). It is not a second image interface. Text-to-image stays `ImageGenerationProvider.generate`.

## Roles

`ProviderRole` is the closed set the registry accepts.

| Role | Factory | Default for `instagram_marketing` |
| --- | --- | --- |
| `reasoning.scheduler` | `select_reasoning_provider` / `get_llm_provider` | `LLM_PROVIDER`, default `deepseek` |
| `reasoning.trend` | Same object as `reasoning.scheduler` for the same implementation and model | same |
| `reasoning.studio` | `get_creative_model` | `deepseek` when the DeepSeek key is set, otherwise `GroundedCreativeModel` |
| `image.generate` | `select_image_provider` / `get_image_generation_provider` | `IMAGE_PROVIDER`, default `openai` |
| `image.edit` | `get_openai_image_provider` | `openai` when the OpenAI image key and model are set |
| `vision.qa` | `get_vision_provider` | `VISION_PROVIDER`, default `deepseek` |
| `embedding` | none | unbound |
| `moderation` | none | unbound |

`creative.export` is Canva. It is not a `ProviderRole`. Asking the registry for it raises `CONFIGURATION_ERROR`.

`instagram_marketing_model(settings)` builds the six live roles from `LLM_PROVIDER`, `IMAGE_PROVIDER`, and `VISION_PROVIDER` and from the configured model ids. It copies names only.

## Resolution

`ProviderRegistry.resolve(agent, role, live=False)` uses the startup factory (`select_reasoning_provider`, `select_image_provider`, `get_creative_model`, `get_vision_provider`). `live=True` uses `get_llm_provider` and `get_image_generation_provider`. Construction still does not require a key and does not call the network. `require_available=True` raises when availability is `unavailable`. A `mock` result is allowed, because tests and empty-key startup depend on it.

The registry applies the binding with `Settings.model_copy`. The copy sets `llm_provider`, `image_provider`, or `vision_provider`, and the model id when the binding has one. The copy is not logged, stored, or returned. The factory then runs unchanged:

- An unknown reasoning name still raises `OPENAI_CONFIGURATION_ERROR` from `get_llm_provider` on a live resolve. Startup selection still returns `MockLLMProvider`.
- `image.generate` bound to `deepseek` does not draw pixels. Startup selection returns `MockImageGenerationProvider`. A live resolve raises `OPENAI_CONFIGURATION_ERROR`.
- `image.edit` bound to anything other than `openai` does not call DeepSeek. A live resolve raises `OPENAI_CONFIGURATION_ERROR`. There is no mock edit client; startup reports `unavailable` and returns no provider. `mock` reports availability `mock` and returns no provider.
- `vision.qa` bound to anything other than `deepseek` raises `DEEPSEEK_CONFIGURATION_ERROR` on a live resolve. Startup reports `unavailable` and returns no provider. A missing DeepSeek key returns no provider and does not fall through to OpenAI.
- `reasoning.studio` follows `get_creative_model`. OpenAI is not a studio planner. A missing DeepSeek key stays `GroundedCreativeModel`.

An agent binding to `deepseek` for reasoning uses DeepSeek even when `OPENAI_API_KEY` is set and even when the process `LLM_PROVIDER` is `openai`. An agent binding to `openai` for images uses OpenAI even when `DEEPSEEK_API_KEY` is empty. OpenAI reasoning runs only when that role's binding is `openai` and the OpenAI key and `LLM_MODEL` are set.

A role missing from the agent raises `CONFIGURATION_ERROR` (`The agent is not bound to this provider role.`). The registry does not fill the gap with the other vendor.

## Availability

`ProviderAvailability` is `available`, `unavailable`, or `mock`.

| State | Meaning |
| --- | --- |
| `available` | The named vendor object was constructed and its key and model satisfy the factory rule |
| `mock` | `MockLLMProvider`, `MockImageGenerationProvider`, or `GroundedCreativeModel` |
| `unavailable` | The named provider cannot serve a live call. Vision or edit with no client. A live vendor object whose key or model is missing |

`configured` is the vendor-key boolean. It is false for mock and grounded. Empty keys stay **IMPLEMENTED — NOT CONFIGURED**. The process still starts.

`describe` and `describe_all` are the non-raising views. They use the startup factories.

## Errors

Registry failures are `AppError`. Messages and `details` carry the role, the implementation name, and the availability. They do not carry keys, prompts, or tokens.

| Situation | Code |
| --- | --- |
| Role missing, unknown role, interface-only capability, unsupported operation shape | `CONFIGURATION_ERROR` |
| Reasoning bound to DeepSeek but not available | `DEEPSEEK_CONFIGURATION_ERROR` |
| Reasoning bound to OpenAI but not available | `OPENAI_CONFIGURATION_ERROR` |
| Image role not available, or a non-image name on a live image resolve | `OPENAI_CONFIGURATION_ERROR` |
| Vision bound to DeepSeek but not available | `DEEPSEEK_CONFIGURATION_ERROR` |
| Vision bound to another name | `VISION_PROVIDER_ERROR` or the factory's `DEEPSEEK_CONFIGURATION_ERROR` |
| Call exceeded the ceiling | `TIMEOUT` (retryable, HTTP 504) |
| Vendor timeout or API failure | The provider's existing code, re-raised |
| Publish-shaped operation name | `TOOL_NOT_ALLOWED` |

`ProviderRegistry.call` rejects `publish`, `publish_instagram_media`, `create_instagram_media`, `upload_image`, `media_publish`, `post_to_instagram`, `instagram_publish`, `publish_media`, and `create_media`. Instagram publishing stays on `InstagramAgent`.

## Timeouts

`timeout_seconds` on the status is the per-request HTTP timeout the vendor client already uses. For DeepSeek that is `DEEPSEEK_TIMEOUT_SECONDS` when it is greater than zero, otherwise `REQUEST_TIMEOUT_SECONDS`. Other providers use `REQUEST_TIMEOUT_SECONDS`.

`call_timeout_seconds` is the ceiling around one `call`, including the provider's own retries. It is the greater of `AGENT_TIMEOUT_SECONDS` and the provider timeout. `call(..., timeout_seconds=)` overrides that ceiling when the override is positive.

`asyncio.TimeoutError` becomes `TIMEOUT`. A provider that already raised `DEEPSEEK_TIMEOUT` or `OPENAI_API_ERROR` is re-raised with its own code. The registry does not start a second HTTP client to implement the timer.

## Telemetry

Resolve and call log one line through `log_step`:

`MODEL_PROVIDER_RESOLVED` or `MODEL_PROVIDER_CALL`, then `agent_id`, `role`, `implementation`, `model`, `availability`, `status`, and on a call `duration_ms` and `error_code`.

The line is passed through `redact_text` and through a replacement of the process key values. Arguments, prompts, images, and the `Settings` object are not logged. A model id that contains a configured key is stored and logged as `[REDACTED]`.

## What this does not change

- Meta publishing, Instagram tools, and `enqueue_instagram_publication`.
- Default `LLM_PROVIDER=deepseek` and `IMAGE_PROVIDER=openai`.
- Startup with empty keys. The app still binds the mock providers in `api/app.py` through `select_reasoning_provider` and `select_image_provider`.
- Canva. It remains creative export, outside this registry.
- Studio offline planning. `GroundedCreativeModel` remains the studio provider when the DeepSeek key is empty.
