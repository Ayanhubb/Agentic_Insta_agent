# Brand assets

Related: [Brand MCP](../MCP/BRAND_ASSET_MCP.md), [Products](PRODUCTS.md), [Image generation](../AI/IMAGE_GENERATION.md), [Media storage](../STORAGE/MEDIA_STORAGE.md).

## What is distinct

| Kind | Where it lives | Passed to OpenAI as pixels? |
| --- | --- | --- |
| Product metadata | `products` columns and `business_profiles.products` JSON | No. Names go into the plan and the text prompt |
| Product image | `product_assets` → `business_assets` file | Yes, on the edit path, when `load_reference_image` reads the file for this user |
| Company logo | `brand_profiles.logo_png_asset_id` or `logo_svg_asset_id`, or a `business_assets` row with a logo role | Yes, on the edit path, when the file loads. SVG may fail the raster MIME allowlist (`png`, `jpeg`, `webp`) |
| Brand guidelines | `brand_guidelines.body` plus profile style, colors, fonts, audience, language | No. Text in the creative context only |
| Creative references | `generated_images` used as a logo/product fallback by MCP | Only if an owned file is loaded. A metadata-only reference stays text |

If the edit path is not used, `OpenAIImageGenerationProvider` appends labels and asset ids to the prompt and does not send bytes. Do not describe that as image grounding.

## HTTP

Authenticated, owner-scoped. Missing or foreign ids return 404.

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/v1/brand` | `BrandWrite` |
| GET | `/api/v1/brand` | Profile |
| GET/PUT | `/api/v1/brand/guidelines` | Text guidelines |
| GET/POST | `/api/v1/brand/assets` | Multipart upload. Form `kind`, default `LOGO` |
| DELETE | `/api/v1/brand/assets/{asset_id}` | |
| GET | `/api/v1/brand/assets/{asset_id}/media` | File response |
| POST | `/api/v1/assets` | Multipart `file`, `role`, optional `scope`, `product_id`. Oversize → 413 |
| GET | `/api/v1/assets` | Query `scope`, `role` |
| GET/DELETE | `/api/v1/assets/{asset_id}` | |
| GET | `/api/v1/assets/{asset_id}/media` | |

Files are stored under the media root via `services/asset_paths.py`, keyed so one user cannot read another's storage key.

## Ownership

`business_assets.user_id` is required. MCP `get_company_logo` and `get_product_image` raise `ASSET_ACCESS_DENIED` for another tenant's id.

## Tests

`tests/test_brand_assets.py`, `tests/test_image_references.py`.
