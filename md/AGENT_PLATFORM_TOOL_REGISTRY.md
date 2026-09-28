# Platform tool registry

> **Status on 2026-09-28.** Checked against the tree. This page matches the package it names. The live studio path is unchanged. Agent rows are durable in `007_platform_agents`. Limits are in [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

Related: [MCP](AGENT_PLATFORM_MCP.md), [MCP runtime](AGENT_PLATFORM_MCP_RUNTIME.md), [Security](AGENT_PLATFORM_SECURITY.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md).

**IMPLEMENTED.** A generic tool registry sits above the in-process MCP allowlist. It does not replace `MCPClient`, and it does not register the Instagram publisher.

```text
Agent
  ↓
Tool binding          who may call which registered name, and whether write is granted
  ↓
Tool registry         only names server code has registered
  ↓
MCP adapter           mcp_read tools only; publishing names are refused
  ↓
Registered tool       the existing MCP handler, or a service handler on the service plane
  ↓
Service               RepositoryGateway, or another server-owned service
```

Instagram publishing stays on `agent/registry.py` and the six tools in `ALLOWED_TOOL_SET`. A write grant on this registry cannot name those tools.

## What a tool definition contains

**IMPLEMENTED** as `ToolDefinition` in `backend/platform/tools/definition.py`. Server code constructs it. There is no API that accepts a definition from a model.

| Field | Rule |
| --- | --- |
| `name` | Stable id. One registered version per name |
| `version` | Non-empty string. A call that asks for a different version is `UNKNOWN_TOOL` |
| `description` | Catalog text. The handler is not part of the catalog |
| `input_schema` | JSON object schema. `additionalProperties` must be false |
| `output_schema` | JSON object schema. The executor checks the result against it |
| `permissions` | `read` for a read tool. `write` for a mutation tool. Nothing else |
| `tenant_scope` | `tenant`. The id comes from `TenantContext` |
| `classification` | `read` or `write`. There is no default |
| `timeout_seconds` | Positive. The executor enforces it. The model cannot raise it |
| `idempotency` | `{idempotent: bool}`. Read tools must be idempotent. Write tools declare either value |
| `audit` | `required` must be true. Every execution is recorded, including failures |

The catalog (`PlatformToolRegistry.catalog`) returns those fields plus `plane`. It does not return the handler.

Planes this registry accepts:

| Plane | What may be registered |
| --- | --- |
| `mcp_read` | A name already on `MCP_TOOL_ALLOWLIST`, classified `read` |
| `service` | A server-owned handler that is not an MCP allowlist name and not an Instagram publishing name |

`instagram_publish` is not a registrable plane. `canva_remote` and `festival_scheduler` stay on their own adapters, the same split as [MCP](AGENT_PLATFORM_MCP.md).

## Binding

**IMPLEMENTED** as `ToolBinding` and `bind_agent`.

A binding is an agent id, a set of registered tool names, and a permission set of `read` and/or `write`. It does not register a tool and it does not store a handler or a secret.

Rules:

- Every name must already be in the registry.
- `INSTAGRAM_PUBLISHING_TOOLS` is rejected with `TOOL_NOT_ALLOWED`.
- A write-classified tool is included only when the grant has `write`.
- A read-classified tool is included only when the grant has `read`.
- The reference grant for MCP is every adapted read tool, permission `read`, agent id `instagram_marketing`. That grant does not include the six Instagram tools.

Omitting a name is how a tool is turned off. There is no disabled row.

## Execution rules

`ToolExecutor.execute(binding, name, arguments, tenant)` is the only way to run a registered tool.

1. The model cannot invent a tool. `register` is server code. An unknown name is `UNKNOWN_TOOL` and the handler is not called.
2. Only a registered tool can run. A name that is registered but absent from the binding is `TOOL_NOT_ALLOWED`.
3. Arguments are validated with the tool's input schema after tenant keys are removed. Failure is `MALFORMED_ARGUMENTS`.
4. The tenant id is `require_trusted_tenant`. Sources remain `authenticated`, `scheduler`, and `system`.
5. `strip_model_tenant_arguments` drops `tenant_id`, `user_id`, `account_id`, and the other untrusted keys. The discarded value is not passed to the handler and is not stored on the audit event. The event records `ignored_untrusted_tenant` instead.
6. Results pass through `sanitize_payload`. Secret keys are removed. Secret-shaped text is redacted. A result that still contains a secret key is `SECRET_REJECTED` and is not returned.
7. Publishing names raise `TOOL_NOT_ALLOWED` before a handler runs. They cannot be registered on this registry or on MCP.
8. Classification is explicit. A `write` tool called without `write` on the binding is `UNAUTHORIZED_MUTATION` and the handler is not called. MCP tools are all `read`. A mutation, if one is added later, is a `service` plane handler reviewed in code. It is not an MCP tool and it is not `publish_instagram_media`.
9. The executor writes `platform.tool.execute` for success and failure: tool, version, agent, trusted tenant, classification, status, error code, duration, sanitized arguments, result keys, idempotency, and whether a model tenant key was ignored.
10. Failures are `ToolExecutionError`. `as_dict()` is `{ok: false, error: {code, message, tool, retryable}}`. Messages do not include argument values, secrets, or another tenant's payload.

A result whose top-level `tenant_id` or `user_id` is not the trusted tenant is `CROSS_TENANT_DENIED`. The payload is not returned. Service handlers that load a row call `require_resource_tenant` with the row's owner and the trusted context.

After those checks the executor sets `tenant_id` on the result from the trusted context.

Timeouts use the definition's `timeout_seconds`. A timeout of an idempotent tool is `retryable: true`. A non-idempotent write timeout is not marked retryable, because the mutation may already have happened. The registry does not retry by itself.

## Instagram publishing

**UNCHANGED.**

| Registry | What it accepts |
| --- | --- |
| `agent/registry.py` `ToolRegistry` | The six Instagram tools only |
| `backend/mcp/registry.py` `MCPRegistry` | The 22 read tools. Publishing names raise `TOOL_NOT_ALLOWED` |
| `PlatformToolRegistry` | Those 22 reads via the MCP adapter, plus reviewed `service` tools. Publishing names raise `TOOL_NOT_ALLOWED` |

`enqueue_instagram_publication` and `InstagramAgent` are still the only publish path. `get_content_rules` still reports `publishing.available: false`.

## Adding a tool

Read tool on MCP: follow the existing MCP steps in [MCP](AGENT_PLATFORM_MCP.md). `build_platform_registry` picks it up from `build_registry`. Do not give it `classification: write`.

Service mutation, only after review:

1. Write a handler `(TenantContext, arguments) -> dict` that reads the tenant from `TenantContext` and calls `require_resource_tenant` for any row it loads.
2. Declare object schemas. Do not put tenant ids or secret field names in either schema.
3. Set `classification` to `write`, `permissions` to `{write}`, `plane` to `service`, and `idempotency` explicitly.
4. Register it on `PlatformToolRegistry`. Do not add the name to `MCP_TOOL_ALLOWLIST`.
5. Grant it only from a binding that has `write`.
6. Cover unknown name, invalid arguments, cross-tenant denial, a read-only binding, secret redaction, and an audit event.

Do not add `validate_image`, `prepare_image`, `upload_image`, `create_instagram_media`, `publish_instagram_media`, `verify_publication`, or the publish aliases.

## Tests

**IMPLEMENTED:** `tests/test_platform_tool_registry.py`.

| Case | Expected code or result |
| --- | --- |
| Unknown name | `UNKNOWN_TOOL`, handler not called |
| Invalid arguments | `MALFORMED_ARGUMENTS`, handler not called |
| Other tenant's row, or a result stamped with another tenant | `CROSS_TENANT_DENIED` |
| Model-supplied tenant id | Ignored. The trusted tenant is used |
| Write tool without `write` | `UNAUTHORIZED_MUTATION`, handler not called |
| Secret keys and secret-shaped text | Absent from the result, the error, and the audit log |
| Success and failure | `platform.tool.execute` with status, tenant, and error code |
| Registered read with valid arguments | Result for the trusted tenant |
| Publishing names | `TOOL_NOT_ALLOWED` on register, bind, and execute |
| MCP adapter | The 22 allowlisted tools, all `read`, and `get_business_profile` still scoped to the trusted tenant |

Existing MCP tests stay the regression gate for `MCPClient`.
