# Image generation

OpenAI image generation and editing are **IMPLEMENTED — NOT CONFIGURED** (`OPENAI_API_KEY` is empty). Product images and the company logo are **IMPLEMENTED — NOT CONFIGURED**: the MCP-to-edit path is in the code, and production logos and product images are not uploaded yet.

Related: [OpenAI](OPENAI.md), [Brand assets](../CONTENT/BRAND_ASSETS.md), [Products](../CONTENT/PRODUCTS.md), [Image QA](../CONTENT/IMAGE_QA.md), [Media storage](../STORAGE/MEDIA_STORAGE.md).

## Asset grounding

`services/asset_resolution.py` is the step between MCP and the OpenAI request.

| State | Sent to OpenAI as pixels? |
| --- | --- |
| `AVAILABLE` | Yes. The file bytes go on `ImageRequest.company_logo`, `product_image`, or `reference_images`, and the edit API receives `(filename, bytes, mime)` |
| `MISSING` | No. Generation continues from the text plan |
| `INVALID` | No |
| `UNAUTHORIZED` | No. The file is not read |
| `DELETED` | No |

When a logo or product image is `AVAILABLE`, the pipeline does not stop at a prompt line such as "Use the company logo". The edit call gets the file. Role notes in the prompt name the reference; they do not replace it.

When the logo is `MISSING`, no logo is drawn or described as if a file had been supplied. When the product image is `MISSING`, the prompt keeps the product name from the plan and no product photo is invented.

`ContentOrchestrator` (manual) and `ContentAgent` (scheduler, including daily automation) both use this resolver. A logo is not required for the process to start.

Image generation is OpenAI-only. DeepSeek does not implement an image API in this repository.

## Factory

`ai/image_generator.py`:

- `get_image_generation_provider`: `IMAGE_PROVIDER=openai` → `OpenAIImageGenerationProvider`. `mock` → `MockImageGenerationProvider`. Any other name, including `deepseek`, raises `OPENAI_CONFIGURATION_ERROR`. Construction does not require `OPENAI_API_KEY`.
- `select_image_provider`: used at startup. OpenAI when `IMAGE_PROVIDER=openai` and a key plus image model are set. Otherwise `MockImageGenerationProvider`, including when the key is missing. Startup does not crash and does not ask DeepSeek for pixels.

A missing key is **IMPLEMENTED — NOT CONFIGURED**, not a failure. `OpenAIImageGenerationProvider.generate` and the edit provider return `OPENAI_CONFIGURATION_ERROR`. Startup keeps `MockImageGenerationProvider`.

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
