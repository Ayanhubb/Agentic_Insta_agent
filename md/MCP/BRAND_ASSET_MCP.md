# Brand and asset MCP

Related: [Brand assets](../CONTENT/BRAND_ASSETS.md), [MCP architecture](MCP_ARCHITECTURE.md), [Tenant isolation](../SECURITY/TENANT_ISOLATION.md).

Two servers. Both are read-only and cannot publish.

## `brand` — `get_brand_guidelines`

Module: `backend/mcp/servers/brand.py`.

Purpose: load brand style, language, audience, and location for this tenant.

Input: none.

Output: `found`, `guidelines`, `assets`.

Data: `business_profiles`, `brand_guidelines`, and `business_assets` with brand scope.

`guidelines` here are stored text and profile fields. They are not a binary brand book unless an asset id is attached.

## `asset` — `get_company_logo`

Module: `backend/mcp/servers/asset.py`.

Purpose: load this tenant's logo. A cross-tenant `asset_id` is denied.

Input: optional `asset_id` string 1–64.

Output: `found`, `asset`.

Data: `brand_profiles` logo foreign keys and `business_assets` whose role is a logo. Fallback: `generated_images` tagged brand/logo.

Error: `ASSET_ACCESS_DENIED`.

The returned object identifies the file. Bytes are loaded only by `load_reference_image` after the owner check. If no file exists, image generation does not receive logo pixels.

## Business profile (related)

`get_business_profile` (`server=business`, `backend/mcp/servers/business.py`) takes no arguments and returns `found` and `profile` from `business_profiles`. The content orchestrator refuses to plan when `business_name` is missing (`CONTENT_PROFILE_MISSING`).

## Content rules (related)

`get_content_rules` (`server=content`) returns content types, modes, diversity limits, festival settings, and this tenant's `automation_settings`. The publishing block is explicit: `available: false`, `user_prompt_auto_publish: false`.

## Tests

`tests/test_mcp.py`, `tests/test_brand_assets.py`.
