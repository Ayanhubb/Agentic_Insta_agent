# Product MCP

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

The tool returns asset metadata and ids. Passing those ids into OpenAI as pixels happens later in `services/image_reference.py`, only when the file exists for this user. See [Image generation](../AI/IMAGE_GENERATION.md).

## `get_active_offers`

Purpose: list this tenant's approved promotion images.

Input: none.

Output: `offers`.

Data: `generated_images` with promotion content and approval `APPROVED` or `AUTO_APPROVED`.

## Tests

Covered with brand and product isolation in `tests/test_brand_assets.py` and MCP discovery in `tests/test_mcp.py`.
