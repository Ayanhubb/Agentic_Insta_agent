# Platform shadow mode

Status date: 2026-09-28.

| Piece | Status |
| --- | --- |
| Execute the registered `instagram.reference` workflow | **IMPLEMENTED** |
| Load the published agent version | **IMPLEMENTED** |
| Record `agent_executions` | **IMPLEMENTED** |
| Compare a supplied studio preview | **IMPLEMENTED** when the payload includes `studio_preview` |
| Publish to Instagram | **NOT_IMPLEMENTED** on this path, on purpose |
| Call Meta | **NOT_IMPLEMENTED** on this path, on purpose |
| Replace `POST /api/v1/instagram/publish` | **NOT_IMPLEMENTED** |
| Run beside every studio request automatically | **NOT_IMPLEMENTED** |
| Production cutover of automatic publishing | **NOT_IMPLEMENTED** |

Shadow mode is a second execution of the declared workflow. The existing studio path is unchanged.

```
Existing Instagram Studio
        │
        ├── existing execution          IMPLEMENTED (unchanged)
        │
        └── Generic Agent Runtime       IMPLEMENTED as run_shadow
                 │
                 └── shadow execution   IMPLEMENTED
                        │
                        └── NO PUBLISH  IMPLEMENTED
```

`run_shadow` is not called from the publish route or from `AutomationRunner`. A studio request does not start a shadow run by itself.

## What a shadow run does

`instagram_reference.shadow.run_shadow` and `backend.platform.builder.runner.run_declared`:

1. Load the durable agent. The shared reference agent is `instagram_marketing` with tenant id `system`. A user agent is loaded with the caller's tenant id.
2. Refuse a retired agent, a paused agent, and an agent with no published version.
3. Require the published snapshot workflow key `instagram.reference`. Any other key fails.
4. Build `AgentRuntime` with the existing reference services: knowledge, MCP tools when a session factory is present, DeepSeek/OpenAI model roles through `ReferenceModelService`, and `InstagramStillImagePolicy`.
5. Register `shadow_publication` for the publication step. That function returns `published: false` and does not import `enqueue_instagram_publication`.
6. Run mode `shadow`, which uses the same published-version rule as production.
7. Store an `agent_executions` row with `execution_id`, `tenant_id`, `agent_id`, `agent_version_id`, `workflow_key`, `status`, `mode`, `started_at`, `completed_at`, and error fields.
8. If the payload contains `studio_preview`, store a comparison of the planned and published flags. The shadow published flag is false.

Empty provider credentials still use the existing mock factories. The snapshot stores provider and model names. It does not store API keys.

## No-publish guarantee

- `shadow_publication` does not call `enqueue_instagram_publication` and does not construct `InstagramGraphClient`.
- The execution output records `published: false`.
- Another tenant cannot read the execution row.
- `tests/test_platform_persistence.py` patches the enqueue function and `InstagramGraphClient.__init__` and asserts neither is called.

The real publish handler remains `instagram_reference.handlers.instagram_agent_publish`. `build_reference_runtime` still registers it. HTTP publish and the scheduler do not call that runtime. `InstagramAgent` remains the only Meta publisher.

## Tenant

**IMPLEMENTED**: the caller user id is the execution tenant. The reference definition is tenant `system`.

**NOT_IMPLEMENTED**: organizations and teams.
