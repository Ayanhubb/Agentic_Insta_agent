# MCP runtime

> **Status on 2026-09-25.** Checked against the tree. This page matches the package it names. Limits (not the live studio path, not durable) are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [Tool registry](AGENT_PLATFORM_TOOL_REGISTRY.md), [MCP](AGENT_PLATFORM_MCP.md), [Security](AGENT_PLATFORM_SECURITY.md), [Runtime](AGENT_PLATFORM_RUNTIME.md).

MCP still runs inside the API process. The platform runtime is a second caller in front of the same handlers. It is not a network server, and it is not a tool the model can extend.

## Two callers, one allowlist

| Caller | Who uses it | What it runs |
| --- | --- | --- |
| `MCPClient.invoke` | Context builder, content orchestrator, and the existing MCP tests | `MCP_TOOL_ALLOWLIST` only |
| `ToolExecutor` through `MCPAdapter` | An agent that holds a `ToolBinding` | The same allowlist, after the binding, classification, and audit checks |

**UNCHANGED:** `MCPClient` still strips model tenant keys, rejects publishing names, validates arguments, applies its timeout, sanitizes the payload, injects the trusted tenant id, and writes `mcp.tool.execute`.

**IMPLEMENTED:** `build_platform_registry` / `build_platform_executor` in `backend/platform/tools/`. The adapter copies each MCP tool into a `ToolDefinition` and, at call time, loads that tool again from `MCPRegistry.get`. The handler that runs is the one on the MCP registry, which calls `RepositoryGateway` or the account and trend services. The adapter does not contain a second copy of those services.

```text
ToolExecutor
  require trusted tenant
  strip model tenant keys
  reject publishing names
  registry lookup
  binding and read/write grant
  input schema
  timeout
    ↓
MCPAdapter.invoke
  plane must be mcp_read and classification must be read
  MCPRegistry.get          allowlist and publishing-name check again
    ↓
MCP tool handler
    ↓
RepositoryGateway or the existing account / trend service
    ↓
sanitize, output schema, trusted tenant id, audit
```

A handler on the platform definition cannot bypass that second `MCPRegistry.get`. Replacing the definition's handler does not change the function the adapter calls.

## What the adapter registers

For each name `MCPRegistry.discover` returns:

| Definition field | Value |
| --- | --- |
| `version` | `1` |
| `classification` | `read` |
| `permissions` | `read` |
| `tenant_scope` | `tenant` |
| `plane` | `mcp_read` |
| `timeout_seconds` | The adapter timeout. Default 5 seconds, the same default as `MCPClient` |
| `idempotency` | idempotent |
| `audit.required` | true |
| `input_schema` | The MCP tool schema |
| `output_schema` | Object, additional properties allowed, then sanitized |

Discovery order and the 22 names are unchanged. See [MCP](AGENT_PLATFORM_MCP.md). `publish_instagram_media` and the other publishing names are not in the catalog. Registering one raises `TOOL_NOT_ALLOWED` inside `ToolDefinition` before it is stored.

The adapter will not register a write tool. A name on `MCP_TOOL_ALLOWLIST` cannot be registered on the `service` plane, so a caller cannot replace `get_business_profile` with a different handler and keep the MCP name.

## Call sequence

`ToolExecutor.execute` for an MCP tool:

1. Reject a binding that is not a `ToolBinding`.
2. `require_trusted_tenant`. A missing or model-built tenant is `TENANT_CONTEXT_REQUIRED`.
3. `strip_model_tenant_arguments`. Later steps see the stripped dict only.
4. If the name is an Instagram publishing tool, `TOOL_NOT_ALLOWED` and stop.
5. `PlatformToolRegistry.get`. Unknown names are `UNKNOWN_TOOL`.
6. If the caller passed a version and it is not the registered version, `UNKNOWN_TOOL`.
7. If the tool is `write` and the binding has no `write` permission, `UNAUTHORIZED_MUTATION`. MCP tools do not take this branch.
8. If the name is not on the binding, `TOOL_NOT_ALLOWED`.
9. `validate_arguments` against the input schema.
10. `asyncio.wait_for` using `timeout_seconds` on the definition. Expiry is `TIMEOUT`.
11. `MCPAdapter.invoke`, which calls the allowlisted handler with the trusted tenant and the validated arguments.
12. Reject a result whose `tenant_id` or `user_id` is not the trusted tenant (`CROSS_TENANT_DENIED`).
13. `sanitize_payload`. Then reject the result if a secret key remains (`SECRET_REJECTED`).
14. Set `tenant_id` from the trusted context and check the output schema.
15. In `finally`, append an audit event and log `platform.tool.execute`.

MCP errors keep their codes (`MALFORMED_ARGUMENTS`, `ASSET_ACCESS_DENIED`, `TIMEOUT`, and the others on `MCPError`) and are re-raised as `ToolExecutionError`. The message is a fixed string for that code. The handler's exception text is not copied into the response.

`ASSET_ACCESS_DENIED` from an asset tool stays that code. The message is generic: the resource is not available for this tenant.

## Tenant and secrets

The tenant id the handler receives is `TenantContext.tenant_id`. Argument keys in `UNTRUSTED_TENANT_KEYS` are removed first, including when the input schema would otherwise have rejected them as extra properties. Schemas are not allowed to declare those keys or a secret field name.

Audit arguments are the sanitized eligible arguments. Model tenant ids are removed from the audit payload. The trusted tenant id is its own field, taken from `TenantContext`.

Secret handling is the same function MCP already uses: `sanitize_payload` in `backend/mcp/client.py`, which drops secret keys and path keys and runs `redact_text`. The platform result and the platform audit line both go through it. `MCPClient` still does this for its own callers.

## Publishing

This runtime does not call `InstagramAgent`, `PublicationService`, or the Graph create, publish, or verify methods.

`get_content_rules` is still the content-rule reader. Its payload still has `publishing.available: false` and `user_prompt_auto_publish: false`.

A binding may grant the 22 read tools to `instagram_marketing`. That grant is not the publish workflow. Publish still enters through `enqueue_instagram_publication`.

## What this runtime does not do

- It does not open a socket or register a remote MCP server.
- It does not move Canva or `FestivalMcp` onto the model allowlist.
- It does not change `MCPClient` call sites. Studio code that already calls `MCPClient` keeps that path and its current errors.
- It does not retry a tool. Idempotency is declared so a caller can decide. The executor only marks an idempotent timeout as retryable.
- It does not accept a timeout, a tenant id, or a tool definition from the model.

## Tests

`tests/test_platform_tool_registry.py` runs `get_business_profile` and `get_content_rules` through `build_platform_executor`. A model-supplied tenant id does not change the profile. The content-rule payload still says publishing is unavailable.

`tests/test_mcp.py` still covers `MCPClient` directly: discovery, invocation, tenant stripping, asset denial, unknown tools, publishing aliases, timeout, and secret redaction in the MCP audit line.
