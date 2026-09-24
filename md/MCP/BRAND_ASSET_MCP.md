# Brand and asset MCP

Company logo lookup is **IMPLEMENTED — NOT CONFIGURED**. Production logos are not uploaded yet. When a logo file exists, `get_company_logo` returns it for this tenant and the OpenAI edit path can attach the bytes. A missing logo returns `found: false` and is not replaced with a generated mark.

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

The returned object identifies the file for this tenant. Another tenant's `asset_id` raises `ASSET_ACCESS_DENIED`. The tool does not return that file.

`resolve_brand_logo` and `resolve_brand_assets` (`services/asset_resolution.py`) then classify the caller's business (`business_profiles.id`) as `AVAILABLE`, `MISSING`, `INVALID`, `UNAUTHORIZED`, or `DELETED`. Only an `AVAILABLE` raster is passed into the OpenAI edit call. A missing logo does not block startup and does not invent a mark.

## Business profile (related)

`get_business_profile` (`server=business`, `backend/mcp/servers/business.py`) takes no arguments and returns `found` and `profile` from `business_profiles`. The content orchestrator refuses to plan when `business_name` is missing (`CONTENT_PROFILE_MISSING`).

## Content rules (related)

`get_content_rules` (`server=content`) returns content types, modes, diversity limits, festival settings, and this tenant's `automation_settings`. The publishing block is explicit: `available: false`, `user_prompt_auto_publish: false`.

## Tests

`tests/test_mcp.py`, `tests/test_brand_assets.py`.
