# Knowledge architecture

> **Status on 2026-09-28.** Checked against the tree. This page matches the package it names. The live studio path is unchanged. Agent rows are durable in `007_platform_agents`. Limits are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Platform architecture](AGENT_PLATFORM_ARCHITECTURE.md), [Asset architecture](AGENT_PLATFORM_ASSET_ARCHITECTURE.md), [Data model](AGENT_PLATFORM_DATA_MODEL.md), [MCP](AGENT_PLATFORM_MCP.md), [Security](AGENT_PLATFORM_SECURITY.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md).

**IMPLEMENTED.** Package `backend/platform/knowledge`. Live business, brand, product, festival, account, trend, and content-rule reads stay on the current tables and MCP tools. This package decides which of those names an agent may load, and it holds versioned documents and structured business snapshots in the process.

```text
Tenant                         users.id
  ↓
Knowledge store                versioned documents and business snapshots
  ↓
Knowledge binding              source names granted to one agent version
  ↓
MCP context                    granted live tools, plus granted snapshots
  ↓
Agent execution                PlatformKnowledgeService.load
```

No agent receives every source by default. An empty binding loads nothing.

## Sources

| Source | Kind | Where the content lives |
| --- | --- | --- |
| `business` | Live, and an optional structured snapshot | `business_profiles` through `get_business_profile`. A snapshot is versioned in the knowledge store |
| `brand` | Live | `brand_profiles` and `brand_guidelines` through `get_brand_guidelines` |
| `product` | Live | `get_active_offers`, or `get_product` when the request names one |
| `document` | Versioned | Knowledge store. Not an MCP tool |
| `festival` | Live | Festival catalog through the festival MCP tools |
| `account` | Live | Account MCP tools. This store refuses to persist account rows |
| `trend` | Live | Trend MCP tools |
| `content_rules` | Live | `get_content_rules` |

The Instagram reference agent binds `business`, `brand`, `product`, `festival`, `account`, `trend`, and `content_rules`. It does not bind `document`. Brand guidelines for that agent arrive on the `brand` source. A brand-guide file is an asset role, described in [asset architecture](AGENT_PLATFORM_ASSET_ARCHITECTURE.md).

Another agent can bind `business` and `document` only. Festival, account, and trend tools are not called. A company logo is not loaded unless that agent's asset binding names `company_logo`.

## Ownership and access

The tenant id is `users.id`. A record's owner is that same id.

| Check | Result |
| --- | --- |
| Another tenant's document id, including an older version | `NOT_FOUND` |
| Source not on the binding | `NOT_GRANTED` |
| Source on the binding but not on the execution snapshot | `NOT_GRANTED` |
| Granted live source with no row | `found: false`. Nothing is invented |
| Granted document with no snapshot | `found: false` |

`PlatformKnowledgeService.load` uses `ctx.tenant_id`. A payload `tenant_id` is not read. Tool arguments pass through `strip_model_tenant_arguments` before a handler runs. The discarded value is not used as the tenant.

Tool results are passed through `public_payload`. Secret keys, filesystem keys, and tenant keys inside the result are removed. The assembler then sets the packet's `tenant_id` from the trusted context, so a handler cannot substitute one.

A handler exception becomes `found: false`. The exception text is not copied into the packet.

## Versioning and deletion

Documents and structured business snapshots are versioned. `put` starts at version 1. `revise` keeps the previous body until `delete`. `delete` marks every version deleted and clears the body. After that, no version is readable.

Live sources other than the business snapshot cannot be written here. `put` of `festival`, `account`, `trend`, `brand`, `product`, or `content_rules` is `NOT_VERSIONED`. Those stay on their current tables so this package does not become a second copy of account or trend data.

Titles reject empty strings, path segments, and control characters. Bodies are JSON objects. Secret keys and path keys are stripped. A body larger than 64KB is rejected.

Agent bindings are immutable per version number, same as asset bindings. Publishing a new agent version does not rewrite the previous grant list.

## MCP context

`McpContextAssembler.build` is the step between the stores and a run.

For each granted knowledge source it calls the mapped MCP tool when an invoker is configured:

| Source | Tool |
| --- | --- |
| `business` | `get_business_profile` |
| `brand` | `get_brand_guidelines` |
| `product` | `get_active_offers`, or `get_product` when `name` is present |
| `festival` | `get_upcoming_festivals`, or `get_festival_details` when `festival_name` is present |
| `account` | `get_account_summary` |
| `trend` | `get_current_trends` |
| `content_rules` | `get_content_rules` |
| `document` | None. The snapshot is read from the knowledge store |

Asset roles are included only when the asset binding grants them. `get_company_logo` is not called for an agent that did not bind `company_logo`. `get_product_image` is called only when the role is bound and the request names a product or an image id. Other granted roles contribute metadata from the asset store, not pixels.

`ContextBuilder` still builds the studio bundle, including the logo read that the current Instagram path expects. Agents registered on this platform use the assembler instead of that fixed list.

## Audit

Each put, revise, delete, and missed read records the action, tenant, agent, source, record id, version, status, and error code. The body and the title are not on the line. A miss uses `NOT_FOUND` for both an unknown id and another tenant's id.

## What this package does not do

- It does not add an embedding index, a retrieval service, or a knowledge table.
- It does not change MCP allowlists or `ContextBuilder`.
- It does not store provider keys, Graph tokens, or account ciphertext.
- It does not publish.

## Files

| Path | Role |
| --- | --- |
| `backend/platform/knowledge/catalog.py` | Source names and MCP tool map |
| `backend/platform/knowledge/binding.py` | `KnowledgeBinding`, `instagram_marketing_knowledge` |
| `backend/platform/knowledge/store.py` | Versioned documents and business snapshots |
| `backend/platform/knowledge/context.py` | `McpContextAssembler` |
| `backend/platform/knowledge/service.py` | `PlatformKnowledgeService` runtime port |
| `backend/platform/knowledge/audit.py` | Audit log |
| `tests/test_platform_knowledge.py` | Grants, versions, tenant isolation, MCP context |

## Tests

`tests/test_platform_knowledge.py` covers an Instagram binding that does not include documents, an operations binding that does not load festivals or logos, version history, deletion, cross-tenant reads, structured business snapshots, trusted-tenant tool calls, stripped secrets and storage keys, and a logo tool that stays uncalled when the asset role is absent.
