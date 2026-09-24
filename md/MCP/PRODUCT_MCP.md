# Product MCP

Product image lookup is **IMPLEMENTED — NOT CONFIGURED**. Production product images are not uploaded yet. When a file exists, `get_product_image` returns it for this tenant and the OpenAI edit path can attach the bytes. A missing image returns `found: false`. Generation continues from product metadata and does not invent a photo.

Related: [Products](../CONTENT/PRODUCTS.md), [MCP architecture](MCP_ARCHITECTURE.md), [Image generation](../AI/IMAGE_GENERATION.md).

Server string: `product`. Module: `backend/mcp/servers/product.py`.

Read only. Cannot publish. Tenant comes from `TenantContext`.

## `get_product`

Purpose: look up one product by name in this tenant's catalog, then fall back to names on the business profile.

Input: `name` string, length 1–200, required.

Output: `found`, `product`.

Data: `products` and `product_assets`. Fallback: `business_profiles.products` JSON.

Error: `MALFORMED_ARGUMENTS` if `name` is missing or the wrong type.

## `get_product_image`

Purpose: load a product image owned by this tenant. Another tenant's asset id is denied.

Input (all optional in the schema; the handler needs at least one): `product` 1–200, `product_id` 1–64, `image_id` 1–64.

Output: `found`, `asset`, `assets`.

Data: `products`, `product_assets`, `business_assets`. Fallback: `generated_images` whose content type is product.

Errors: `MALFORMED_ARGUMENTS`, `ASSET_ACCESS_DENIED`.

The tool returns asset metadata and ids for this tenant only. A `product_id` or `image_id` owned by someone else raises `ASSET_ACCESS_DENIED`. It does not return the other tenant's file.

Those ids are not pixels. `resolve_product_asset` / `resolve_product_assets` (`services/asset_resolution.py`) classify the file as `AVAILABLE`, `MISSING`, `INVALID`, `UNAUTHORIZED`, or `DELETED`, and only `AVAILABLE` bytes are passed into the OpenAI edit call. See [Image generation](../AI/IMAGE_GENERATION.md).

## `get_active_offers`

Purpose: list this tenant's approved promotion images.

Input: none.

Output: `offers`.

Data: `generated_images` with promotion content and approval `APPROVED` or `AUTO_APPROVED`.

## Tests

Covered with brand and product isolation in `tests/test_brand_assets.py` and MCP discovery in `tests/test_mcp.py`.
