# Image generation

Related: [OpenAI](OPENAI.md), [Brand assets](../CONTENT/BRAND_ASSETS.md), [Products](../CONTENT/PRODUCTS.md), [Image QA](../CONTENT/IMAGE_QA.md), [Media storage](../STORAGE/MEDIA_STORAGE.md).

Image generation is OpenAI-only. DeepSeek does not implement an image API in this repository.

## Factory

`ai/image_generator.py` `get_image_generation_provider`:

- `IMAGE_PROVIDER` empty or `openai` → `OpenAIImageGenerationProvider`
- any other name → configuration error

`backend/ai/image/factory.py` `get_openai_image_provider` / `create_image_provider` returns `OpenAIImageProvider` for the edit path. The module docstring states it is not an Instagram tool and cannot publish.

`api/app.py` stores the generation provider on `app.state`. Generation routes pass `request.app.state.images` into `ContentOrchestrator`. Reference submission goes through `submit_creative_image` (`services/image_reference.py`), which prefers the edit-capable provider when reference bytes exist.

## Text-only generation

`OpenAIImageGenerationProvider.generate` calls `images.generate`.

`_prompt_with_references` adds `kind`, `asset_id`, and `label` strings to the prompt. Those are metadata. Pixel bytes are not attached on this path.

## Edit / reference path

`OpenAIImageProvider.edit` sends `image` as a list of `(filename, bytes, mime)`.

`load_reference_image` reads bytes only after the asset id is confirmed for the caller. Missing, foreign, deleted, or unreadable files yield no image. The code does not invent a product photo or a logo.

Limits: raster MIME types `image/png`, `image/jpeg`, `image/jpg`, `image/webp`. At most 8 reference images (`MAX_REFERENCE_IMAGES`).

If a product plan has no loadable product image, approval can block with `product_reference_missing`. A required logo that cannot be loaded blocks with `required_logo_missing` (`scheduler/approval_policy.py`). That is a gate, not a synthetic image.

## Output

Files land under `storage/generated/{user_id}/` via `LocalGeneratedImageStore`. Rows are `generated_images`. Owner-scoped reads:

- `GET /api/v1/generation/{image_id}/media`
- `GET /api/v1/media/generated/{image_id}` (authenticated)

Public hosting for Meta uses `IMAGE_PUBLIC_BASE_URL` / `PUBLIC_IMAGE_BASE_URL`. The code default when unset is the mock host `https://cdn.example.test/instagram-agent`, which Meta cannot fetch. Live publish needs a real HTTPS origin.

## Retries

`IMAGE_MAX_ATTEMPTS` (default 3) and `OPENAI_RETRY_DELAY_SECONDS` (default 0.4).

## QA after generation

The orchestrator reviews bytes with the creative client's `review_image` (DeepSeek vision, or Pillow size check on `GroundedCreativeModel`). The scheduler uses `ImageQAService`. See [Image QA](../CONTENT/IMAGE_QA.md).
