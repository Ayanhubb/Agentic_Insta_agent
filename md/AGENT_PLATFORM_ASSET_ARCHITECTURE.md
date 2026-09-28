# Asset architecture

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Platform architecture](AGENT_PLATFORM_ARCHITECTURE.md), [Knowledge architecture](AGENT_PLATFORM_KNOWLEDGE_ARCHITECTURE.md), [Data model](AGENT_PLATFORM_DATA_MODEL.md), [Security](AGENT_PLATFORM_SECURITY.md), [Brand assets](CONTENT/BRAND_ASSETS.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md).

**IMPLEMENTED.** Package `backend/platform/assets`. Studio upload routes, `AssetCatalog`, and the MCP asset tools are unchanged. This package classifies those files as optional capabilities an agent may bind.

```text
Tenant                         users.id, from the session or the scheduler
  ↓
Asset store                    owned files and metadata
  ↓
Asset binding                  roles granted to one agent version
  ↓
MCP context                    only the granted roles are read
  ↓
Agent execution                resolve, then prepare for a provider when a step asks
```

A company logo is one role. It is not a platform requirement. An agent with an empty binding runs without opening any file.

## Capabilities

Every capability is optional.

| Capability | What it is | How it is stored today |
| --- | --- | --- |
| Brand | Company logo | `business_assets` roles `logo_png` and `logo_svg` |
| Product | Product image | `business_assets` role `product_image`, linked by `product_assets` |
| Document | Brand guide or another document | `guideline` on the catalog, or a platform document the catalog does not list |
| Campaign | Campaign creative | `business_assets` role `campaign` |
| Reference image | Raster the pixel provider may receive | Eligibility flag. Not a second file type |
| Agent | File used by one agent and not assumed for the others | Catalog role `other`, or a platform record |

Roles an agent binds:

| Role | Capability | Reference image |
| --- | --- | --- |
| `company_logo` | Brand | PNG only. SVG stays a brand file and is not sent as pixels |
| `product_image` | Product | PNG or JPEG |
| `brand_guide` | Document | No. Text or PDF |
| `campaign` | Campaign | PNG or JPEG |
| `document` | Document | No |
| `reference_image` | Reference image | PNG or JPEG |
| `agent_asset` | Agent | No, even when the file is a PNG |

The Instagram reference agent binds `company_logo`, `product_image`, `brand_guide`, and `campaign`. It does not bind `document`, `reference_image`, or `agent_asset`. A missing logo stays `found: false`. The service does not draw a replacement.

An operations-style agent can bind `document` and nothing else. Asking that agent for `company_logo` is `NOT_GRANTED`. The logo file is not opened, and the error does not include the file name.

## Ownership and access

The tenant id is `users.id`. Asset owner and tenant are the same id. There is no share across tenants.

| Check | Result |
| --- | --- |
| Caller tenant does not match the row | `NOT_FOUND`. The same code as a missing id |
| Role is not on the agent binding | `NOT_GRANTED`, before the file is read |
| Role is on the binding but not on the execution snapshot | `NOT_GRANTED` |
| Role matches and the file exists | Public metadata. No storage key |
| Role matches and the file does not exist | `found: false` |

`PlatformAssetService.resolve` takes the tenant from the execution context. A `tenant_id` in the payload is ignored. A foreign asset id in that payload resolves as not found.

Catalog rows keep the existing rule: `AssetCatalog.get_asset` for another user raises `NOT_FOUND` with HTTP 404. `upload_through_catalog` calls `AssetCatalog.upload` and then indexes the public record. It does not add a route or a column.

## Intake

Intake reuses `services/asset_validation.py` and `services/asset_paths.py`.

| Rule | Behavior |
| --- | --- |
| MIME | Bytes decide the type. A declared type that disagrees is rejected |
| Size | `ImageLimits` from the catalog floor (minimum side 16, not the publish minimum of 320) |
| Filename | Label only. `..`, slashes, backslashes, drive prefixes, and control characters are `INVALID_FILENAME` |
| Path | The caller cannot pass a storage path. Keys are `assets/{tenant}/{scope}/{generated name}` |
| Traversal | `AssetPaths.resolve_storage_key` rejects `..`, absolute paths, and another tenant's prefix |

SVG script, foreign object, and active PDF content stay rejected by the existing validators.

## Lifecycle, deletion, and versioning

| Object | Rule |
| --- | --- |
| File bytes | Immutable. A replacement is a new id |
| Metadata | Revision counter. A metadata update does not rewrite bytes. Path keys and secret keys are dropped |
| Binding | Immutable for that agent version. A later version is a new binding. Version 1 does not gain roles because version 2 exists |
| Deletion | Platform files are unlinked. Catalog-backed files go through `AssetCatalog.delete_asset`. Afterward the id is not found for any tenant |
| Audit | Action, tenant, agent, asset id, role, status, error code. No bytes, no storage key, no file name |

Foreign and missing ids share the audit code `NOT_FOUND`, so the log line is not proof that another tenant holds the id.

## Provider preparation

`prepare_for_tenant` chooses what a provider may receive.

| Target | Eligible file | Result |
| --- | --- | --- |
| `text` | Any granted asset | Metadata only. `image` is absent. Reason `metadata_only` |
| `image_edit` | Reference-eligible PNG or JPEG | `ImageInput` bytes plus a public view that has no storage key |
| `image_edit` | SVG, PDF, text, or an agent PNG | `eligible: false`, reason `not_a_reference_image`. No stand-in image |
| `image_edit` | Granted role, no file | `found: false` |

Bytes are read only for an eligible `image_edit`. The public view is passed through `public_payload`, which removes path keys, secret keys, and tenant keys copied out of a tool result.

## MCP context

`McpContextAssembler` calls `get_company_logo` only when the binding includes `company_logo`. It calls `get_product_image` only when the binding includes `product_image` and the request names a product or an image id. Roles without an allowlisted tool are read from the asset store as metadata.

`ContextBuilder` in `backend/mcp/context_builder.py` is the existing studio bundle. It still loads the logo for the current product path. New agents use the assembler so they do not inherit that call.

## What this package does not do

- It does not replace `/api/v1/assets`, `/api/v1/brand/assets`, or `/api/v1/products/{id}/images`.
- It does not add a table or a second blob root. Platform-only files use the same `assets/{tenant}/...` rules and are not added to the catalog list unless `upload_through_catalog` stored them there.
- It does not publish, and it does not import the Instagram publisher.
- It does not treat a logo as required for every agent.

## Files

| Path | Role |
| --- | --- |
| `backend/platform/assets/catalog.py` | Capabilities, roles, reference-image rule |
| `backend/platform/assets/binding.py` | `AssetBinding`, `instagram_marketing_assets` |
| `backend/platform/assets/validation.py` | Filename, MIME, and size checks over the existing helpers |
| `backend/platform/assets/store.py` | Tenant store, catalog bridge, deletion |
| `backend/platform/assets/prepare.py` | Provider preparation |
| `backend/platform/assets/service.py` | `PlatformAssetService` runtime port |
| `backend/platform/assets/audit.py` | Audit log |
| `backend/platform/redaction.py` | Path, secret, and tenant-key stripping |
| `tests/test_platform_assets.py` | Isolation, intake, preparation, catalog bridge |

## Tests

`tests/test_platform_assets.py` covers two tenants, ungranted logos, missing logos, filename and path rejection, MIME and size, SVG versus PNG eligibility, text versus image-edit preparation, metadata revisions, deletion, payload tenant ids, and `AssetCatalog` ownership after `upload_through_catalog`.
