# Agent platform API

Related: [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Data model](AGENT_PLATFORM_DATA_MODEL.md), [Instagram reference agent](INSTAGRAM_REFERENCE_AGENT.md), [API reference](API/API_REFERENCE.md).

**IMPLEMENTED** as process-local configuration on the in-memory agent catalog. Routes live in `api/agent_routes.py`. This is the canonical agent API ([canonical components](PLATFORM_CANONICAL_COMPONENTS.md)). No Alembic revision was added. Instagram publishing routes are unchanged. `/api/v1/platform/agents` is a duplicate and is not the API new work should call.

Authentication is `require_password_ok`. The tenant id is the session user's id. Another tenant's agent key returns 404. The system agent `instagram_marketing` is visible to every tenant and cannot be updated, disabled, retired, or rolled back.

Provider configuration stores a role and an implementation name (`deepseek`, `openai`, `mock`). It does not accept API keys, access tokens, or other secrets. Request models use `extra: forbid`. Code fields (`handler`, `exec`, `script`, and the same family) are rejected. Clients cannot submit handler paths, Python callables, or workflow steps.

## Routes

| Method | Path | Effect |
| --- | --- | --- |
| GET | `/api/v1/agents` | System agent plus this tenant's agents. Retired agents are omitted |
| POST | `/api/v1/agents` | Create `{key, display_name, description?}`. Starts active with draft version 1 and no published pointer |
| GET | `/api/v1/agents/{agent_id}` | Agent, versions, and bindings. `agent_id` is the agent key |
| PATCH | `/api/v1/agents/{agent_id}` | Update the open draft: display name, models, tools, knowledge, assets, workflow, trigger, approval policy, timeout |
| DELETE | `/api/v1/agents/{agent_id}` | Retire the agent. The row stays readable to the owner. New runs are refused |
| POST | `/api/v1/agents/{agent_id}/versions` | Open the next draft from the published version. One draft at a time |
| POST | `/api/v1/agents/{agent_id}/validate` | `{valid, errors}` for the draft, or the published version when no draft is open |
| POST | `/api/v1/agents/{agent_id}/publish` | Freeze the draft and move the published pointer. With no draft, this re-enables a paused agent |
| POST | `/api/v1/agents/{agent_id}/disable` | Pause the agent. Test and trigger runs stop |
| POST | `/api/v1/agents/{agent_id}/rollback` | `{version_number}` selects an older published version of the same agent |
| POST | `/api/v1/agents/{agent_id}/clone` | `{key, display_name?}` copies the current draft or published bindings into a new agent for this tenant |
| GET | `/api/v1/agents/{agent_id}/executions` | This tenant's test runs for that agent |
| POST | `/api/v1/agents/{agent_id}/execute` | Test run. Mode is `test`. A draft cannot perform the external effect |

`POST /api/v1/instagram/publish` is not one of these routes. It still calls `PublicationGateway.enqueue_publication`.

## Configuration

A new agent starts from the registered Instagram reference bindings:

| Field | Allowed values |
| --- | --- |
| Workflow | `instagram.reference` only. Steps stay the server workflow |
| Trigger | A dotted key bound to that workflow. Default `reference.run` |
| Approval | `instagram.still_image`, which calls `decide_approval` |
| Tools | Names in `MCP_TOOL_ALLOWLIST`. Publishing names are rejected |
| Knowledge | `business`, `brand`, `product`, `document`, `festival`, `account`, `trend`, `content_rules` |
| Assets | Registered asset roles (`company_logo`, `product_image`, and the other catalog roles) |
| Models | `content_planning` is `deepseek`, `openai`, or `mock`. `image_generation` is `openai` or `mock`. `vision_qa` is `deepseek` or `mock` |
| Timeout | Greater than 0 and at most 3600 seconds. Default 120 |

The reference workflow's context and model steps must stay granted. `POST /validate` reports a missing source or role. `POST /publish` refuses that draft.

Published versions are immutable. `PATCH` after publish returns 409 until `POST /versions` opens a draft. Rollback changes the pointer only. It does not edit either version.

`instagram_marketing` cannot be used as a user agent key.

## Test execution

`POST /execute` always uses test mode. The model step reports the configured provider name and does not call DeepSeek or OpenAI. The publication handler records `published: false` and does not call `enqueue_instagram_publication`. A draft run stops at the external effect with step status `blocked`. A published run still has to pass `decide_approval`. A payload flag named `publish` is not an approval.

Paused agents return 409. Retired agents return 404. Executions are listed only for the tenant that ran them, including runs of the shared system agent.

## Storage

Definitions live in the process catalog created with the app. Restarting the process loads `instagram_marketing` from code again. User agents created through the API are not written to SQLite. No `agent_tasks` columns were added.

## Studio form

`api/agent_builder_routes.py` mounts a second API at `/api/v1/platform/agents`. It uses the same session tenant and the same secret and code rejection. It stores a **separate** process record: generated ids, provider and model names, tools, knowledge, assets, workflow, triggers, and approval. `GET /catalog` lists those registered names and does not copy API keys. An agent created here is invisible to `/api/v1/agents`.

That second API also has `POST /validate`, `POST /publish`, `POST /versions`, `POST /disable`, `POST /clone`, `POST /rollback`, `DELETE`, and `POST /execute`. Test execution does not call Meta.

The React client does **not** call `/api/v1/platform/agents`. `frontend/src/services/api/endpoints.ts` points the builder at `/api/v1/agents`. The pages call list, create, get, patch, disable, retire, and list executions. They do not call validate, publish, open draft, clone, rollback, or execute.
