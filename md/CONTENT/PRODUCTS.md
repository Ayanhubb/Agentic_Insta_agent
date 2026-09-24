# Products

Related: [Product MCP](../MCP/PRODUCT_MCP.md), [Brand assets](BRAND_ASSETS.md), [Image generation](../AI/IMAGE_GENERATION.md).

## Table `products`

Columns: `id`, `user_id`, `name`, `description`, `category`, `price` (`Numeric(12,2)`), `sku`, `is_active`, `offer`, timestamps.

Images are not columns on `products`. They are `product_assets` rows pointing at `business_assets`.

`business_profiles.products` is a JSON list of names. `get_product` falls back to that list when the catalog table has no match. A JSON name has no image.

## HTTP

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/v1/products` | `ProductWrite` |
| GET | `/api/v1/products` | Query `active` default false |
| GET | `/api/v1/products/{product_id}` | 404 if not owned |
| PUT | `/api/v1/products/{product_id}` | `ProductUpdate` |
| POST | `/api/v1/products/{product_id}/images` | Multipart `file` |

Offers: `GET/POST /api/v1/offers` read and write the `offer` field on a product. That is metadata. It is not an image.

## MCP

`get_product` requires `name`. `get_product_image` resolves owner-scoped files. `get_active_offers` lists approved promotion images in `generated_images`, not the `products.offer` strings.

## Image generation

A featured product with no readable image blocks automatic publish (`product_reference_missing`) when the approval policy requires a reference. The generator does not substitute a stock photo.

Manual generation can still run from the text plan. The prompt then contains the product name. Pixels are included only when the edit path loads the file. See [Image generation](../AI/IMAGE_GENERATION.md).

## Tests

`tests/test_brand_assets.py`, `tests/test_image_references.py`.
