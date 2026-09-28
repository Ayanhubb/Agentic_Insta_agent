# Agent platform frontend

Related: [Architecture](AGENT_PLATFORM_ARCHITECTURE.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Security](AGENT_PLATFORM_SECURITY.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md).

The agent builder is a separate area of the existing React app. The Instagram studio pages stay the interface of `instagram_marketing`. Status on 2026-09-25: **PARTIAL**. See [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md).

The pages call `/api/v1/agents` only. They do not call `/api/v1/platform/agents`, and they do not call validate, version publish, open draft, clone, rollback, or execute. Those routes exist on the server.

## What is implemented

**IMPLEMENTED.** Authenticated pages under the **Agents** nav item:

| Route | What it configures |
| --- | --- |
| `/agents` | Agents for the signed-in user, plus the system reference agent |
| `/agents/new` | Key, name, and description |
| `/agents/:id` | Name, description, pause, and retire |
| `/agents/:id/workflow` | The registered workflow and its steps. Steps are not editable |
| `/agents/:id/tools` | MCP read tools from the allowlist |
| `/agents/:id/models` | LLM provider and model, image provider and model, and the vision role the workflow requires |
| `/agents/:id/assets` | Asset roles. Files stay in the existing asset library |
| `/agents/:id/knowledge` | Named knowledge sources |
| `/agents/:id/triggers` | One registered trigger |
| `/agents/:id/approval` | The registered approval policy and its gates |
| `/agents/:id/executions` | Test runs stored for that agent |

`instagram_marketing` is read-only. Its overview links to the existing Instagram studio. The studio pages are not a builder.

## API

The pages use the same `request()` client as the studio: Bearer token or cookie, `credentials: "include"`, and `ProtectedRoute`.

| Method | Path | Body |
| --- | --- | --- |
| `GET` | `/api/v1/agents` | `{ "agents": [] }` |
| `POST` | `/api/v1/agents` | `{ "key", "display_name", "description" }` |
| `GET` | `/api/v1/agents/{key}` | `{ "agent": {} }` |
| `PATCH` | `/api/v1/agents/{key}` | Draft fields: name, description, `models`, `tool_names`, `knowledge_sources`, `asset_roles`, `workflow_key`, `trigger_key`, `approval_policy_id` |
| `POST` | `/api/v1/agents/{key}/disable` | Pause |
| `DELETE` | `/api/v1/agents/{key}` | Retire |
| `GET` | `/api/v1/agents/{key}/executions` | `{ "executions": [] }` |

Routes require `require_password_ok`. A user agent is visible only to its owner. Another user's key is 404. The reference agent is 403 on every write.

Configuration is process memory on `AgentBuilderService`. A restart clears user agents. The reference agent is registered from code.

Model fields are role, implementation name (`deepseek`, `openai`, `mock`), and model name. The only registered workflow is `instagram.reference`. The only registered approval policy is `instagram.still_image`. Tool names must be on `MCP_TOOL_ALLOWLIST`. Publishing tool names are rejected.

## Secrets

**IMPLEMENTED.** The builder never asks for an API key, token, or password.

A request that contains a secret-shaped key or value is rejected. The error message does not include the value. Provider keys stay in the process environment.

`frontend/src/lib/agentConfig.ts` discards a response that still contains a credential, and it removes publishing tool names before React state. The discarded value is not put in the error message.

## What a person cannot configure

- A new tool, a handler path, or a publish step that skips the Instagram Agent
- Another tenant's knowledge or assets
- A provider API key
- Removing an approval gate
- The Instagram studio's generate, approve, connect, or post screens

## Out of this UI

The studio task history stays on the existing task APIs. Builder executions are the test runs stored for that agent configuration.
