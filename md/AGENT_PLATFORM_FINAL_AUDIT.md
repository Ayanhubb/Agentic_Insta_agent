# Agent platform final integration audit

Inspection date: 2026-09-28. The 2026-09-25 inspection remains the baseline. This update records the persistence, closed workflow, shadow mode, scheduler lock, and production JWT refusal that landed after it.

The generic runtime does **not** control Instagram publishing. Do not treat the platform as production-ready.

## Verdict

The Instagram studio still publishes only through `InstagramAgent`. Shadow mode can execute `instagram.reference`, record the run, and cannot publish. That path is not attached to `POST /api/v1/instagram/publish` or to `AutomationRunner`.

Agent configuration is durable in `agents`, `agent_versions`, and `agent_executions`. Published version snapshots are immutable. The React builder still talks to `/api/v1/agents` and does not drive shadow execution. Two agent APIs remain. The platform is not the live control plane.

## How this was checked

| Check | Command | Result |
| --- | --- | --- |
| Backend tests | `.venv\Scripts\python.exe -m pytest -q --tb=line` | Exit 0. 579 collected under the default marker filter (`not real_openai and not real_deepseek`). 575 passed, 4 skipped |
| Opt-in provider tests | `pytest.ini` `addopts` excludes `real_openai` and `real_deepseek` | 2 tests in `tests/test_real_openai.py` were not run. No `real_deepseek` tests exist |
| Frontend tests | `npm test` (`vitest run`) in `frontend/` | 14 files, 47 passed |
| Frontend typecheck and build | `npm run build` (`tsc -p tsconfig.app.json && vite build`) | Exit 0. `dist/` produced |
| Backend typecheck | — | **NOT_CONFIGURED**. `mypy` is not in `requirements.txt` and there is no `mypy.ini` |
| Frontend lint | — | **NOT_CONFIGURED**. `frontend/package.json` has no ESLint script or dependency |
| Backend lint | `.venv\Scripts\python.exe -m ruff check .` | **NOT_CONFIGURED** as a project gate: `ruff` is in `requirements.txt`, there is no `ruff.toml`, and there is no CI workflow. An unconfigured run reported 440 findings. See Linting |
| Migrations | Alembic `upgrade head` on an empty temporary SQLite database | Head `007_platform_agents`. Creates `agents`, `agent_versions`, and `agent_executions` |
| API routes | `create_app` OpenAPI | 117 operations. Duplicate operation-id warnings. See API routes |

The Anaconda base interpreter cannot collect tests (`ModuleNotFoundError: No module named 'openai'`). The project virtualenv can. That environment gap is not a product failure.

## Scoreboard

| # | Area | Status |
| --- | --- | --- |
| 1 | Platform architecture | PARTIAL |
| 2 | Agent domain model | PARTIAL |
| 3 | Workflow engine | PARTIAL |
| 4 | Agent runtime | PARTIAL |
| 5 | MCP / tool registry | PARTIAL |
| 6 | Provider registry | PARTIAL |
| 7 | Knowledge / assets | PARTIAL |
| 8 | Trigger / scheduler | PARTIAL |
| 9 | Approval / policy | PARTIAL |
| 10 | Agent Builder APIs | PARTIAL |
| 11 | React Agent Builder | PARTIAL |
| 12 | Instagram Reference Agent | PARTIAL |
| — | Publishing invariant | PASS |
| — | Security (overall) | PARTIAL |
| — | Studio schema migrations | PASS |
| — | Agent-platform persistence | IMPLEMENTED |
| — | Backend mypy | NOT_CONFIGURED |
| — | Frontend ESLint | NOT_CONFIGURED |
| — | Ruff project gate | NOT_CONFIGURED |
| — | Deployment as the agent platform | NOT_IMPLEMENTED |

`md/AGENT_PLATFORM_ARCHITECTURE.md` still marks agent configuration, a generic dispatcher, and a provider registry view as proposed or planned. Those packages now exist. The document is behind the tree, and the tree is not wired into `create_app`.

## 1. Platform architecture — PARTIAL

`api/app.py` `create_app` is the live process:

- JWT cookie or Bearer, session `jti` check, `require_password_ok` on studio and agent routes
- Owner-scoped SQLAlchemy repositories (`users.id` is the tenant)
- Content generation, approval, the campaign scheduler, and `POST /api/v1/instagram/publish` still call the existing studio functions
- `PublicationGateway` / `PublicationService` constructs `InstagramAgent`
- `scheduler_loop` starts only when `SCHEDULER_ENABLED` is true

The new packages (`agent_platform`, `agent_runtime`, `agent_builder`, `backend/platform`, `instagram_reference`, `ai/model_registry`) are imported by tests and by the two agent routers. `create_app` does not import `instagram_reference`, `TriggerScheduler`, `PlatformToolRegistry`, `PolicyEngine`, or `build_reference_runtime`.

What is live is the Instagram studio. The platform runtime can execute the reference workflow in shadow mode. It does not own the request.

**IMPLEMENTED** tenant boundary: `users.id`. The reference agent uses tenant id `system`.

**NOT_IMPLEMENTED**: Organization → users → agents.

## 2. Agent domain model — PARTIAL

`agent_runtime/models.py` defines agent, version, workflow pin, trigger binding, tool grant, and execution records. Statuses include active, paused, and retired. Versions are draft or published.

`instagram_reference/definition.py` registers system agent `instagram_marketing` as owner `system`, status active, published version 1. Tool grants are `MCP_TOOL_ALLOWLIST` and are disjoint from `INSTAGRAM_PUBLISHING_TOOLS`.

**IMPLEMENTED** tables, Alembic `007_platform_agents`: `agents`, `agent_versions`, `agent_executions`. Every row has `tenant_id`. A published snapshot cannot be replaced. The snapshot holds the workflow key, model names, tool names, knowledge sources, asset roles, approval policy, and trigger. It does not hold provider secrets. `AgentBuilderService` writes these rows when `create_app` passes the session factory. The runtime catalog is still `InMemoryCatalog`. `AgentBuilderStore` is still a second in-memory store and is not canonical.

`db/models.py` also has `AgentTask` and `AgentEvent` for the studio publisher. Those are not the platform agent tables.

## 3. Workflow engine — PARTIAL

Two engines exist, and neither replaced the studio call order.

| Engine | Role observed |
| --- | --- |
| `agent_runtime/engine.py` `WorkflowEngine` | Runs a pinned workflow for `AgentRuntime`. Gates external effects for draft versions. Used by builder test runs and by `instagram_reference.binding` |
| `agent_platform/workflow/engine.py` `WorkflowEngine` | Capability registry, structured conditions, secret and code-key rejection. Covered by `tests/test_workflow_engine.py`. Not mounted in `create_app` |

Live order is still `ContentOrchestrator`, `CampaignPipeline`, and `InstagramAgent._run_inner`.

**IMPLEMENTED**: `run_declared` resolves `agent_runtime.workflows`. `instagram.reference` runs the registered shadow workflow. Unknown keys, including `studio.generate` and `studio.approve`, fail with `INVALID_REQUEST`. Models cannot add a workflow key. `tests/test_platform_persistence.py` covers the registered run and the rejection.

## 4. Agent runtime — PARTIAL

`agent_runtime/runtime.py` and `agent_runtime/resolver.py` implement:

- Production and shadow modes require the published version pointer. A draft version returns `DRAFT_NOT_ALLOWED`
- Paused agents return `AGENT_PAUSED`. Retired agents return `AGENT_RETIRED`
- User-owned agents must match `trigger.tenant_id` and `trigger.user_id`
- External effects on a draft version return `DRAFT_CANNOT_PUBLISH` before the handler runs
- Tool, knowledge, and asset steps require a grant on the snapshot

`tests/test_agent_runtime.py` (46) and `tests/test_agent_runtime_boundaries.py` (1) passed.

The HTTP execute handlers do not use this production path. Both call `ExecutionMode.TEST` and register a stub publication handler that returns `published: false` (`agent_builder/service.py` `_test_publication`, `backend/platform/builder/runner.py` `_test_publication`). `agent_runtime` does not import `enqueue_instagram_publication` or `InstagramGraphClient`.

`instagram_reference/binding.py` `build_reference_runtime` still registers the real `instagram_agent_publish` handler. `dispatch` runs production mode. `create_app` never calls it.

**IMPLEMENTED** shadow path: `instagram_reference.shadow.run_shadow` loads the published SQL version, runs `AgentRuntime` in shadow mode, injects the canonical MCP client when a session factory is present, and registers `shadow_publication` instead of the enqueue handler. See [PLATFORM_SHADOW_MODE.md](PLATFORM_SHADOW_MODE.md). A default `ReferenceKnowledgeService` without that factory still does not load live MCP rows.

## 5. MCP / tool registry — PARTIAL

The live tool plane is `backend/mcp/`. `MCP_TOOL_ALLOWLIST` is 22 read tools. `assert_tool_allowed` rejects `INSTAGRAM_PUBLISHING_TOOLS` (the six Instagram agent tools plus aliases such as `publish`, `media_publish`, `publish_instagram_media`). Handlers take a server-built `TenantContext`. Model-supplied tenant keys are stripped.

`backend/platform/tools/` is a second registry. `ToolDefinition` refuses publishing names and an `instagram_publish` plane. `ToolExecutor.execute` checks the grant, classification, schema, timeout, and audit, and rejects publishing names before the handler. `tests/test_platform_tool_registry.py` (9) passed. `create_app` does not construct a `PlatformToolRegistry`, so the content agent and the studio MCP client do not go through it.

Canva remains a separate remote adapter (`backend/integrations/canva`). It is not on `MCP_TOOL_ALLOWLIST`. `tests/test_canva_execution.py` asserts the execution module does not publish.

## 6. Provider registry — PARTIAL

`ai/model_registry/registry.py` maps roles to implementations selected from process settings. It refuses publish-shaped calls. `tests/test_model_registry.py` (14) passed, including a check that the package does not reference the frontend, an Instagram client, `enqueue_instagram_publication`, or `graph.facebook.com`.

`create_app` still selects providers with `select_reasoning_provider` and `select_image_provider`. Keys stay in the process environment. There is no per-tenant provider-key column, which matches the architecture note and is also why the builder cannot store a customer key. The builder rejects secret-like keys and `sk-` / Bearer values before save.

The React models page sends role, implementation, and model name. It has no API-key field. `frontend/src/test/agents.test.tsx` covers that.

## 7. Knowledge / assets — PARTIAL

Live studio data is in SQL and is read through MCP: business profile, brand, products, festivals, account snapshots, trends, content rules. Path and secret-like keys are dropped by `sanitize_payload`. Asset reads check the owner. Foreign asset ids are denied. Path traversal is rejected in `tests/test_brand_assets.py`, `tests/test_media_storage.py`, `tests/test_platform_assets.py`, and `tests/test_generation_api.py`.

`backend/platform/knowledge/store.py` and `backend/platform/assets/store.py` are additional in-memory stores. They are not tables in the 29-table schema. `PlatformKnowledgeService.load_for_tenant` calls a live MCP tool only when an invoker was injected. The reference runtime's default service does not inject one.

`GET /api/v1/media/{filename}` is unauthenticated so Meta can fetch a prepared image. `resolve_public_file` rejects `../`. Generated images and brand files are not on that path. `GET /api/v1/media/generated/{image_id}` requires the session user and an owned row.

## 8. Trigger / scheduler — PARTIAL

The process scheduler is `scheduler/scheduler.py`. The lifespan starts it only after `SchedulerLeader` holds an exclusive file lock. A second worker on the same machine does not tick. A lock across hosts is **NOT_IMPLEMENTED**. See [SCHEDULER_DEPLOYMENT_MODEL.md](SCHEDULER_DEPLOYMENT_MODEL.md). The loop is started from the FastAPI lifespan. The campaign pipeline calls `PublicationService.publish_generated_image`, which constructs `InstagramAgent`. `scheduler/pipeline.py` does not construct `InstagramGraphClient` and does not contain `media_publish`. `tests/test_trend_scheduler.py` checks the trend scheduler source for those tokens.

`agent_platform/triggers/scheduler.py` `TriggerScheduler` is a separate ticker. Its module docstring says a tick never publishes. `tests/test_trigger_platform.py` (18) passed, including a source scan for `enqueue_instagram`, Graph hosts, and `eval` / `exec`. `create_app` does not start this scheduler. Builder trigger names (`manual`, `http.generation`, `scheduler.daily`, and the rest) are stored on the in-memory version. They do not bind the live runner.

Webhooks are not implemented. That matches the trigger package, which skips event and webhook kinds.

## 9. Approval / policy — PARTIAL

Studio approval is `scheduler/approval_policy.py` `decide_approval`, used by the campaign pipeline and by `InstagramStillImagePolicy`. The policy returns not allowed when a plan names a publish tool. It does not call Meta.

`agent_platform/policy` and `agent_platform/approval` reject secret keys, code keys, tenant keys supplied as facts, and blocked capability names including the publishing tools and `enqueue_instagram_publication`. `tests/test_policy_approval.py` (13) passed. These engines are not on the `POST /api/v1/instagram/publish` path.

The React-facing builder accepts only policy id `instagram.still_image`. The platform-agent catalog also offers `human.required`. The two APIs do not share that list.

## 10. Agent Builder APIs — PARTIAL

`create_app` mounts two routers that do not share a store:

| Router | Store on `app.state` | Shape |
| --- | --- | --- |
| `/api/v1/agents` | `agent_builder` → `AgentBuilderService` | `{agents, agent, executions}` with `key` |
| `/api/v1/platform/agents` | `platform_agents` → `AgentBuilderStore` | `{items}` with `id` |

Both require `require_password_ok`. A missing or other-tenant agent is not found. The system agent cannot be edited. Publish tool names are rejected. Execute is test mode and does not call Meta. Paused and retired agents are refused. Draft versions can run only as a test, and draft external effects are blocked.

`/api/v1/agents` is what the React client calls. It can create, patch, disable, retire, validate, open a draft, publish a version, clone, rollback, list executions, and execute. `/api/v1/platform/agents` repeats that surface with a catalog and per-section PUT routes, against a different memory store. An agent created on one route is invisible to the other.

Neither store is durable.

## 11. React Agent Builder — PARTIAL

Routes under `/agents` are behind `ProtectedRoute`. Pages cover list, create, overview, models, tools, assets, knowledge, triggers, workflow, approval, and executions.

`frontend/src/services/api/agents.ts` calls `/api/v1/agents` for list, create, get, patch, disable, retire, and list executions. It does not call validate, publish version, open draft, clone, rollback, or execute. It does not call `/api/v1/platform/agents`.

The tool and workflow lists in `frontend/src/lib/agentCatalog.ts` are a local copy. They are not loaded from a catalog endpoint. `get_publishing_history` is a read tool name. Publishing tool names are stripped in `frontend/src/lib/agentConfig.ts` before render. `frontend/src/test/agents.test.tsx` (8, included in the 47) checks that a secret field is absent and that `publish_instagram_media` is not offered.

The executions table expects `execution_id` and `workflow_key`, which matches `AgentBuilderService`. The other API returns `id` and `workflow_id`. The UI never asks that API.

The builder does not call `POST /api/v1/instagram/publish`. The studio page `frontend/src/pages/InstagramPage.tsx` does. That page is the existing client of the Instagram agent route, not a second publisher.

## 12. Instagram Reference Agent — PARTIAL

`instagram_reference/definition.py` fixes a 13-step workflow. One step is an external effect, handler `instagram_agent_publish`, step key `instagram_agent_publication`. Verification is not an effect. Tool grants cannot include publishing tools. `assert_publish_boundary` runs at registration.

`instagram_reference/handlers.py` is the only module in that package that imports `agent.instagram_tasks`. `instagram_agent_publish` calls `enqueue_instagram_publication` after approval and an image path. It does not construct a Graph client. `meta_verification` reads the agent result and does not call Meta. `tests/test_instagram_reference_agent.py` (5) passed, including a model plan that names `publish_instagram_media` and a source scan that the Graph client is absent from the reference package, the runtime package, the orchestrator, the creative model, and DeepSeek.

`api/routes.py` still owns `POST /api/v1/instagram/publish`. `instagram_reference` is not referenced by `api/app.py` or `api/routes.py`. The reference workflow is not what runs when a person approves an image or the scheduler fires.

## Publishing invariant — PASS

Only `InstagramAgent` calls Meta. The Graph publish request is `POST /{ig-user-id}/media_publish` in `tools/instagram_media.py`, reached through `InstagramAgent`.

Callers of that agent, observed in source:

| Caller | What it does |
| --- | --- |
| `POST /api/v1/instagram/publish` | Builds `InstagramAgent` from `create_app` |
| `PublicationService.publish_generated_image` and `enqueue_instagram_publication` | Construct `InstagramAgent` |
| `scheduler/pipeline.py` | Calls `publish_generated_image`. No Graph client in that file |
| `instagram_reference/handlers.py` `instagram_agent_publish` | Calls `enqueue_instagram_publication`. Not mounted on HTTP |

Components that must not gain a publisher:

| Component | Status | Evidence |
| --- | --- | --- |
| Content Agent | PASS | `agent/content_orchestrator.py` keeps `published: Literal[False]`. No `InstagramGraphClient`. `tests/test_content_orchestrator.py` asserts `media_publish` is absent |
| DeepSeek | PASS | `ai/llm/deepseek.py` has no Graph client. `tests/test_deepseek.py` and the reference-agent source scan |
| OpenAI | PASS | Image generation stores pixels. `tests/test_backend_openai_image.py` and `tests/test_ai_providers.py` reject publish tool names and `graph.facebook.com` in the provider package |
| Canva | PASS | `backend/integrations/canva/execution.py` does not publish. `tests/test_canva_execution.py` |
| MCP | PASS | Allowlist and `ToolNotAllowed`. Platform registry refuses the same names. `tests/test_mcp.py`, `tests/test_platform_tool_registry.py` |
| Scheduler | PASS | No Graph client in `scheduler/pipeline.py`. A publish happens only by calling `PublicationService`, which runs `InstagramAgent`. The new `TriggerScheduler` does not publish and is not started |
| Trend Intelligence | PASS | Trend MCP reads stored observations. `tests/test_trend_research.py`, `tests/test_trend_analysis.py`, `tests/test_trend_scheduler.py` assert `media_publish` is absent |
| React | PASS | Builder pages do not call the publish route. `frontend/src/lib/agentConfig.ts` drops publishing tool grants. The studio Instagram page posts to the existing agent route |
| Agent runtime | PASS | Package source has no `enqueue_instagram_publication` and no Graph client. Builder execute stubs the publish handler. Draft effects are blocked |

A model-generated tool name does not become a call. `ReferenceToolService.invoke` and `assert_tool_allowed` reject publishing names. `mentions_publish_tool` stops the reference publish step when a plan names one. `tests/test_instagram_reference_agent.py` `test_publish_tool_names_never_reach_the_gateway` left the publication spy empty.

## Security

Overall: **PARTIAL**. The studio and the new builders refuse cross-tenant ids and publishing tools. Production auth defaults are weak, and there is no CSRF token.

| Control | Status | What the source does |
| --- | --- | --- |
| Tenant isolation | PASS | Session user id is the tenant. Repositories and MCP use that id. Builder stores key records by user id and return not found for another tenant. `tests/test_instagram_credential_isolation.py`, `tests/test_auth.py`, platform asset and knowledge tests |
| Secret handling | PARTIAL | Instagram tokens are Fernet-encrypted. Logs and MCP payloads redact secret-like keys. Builder schemas and version snapshots reject secret keys and token-shaped strings. Provider keys stay in the process environment and are not stored on `agent_versions`. `jwt_cookie_secure` still defaults false |
| JWT | PARTIAL | HS256, `exp`, `jti` checked against `sessions`. `Settings.from_env` refuses a known or short `JWT_SECRET` when `APP_ENV` is production or prod, including `dev-change-me` and `docker-dev-jwt-secret-change-me-32b`. Direct `Settings()` construction, which tests use, does not apply that check. Cookie `Secure` is still opt-in |
| Cookies | PARTIAL | `auth/service.py` sets `access_token` `httponly`, `samesite=lax`, `path=/`. `secure` follows `JWT_COOKIE_SECURE`, default false, because the current HTTP deployment would drop a `Secure` cookie. The login response still returns `access_token` in the JSON body because the SPA stores it. That body token is **PARTIAL** |
| CSRF | PARTIAL | No CSRF token and no CSRF middleware. `SameSite=Lax` blocks a typical cross-site POST from another site. Cookie-authenticated mutations still have no dedicated CSRF check. CORS allows credentials for the configured origin list, not `*` |
| Tool authorization | PASS | Allowlist, per-agent grants, read vs write classification, trusted tenant required, model tenant keys stripped |
| Arbitrary code execution | PASS | Policy, workflow, and builder payloads reject `eval`, `exec`, `subprocess`, and callable values. No path executes model text as code. Ruff reports `ApprovalDecision` as an undefined name in `agent_runtime/services.py` line 56. That file has `from __future__ import annotations`, the name is only a protocol annotation, and the runtime tests passed. It is not an execution bug |
| Prompt injection boundaries | PARTIAL | Tenant ids, secret keys, and tool names are structural and rejected or stripped. Free-text prompts and captions still reach the model and, after approval, the Instagram agent. That is the product. A plan that names a publish tool does not get a tool call |
| Model-generated tool names | PASS | Unknown names and publishing names fail before a handler runs |
| Asset path traversal | PASS | Storage helpers and the public media resolver reject `..`, absolute paths, and null bytes. Covered by the asset and media tests cited above |
| Unauthorized agent execution | PASS | Agent routes depend on `require_password_ok`. No session means the route does not run the builder |
| Draft agent execution | PASS | Production resolution rejects drafts. Test execution is allowed and cannot publish: the effect gate and the stub handler both apply |
| Disabled agent execution | PASS | Disable sets `paused`. Execute returns conflict. Retire is hidden as not found |
| Cross-tenant access | PASS | User-owned agents require the same user id on the definition and the trigger. Studio asset, post, and Instagram account reads are owner-scoped. Publishing with another user's account returns `PERMISSION_ERROR` in `PublicationService` |
| Publishing boundary | PASS | See the invariant section. User-built agents cannot be granted publishing tools. Builder execute does not call Meta |

`GET /api/v1/admin/users` is registered twice. `api/auth_routes.py` `admin_router` is mounted first and lists all users. `api/platform_routes.py` lists active users and is the duplicate OpenAPI warned about. Both handlers require an admin. This is a route defect, not a tenant bypass.

`api/instagram_account_routes.py` is not mounted. Connect and disconnect are on `api/platform_routes.py`.

## API routes — PARTIAL

OpenAPI from `create_app` listed 117 operations, including both agent prefixes. FastAPI warned:

- Duplicate operation id `admin_users` (`api/platform_routes.py` and `api/auth_routes.py`)
- Duplicate operation ids for `GET`+`HEAD` pairs on `/api/v1/media/{filename}` and `/api/v1/generation/{image_id}/media` (one function, two methods)

Unauthenticated mutations observed in the route list are the auth endpoints (`register`, `login`, `logout`) and no others in the filtered set. `GET /api/v1/health` and `GET|HEAD /api/v1/media/{filename}` are public by design. Agent execute routes are present on both prefixes and require the session user.

## Linting

Ruff is installed in the project virtualenv because `requirements.txt` lists it. The repository has no Ruff configuration and no GitHub workflow, so there is no project lint gate. Status of the gate: **NOT_CONFIGURED**.

`ruff check .` with Ruff defaults reported **440** findings. The largest groups were `B008` (183, function call in a default argument), `I001` (79, import order), `BLE001` (30, blind except), and `F401` (27, unused import). One `F821` is the annotation noted above. These were not treated as a demonstrated runtime defect, and application code was not edited to clear them.

## Migrations — PASS for the studio schema

Chain, single head: `001_initial_schema` → `002_brand_assets` → `003_canva_connections` → `004_generated_image_qa` → `005_trend_research` → `006_trend_intelligence` → `007_platform_agents`.

`upgrade head` on empty SQLite completed and created `agents`, `agent_versions`, and `agent_executions`.

`create_app` calls `init_db`, which is `Base.metadata.create_all`. It does not run Alembic. `create_all` creates the new tables on a fresh database and does not alter an old one. An existing database still needs `alembic upgrade head`.

Platform knowledge documents, platform asset records, and trigger state are **NOT_IMPLEMENTED** as their own tables. Bindings stay inside the version snapshot.

## Remaining gaps

- The generic runtime does not own `POST /api/v1/instagram/publish` or the scheduler. Shadow mode is a separate call.
- Two agent APIs remain. `AgentBuilderStore` is still in memory. The React client uses `/api/v1/agents`.
- The React client cannot publish a version, open a draft, validate, clone, rollback, or execute.
- Shadow mode is not started automatically beside a studio request.
- A scheduler lock across more than one host is not implemented.
- Cookie `Secure` still follows `JWT_COOKIE_SECURE` (default false). Login JSON still includes `access_token`.
- No CSRF token.
- Duplicate `GET /api/v1/admin/users`.
- `md/AGENT_PLATFORM_ARCHITECTURE.md` describes work that the code has since started, and it does not describe the dual API.
- Ruff is not a configured gate and is not clean.
- Backend mypy and frontend ESLint are not configured.
- Real OpenAI tests are opt-in and were not run. There is no real DeepSeek test module.

## Deployment readiness

**Not ready as the control plane.** The Instagram studio can keep serving generation, approval, and Meta publish through `InstagramAgent`. Shadow mode proves the registered workflow without publishing. It does not replace that studio path.

## Production blockers

1. The generic runtime does not control live Instagram execution. Shadow mode is not on the publish route or the scheduler.
2. `/api/v1/agents` and `/api/v1/platform/agents` are still two products. The UI binds to the first and omits publish, execute, and draft management.
3. `JWT_COOKIE_SECURE` defaults false. Login JSON still returns the bearer token. `Settings.from_env` now refuses a known JWT default in production; an existing deployment must set a unique `JWT_SECRET` of at least 32 characters.
4. The app bootstraps with `create_all` and will not migrate an existing database. Production still needs `alembic upgrade head` before serve. Head is `007_platform_agents`.
5. More than one host can still run two scheduler loops. The file lock covers one machine only.

## Totals

| Item | Result |
| --- | --- |
| Backend tests | 575 passed, 4 skipped, 579 collected by default pytest. 2 `real_openai` tests not run. Exit 0 |
| Frontend tests | 47 passed. Exit 0 |
| Build | Passed. `tsc -p tsconfig.app.json` and Vite build. Exit 0 |
| Migration | Passed. Head `007_platform_agents`. Adds `agents`, `agent_versions`, `agent_executions` |
| Security | PARTIAL. Tenant, tool, draft, disabled, and publishing checks hold. Production `from_env` refuses a known JWT default. Cookie `Secure` is still opt-in, login JSON still returns the token, and there is no CSRF token |
| Remaining gaps | Listed above. Shadow mode works and cannot publish. The platform is not the studio's control plane |
| Deployment readiness | Not ready as an agent platform |
| Production blockers | Runtime does not own live publish, split agent APIs, cookie Secure and login body token, Alembic not applied by `create_app`, scheduler lock is single-host |
