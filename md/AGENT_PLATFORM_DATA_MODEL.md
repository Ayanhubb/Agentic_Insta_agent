# Agent platform data model

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Related: [Concept](AGENT_PLATFORM_CONCEPT.md), [Architecture](AGENT_PLATFORM_ARCHITECTURE.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md), [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md), [Runtime](AGENT_PLATFORM_RUNTIME.md), [MCP](AGENT_PLATFORM_MCP.md), [Providers](AGENT_PLATFORM_PROVIDER_MODEL.md), [Security](AGENT_PLATFORM_SECURITY.md), [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md), [Migration](AGENT_PLATFORM_MIGRATION_PLAN.md), [Decisions](AGENT_PLATFORM_DECISIONS.md), [Schema](DATABASE/DATABASE_SCHEMA.md).

Proposal date: 2026-09-25. Inspected git HEAD `a63569e` and `db/models.py`. This page defines the generic domain. It does not add tables, columns, routes, or ORM classes.

## Implementation status

**No SQLAlchemy models are added with this page.**

The architecture page describes boundaries and does not add tables. [Migration](AGENT_PLATFORM_MIGRATION_PLAN.md) phase 0 is documentation. Phase 1 is an in-code catalog and has no Alembic revision. Phase 3 may add nullable `agent_id` and `workflow_id` on `agent_tasks` only after a dispatcher stores an agent id. The same plan says: do not add agent-definition tables until a user can create an agent; the system catalog can live in code.

That condition is not met. `instagram_marketing` is still the code that already runs, not a row a user inserts. User-owned agents are **PROPOSED** and come after the system record exists. Creating `platform_*` tables, or altering `agent_tasks`, would get ahead of that order.

Instagram publishing modules and Instagram tables are unchanged. `instagram_accounts`, `instagram_posts`, `generated_images`, and the six-tool runtime stay as they are. No generic foreign key is added onto those tables.

Tests for ownership and uniqueness are not added, because no new persistence was implemented. The assertions a later phase must include are listed under [Tests when tables exist](#tests-when-tables-exist).

## Status of each entity

| Entity | Home today | Later persistence |
| --- | --- | --- |
| Agent | Python classes only. No agent table | Code catalog for `instagram_marketing`. Rows only when a user can create an agent |
| AgentVersion | Integer on the definition. Version 1 is today's call graph | Same catalog. A second version does not require a migration until it exists |
| AgentConfiguration | Existing owner-scoped rows | Same tables. No parallel copy of business, brand, products, tokens, or automation flags |
| Workflow | Call order in Python | Catalog module in phase 1. Not a user-authored graph |
| WorkflowVersion | Implied version 1 | Catalog. Immutable once published by code review |
| WorkflowStep | Steps inside those functions | Catalog records. Handler paths are reviewed code |
| ToolBinding | `MCP_TOOL_ALLOWLIST` and the separate planes | Grant list on the agent version. Not a second tool registry |
| ModelBinding | Env factories | Role names on the agent version. No key columns |
| KnowledgeSource | Owner-scoped tables and the festival catalog | Source name on the agent version. No new store |
| AssetBinding | `business_assets`, `product_assets`, `generated_images` | Role grant. Files stay in those tables |
| Trigger | HTTP routes, `POST /automation/run-now`, `scheduler_loop` | Name of an existing entry point |
| ApprovalPolicy | `decide_approval` | Id `instagram.still_image`. Not a user-editable document |
| Execution | `agent_tasks` | Same table. Optional nullable labels later |
| ExecutionStep | `agent_tasks.current_step` and `agent_events` | Same rows. No `execution_steps` table |
| Task | `agent_tasks` | Same row as Execution |
| AgentEvent | `agent_events` | Same table. Append for a live run |

## Tenancy and ownership

**IMPLEMENTED.** The tenant is `users.id` ([ADR-009](AGENT_PLATFORM_DECISIONS.md)). There is no organization table. New user-owned rows use `user_id`. A miss is 404, including another user's task. `TenantContext` sources are `authenticated`, `scheduler`, and `system`. Model arguments never supply the tenant.

`canva_connections` already stores both `tenant_id` and `user_id`. That exception stays on Canva. Platform entities do not copy it. They use `user_id`.

Two owner kinds:

| Kind | `user_id` | Who may change it | Who may run it |
| --- | --- | --- | --- |
| `system` | null | Registration in reviewed code. Not a user API | Any authenticated tenant, or the scheduler for that tenant. The run's `agent_tasks.user_id` is that tenant |
| `user` | `users.id` | That user, after the control plane checks every referenced id | That user only. Another user's id returns 404 |

Multiple agents per tenant means many user-owned agents with the same `user_id` and different keys. It does not mean a private copy of `instagram_marketing` per user. The reference agent stays one system definition. Tenants share it. Their business profile, brand, products, assets, Instagram account, Canva connection, and `automation_settings` stay one set of rows per user, resolved at run time.

A user-owned agent may only reference workflows, tools, knowledge sources, and policies that are already registered. It may not add a tool name, a Python callable, or a Graph host. It may not read another tenant's knowledge or assets. There is no share-across-tenants operation.

Authentication stays `users` and `sessions`. Agent routes, when they exist, use `require_password_ok` and the session user. This model does not add an API-key principal.

## Versioning, draft, published, rollback

The accepted agent lifecycle is `active`, `paused`, and `retired`. There is no draft flag on the agent itself. Draft and published are version states.

| Object | States | Rule |
| --- | --- | --- |
| Agent | `active`, `paused`, `retired` | Triggers dispatch only when status is `active` and `published_version_id` is set. `paused` returns a configuration error and does not publish. In-flight Instagram tasks finish under `agent/recovery.py`. `retired` is hidden from new runs. Historical `agent_tasks` remain |
| AgentVersion | `draft`, `published` | A draft is mutable. Publishing freezes it. At most one draft per agent. Many published versions may exist. Triggers use the agent's published pointer, not "the highest version number" |
| WorkflowVersion | `published` in the catalog | Users do not draft workflow graphs. A new catalog version is a reviewed code change. Optional steps that already exist (Canva, auto-publish flags) are configuration, not new nodes |

`instagram_marketing` is registered directly as `active` with published version `1`. Pause and retire of that system agent are not part of the first registration. Pausing it would turn off the studio.

A user-owned agent is inserted as `active` only after the control plane checks that every referenced id is registered and that the tool grant does not include `INSTAGRAM_PUBLISHING_TOOLS` or the publish aliases MCP already rejects. Until those checks pass, there is no runnable pointer. A holding row, if a later API needs one, stays `paused` with `published_version_id` null. That is not a fourth lifecycle name.

Rollback sets `published_version_id` to an older **published** version of the **same** agent. It does not edit that version, delete later versions, or rewrite `agent_tasks`. A run copies the version numbers it started with and keeps them. In-flight work is not migrated onto the restored version. Rollback cannot point at another tenant's version, a draft, or a version whose tool grant fails the same checks.

Publishing a draft replaces the pointer in one transaction: version status becomes `published`, `published_at` is set, and the agent pointer moves. A second publish of an already published version is a no-op that returns the existing pointer.

## Idempotency

**IMPLEMENTED** uniqueness the studio already relies on. The platform does not replace these with a generic key:

| Mechanism | Uniqueness | Effect |
| --- | --- | --- |
| `daily_post_slots` | `(user_id, instagram_account_id, local_date)` | A scheduler tick cannot double-claim a local date |
| `instagram_posts` partial unique index `uq_ig_posts_daily_published` | `(user_id, instagram_account_id, scheduled_date)` where `status = PUBLISHED` and `post_type = DAILY_RETAIL_POST` | One published daily post for that account and date |
| `festival_campaigns` | `(user_id, festival_name, year)` | One campaign |
| `festival_posts` | `(campaign_id, sequence_number)` | One sequence slot |
| `scheduled_jobs` | `(user_id, instagram_account_id, job_type)` | One job row per type |
| `automation_settings` | `user_id` | One settings row |
| `business_profiles`, `brand_profiles` | `user_id` | One profile |
| `users.email` | email | One account |
| Trend tick | A report for the local day already exists | Skip another `trend.cycle` collect for that day |
| Publish recovery | `skip_publish`, certainty `SUCCEEDED`, or an existing media id | Do not publish again |

`agent_tasks.id` is the caller-supplied task id (`String(64)`), not a database default. `AgentTaskRepository.upsert` updates the row when that id exists. Reusing an id continues that task. It does not insert a second execution. That is the publish path's identity. It is not a license to rewrite a completed run's event history.

`AgentEventRepository.append` adds a row. `replace_for_task` deletes and rewrites events for a task. That helper stays an Instagram persistence detail. Platform execution history for a completed run is append-only. New platform code does not call `replace_for_task` to edit a finished execution.

**PROPOSED**, and not added now: nullable `idempotency_key` on `agent_tasks`, unique on `(user_id, idempotency_key)` where the key is not null. A replay with the same key returns the existing task. The key does not override `skip_publish` or the daily slot. Generation requests that do not create `agent_tasks` today stay that way, so they do not gain a key in the first registration.

## Execution history

History is `agent_tasks` plus `agent_events`. Retiring an agent does not delete those rows. Old rows may have a null agent label. Readers do not require the label.

A run that creates a task records, when the columns exist:

- tenant: `user_id`
- `task_type` and `trigger` (already required)
- agent key and agent version number started with
- workflow key and workflow version number started with
- status, `current_step`, redacted `error`

Studio generation that only writes `generated_images` does not gain a forced task row.

Publish progress remains `GET /api/v1/tasks/{task_id}`, the SSE stream, and `GET /api/v1/tasks/{task_id}/owned`. Another user's task is 404.

Definition changes (publish a version, pause, retire, rollback) are not `agent_events`. Those rows feed the task SSE stream and carry tool transitions. Definition audit goes through `log_step` with `agent_id`, `workflow_id` when known, `tenant_id`, and `status`. It does not log prompts, tokens, or storage keys. A queryable audit table is out of scope until a user can mutate a definition through an API. Observability stays the current channels ([ADR-013](AGENT_PLATFORM_DECISIONS.md)).

## What is not stored

- Provider API keys, `JWT_SECRET`, `TOKEN_ENCRYPTION_KEY`, Graph tokens, Canva tokens. Instagram tokens stay in `instagram_accounts.access_token_encrypted`. Canva tokens stay in `canva_connections`.
- User-supplied Python, shell, HTTP tools, or handler paths.
- Embeddings, document corpora, or a second blob store.
- An organization id.
- A second copy of `TaskStatus` (`pending` through `verifying`). Those values belong to the Instagram publish runtime.
- Workflow expressions or free-form node graphs.

Foreign keys below are the contract for a later migration. They are not created here.

---

## Agent

**Purpose.** Stable identity of a binding: which published version triggers may run. The binding is data. The behavior is a reviewed handler. `InstagramAgent`, `ContentOrchestrator`, `ContentAgent`, and `DeepSeekTrendAnalyst` are not this entity.

**Ownership.** `system` for `instagram_marketing`. `user` for a later agent, owned by one `users.id`.

**Tenant.** System agents have no tenant. User agents use `user_id`. Runs always stamp the acting tenant on `agent_tasks.user_id`.

**Lifecycle.** `active` → `paused` → `active`, or `active` → `retired`. `retired` is terminal for new runs. The system reference agent is inserted as `active`. Pause and retire of that agent are excluded from the first registration.

**Immutable.** `id`, `key`, `user_id` (null for system).

**Mutable.** `display_name`, `status`, `published_version_id`, `updated_at`.

**Versioning.** The agent row is not versioned. `published_version_id` selects an `AgentVersion`. Rollback changes only that pointer.

**Status.** `active`, `paused`, `retired`.

**Foreign keys.** `user_id` → `users.id` ON DELETE CASCADE when not null. `published_version_id` → AgentVersion, nullable, ON DELETE RESTRICT (a published version that is the current pointer cannot be deleted).

**Indexes.** `user_id`. `(user_id, status)` for listing a tenant's runnable agents.

**Uniqueness.** `key` unique among system rows (`user_id` is null). `(user_id, key)` unique among user rows. Two tenants may use the same key. One tenant may not. `instagram_marketing` is reserved and cannot be inserted with a non-null `user_id`.

**Audit.** Registration, pause, retire, and pointer changes through `log_step`. No secrets. Historical tasks remain after retire.

**Physical.** Not a table yet. Phase 1 catalog constant: key `instagram_marketing`, owner `system`, version `1`, status `active`.

## AgentVersion

**Purpose.** Immutable snapshot, once published, of workflow pins, tool grants, model roles, knowledge names, asset roles, triggers, approval policy id, and the execution-policy citation.

**Ownership.** Same owner as the parent Agent. A version cannot be moved to another agent or another user.

**Tenant.** Inherited from the agent. A user version is visible only to that `user_id`. System versions are readable as catalog. They are not writable by a tenant.

**Lifecycle.** Created as `draft`. Publish freezes the row and moves the agent pointer. A published version stays `published` after rollback. It is not deleted while any `agent_tasks` row cites its number, and it is not deleted while it is the current pointer.

**Immutable.** `id`, `agent_id`, `version_number`, `created_at`. After publish: every binding, the execution-policy citation, `published_at`.

**Mutable.** While `draft` only: the bindings and the execution-policy citation. `status` and `published_at` change once, at publish.

**Versioning.** Integer `version_number` starting at 1, unique per agent, increasing by 1. A run copies this integer. Version 1 of the reference agent means today's call graph. This proposal does not require a migration until a second version exists.

**Status.** `draft`, `published`.

**Foreign keys.** `agent_id` → Agent ON DELETE CASCADE for drafts of a deleted user agent. ON DELETE RESTRICT if any published version is still cited by the pointer. Prefer: user deletion cascades the agent and its versions; task rows keep a copied key and number, not a foreign key, so history survives.

**Indexes.** `(agent_id, version_number)`. Partial index on `agent_id` where `status = draft`.

**Uniqueness.** `(agent_id, version_number)`. At most one `draft` per `agent_id`.

**Audit.** Publish records actor `user_id` or `system`, agent key, version number, and the new pointer. The snapshot itself is the audit of what ran. Bindings must not contain tokens.

**Execution policy on the version.** A citation of knobs that already exist, not a second retry policy. The reference citation is `AGENT_TIMEOUT_SECONDS` default 120, MCP client timeout default 5 seconds, plan and QA attempt caps of 3, storage retries 3, verification retries 5. Changing publish retries means changing `RecoveryPolicy`, which stays Instagram-specific. The version does not store a callable.

## AgentConfiguration

**Purpose.** The tenant's data and flags the reference agent already reads. It is not a second copy of the agent definition.

**Ownership.** The session user. The system definition is not configurable into a different publisher.

**Tenant.** `user_id` on each existing row. Resolution uses owner-scoped repositories.

**Lifecycle.** Follows the existing rows. Connect, disconnect, upload, and `PUT /automation` stay the writers. Phase 5, when it exists, writes those same tables.

**What the entity includes today.**

| Concern | Row | Uniqueness already enforced |
| --- | --- | --- |
| Business facts | `business_profiles` | `user_id` |
| Brand and guidelines | `brand_profiles`, `brand_guidelines` | brand profile `user_id` |
| Products and offers | `products`, `product_assets` | `(user_id, sku)` when sku is set; `(product_id, asset_id)` |
| Files | `business_assets` | owner check; scope and role check constraints |
| Instagram account | `instagram_accounts` | `(user_id, instagram_account_id)` |
| Canva connection | `canva_connections` | `(tenant_id, user_id)` |
| Scheduler flags | `automation_settings` | `user_id` |
| Job rows the runner does not require | `scheduled_jobs` | `(user_id, instagram_account_id, job_type)` |

**Immutable.** Identity columns on those tables (`id`, `user_id`). Encrypted token columns are replaced only by reconnect, never returned.

**Mutable.** The fields those routes already update. `daily_posts_per_day` stays forced to 1 when a client sends it. Auto-publish flags may be toggled. Gates inside `decide_approval` may not be removed.

**Versioning.** Configuration is not versioned with the agent. A published agent version names sources and roles. The bytes and flags are whatever the tenant has stored at run time. Changing a logo does not publish a new agent version.

**Status.** Account, Canva, image approval, and job statuses stay the enums already on those tables. There is no configuration status on the agent.

**Foreign keys.** The foreign keys already on those tables. No new foreign key from them to an agent id. A clean generic relationship is unnecessary: the system agent is not a row, and user agents share these stores by `user_id`.

**Indexes.** The indexes already declared in `db/models.py`.

**Uniqueness.** As in the table above. A future per-agent toggle that is not already a column would be one row per `(user_id, agent_key)` and would store only catalog-approved switches such as "Canva on". It would not store keys, tokens, or handler paths. That row is not created until such a switch exists outside the current columns.

**Audit.** Existing route logs and `updated_at`. Responses pass through `strip_secrets`. `storage_key` stays off API and tool payloads.

**A user may not set.** A new tool, a publish step that skips `InstagramAgent`, another tenant's ids, a provider API key, `LLMPlanner` as the publish planner, or a format the publish workflow cannot accept.

## Workflow

**Purpose.** Stable name of a call graph the control plane already runs. The engine is a dispatcher to an existing function, not an interpreter.

**Ownership.** The catalog, owned by the agent that the workflow belongs to. The first catalog is owned by `instagram_marketing`.

**Tenant.** None. Workflows are not tenant data. A tenant enables optional steps through configuration. A tenant does not insert a workflow.

**Lifecycle.** Registered in code as published. A workflow id is not retired while an active agent version pins it. Retired means the catalog stops offering it to new versions. Historical runs keep the id they copied.

**Immutable.** `key`, owning agent key, handler identity for a published version.

**Mutable.** Nothing on a published catalog entry. A new version is a new WorkflowVersion.

**Versioning.** See WorkflowVersion. The workflow key stays stable across versions (`studio.generate` does not become a new id when a step comment changes).

**Status.** Catalog entries used by the reference agent are published. There is no user draft of a workflow.

**Foreign keys.** None today. A later pin is `(workflow_key, workflow_version_number)` stored on the agent version, not a foreign key into Instagram tables.

**Indexes.** Catalog lookup by key.

**Uniqueness.** `key` unique in the process catalog.

**Audit.** The module path and the test that the publish workflow's callable is the Instagram agent path. Users never supply the path.

**Catalog keys.** `studio.generate`, `studio.approve`, `studio.regenerate`, `direct.publish`, `scheduler.campaign`, `trend.cycle`, `instagram.publish`. Step order is specified in the [workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md). `instagram.publish` is one closed step from the platform's point of view.

## WorkflowVersion

**Purpose.** The ordered steps, output schema name, approval policy id, and declared side effects for one integer version of a workflow key.

**Ownership.** The workflow's owning agent. System-owned for the reference catalog.

**Tenant.** None.

**Lifecycle.** Published by code review. Not edited in place. A later version is a new integer. In-flight runs keep the version they started with.

**Immutable.** `workflow_key`, `version_number`, step list, `output_schema`, `approval_policy_id`, `side_effects`.

**Mutable.** None after publish.

**Versioning.** Integer. Version 1 is today's call graph. Output schema for studio plans is `instagram.creative_plan`, not a generic plan name.

**Status.** `published`.

**Foreign keys.** Optional `approval_policy_id` string matching a registered policy. Empty when the workflow itself is the publish runtime (`instagram.publish`), because recovery stays inside that runtime.

**Indexes.** `(workflow_key, version_number)`.

**Uniqueness.** `(workflow_key, version_number)`.

**Audit.** Code review and the phase 1 catalog test. Side-effect class is one of `none`, `local_write`, `provider_call`, `instagram_publish`. A workflow that names carousel, reel, or story cannot include `enqueue_publish`.

## WorkflowStep

**Purpose.** One ordered handler inside a workflow version. The handler is a Python callable that already exists, or a new function added in review. It is not a string the model evaluates.

**Ownership.** The workflow version.

**Tenant.** None on the step. The handler receives `TenantContext` from the control plane.

**Lifecycle.** Created with the workflow version. Frozen when that version is published. The runtime does not append a step during a run.

**Immutable.** `workflow_key`, `version_number`, `position`, `step_key`, `kind`, `handler`, `tool_allowlist`, `reads`, `writes`.

**Mutable.** None after publish.

**Versioning.** The parent WorkflowVersion.

**Status.** None of its own. Failure is a failed step: the engine stops on `AppError` and does not ask the model to pick a different tool.

**Foreign keys.** Parent workflow version. `reads` are knowledge source names. `writes` name existing tables only.

**Indexes.** `(workflow_key, version_number, position)`.

**Uniqueness.** `(workflow_key, version_number, position)` and `(workflow_key, version_number, step_key)`.

**Audit.** Handler path is a catalog constant. `writes` may name `generated_images`, `instagram_posts`, `agent_tasks`, `agent_events`, `daily_post_slots`, festival tables, trend tables, and snapshot tables. The step does not gain new tables.

**Kinds.** Only the kinds in the workflow model: `load_context`, `reason`, `validate_plan`, `render_image`, `export_creative`, `vision_qa`, `store_pending`, `decide_approval`, `enqueue_publish`, `instagram_tool_sequence`, `collect_trends`, `human_decision`. `instagram_tool_sequence` is one step. The six tool names are not workflow steps a user can reorder.

## ToolBinding

**Purpose.** A grant from an agent version into an existing plane. It does not register a tool.

**Ownership.** The agent version.

**Tenant.** None on the grant. Invocation uses the run's `TenantContext`. Model-supplied tenant keys are stripped.

**Lifecycle.** Edited only while the agent version is `draft`. Frozen on publish. A published grant cannot grow a tool.

**Immutable after publish.** `agent_version_id`, `plane`, `tool_name`.

**Mutable.** While the parent version is `draft`.

**Versioning.** The parent AgentVersion.

**Status.** Present or absent. There is no disabled tool row. Omitting a tool is the off switch. Canva is off when `CANVA_ENABLED` is false or the user is not connected, even if the catalog names the plane.

**Foreign keys.** Parent agent version. `tool_name` must already exist on that plane. No foreign key to a tools table. The registries stay in code.

**Indexes.** `(agent_version_id, plane)`.

**Uniqueness.** `(agent_version_id, plane, tool_name)`. `tool_name` may be null only when the grant is the whole allowed plane and every name on that plane is already allowlisted.

**Planes a grant may name.** `mcp_read`, `canva_remote`, `festival_scheduler`.

**Planes a grant may not name.** `instagram_publish`. The six tools and the publish aliases stay inside `instagram.publish` / `InstagramAgent`. MCP `assert_tool_allowed` still raises `TOOL_NOT_ALLOWED` for those names. A check on any future binding table rejects `validate_image`, `prepare_image`, `upload_image`, `create_instagram_media`, `publish_instagram_media`, `verify_publication`, `publish`, `media_publish`, `instagram_publish`, `post_to_instagram`, `create_media`, and `publish_media`.

**Audit.** Phase 1 test: the MCP grant and the Instagram tool set are disjoint. Binding rows store names only. They do not store the handler, a secret class, or a token.

**Reference grants.** The 22 MCP read tools. Canva adapter off that allowlist. `FestivalMcp` off that allowlist. Publish tools are not a grant.

## ModelBinding

**Purpose.** Declares which provider **role** an agent version uses, and the implementation name for that role (`deepseek`, `openai`, `mock`). `ProviderRegistry` passes that name into the existing factories. The binding does not contain a key.

**Ownership.** The agent version.

**Tenant.** None. Keys are process environment variables. Per-tenant provider keys are rejected. Every tenant in the process shares the configured keys. Isolation is on rows, files, and Instagram tokens.

**Lifecycle.** Frozen on publish. A published binding keeps its implementation name when an operator changes `LLM_PROVIDER`. `ProviderRegistry` calls `select_reasoning_provider`, `get_llm_provider`, `select_image_provider`, and `get_image_generation_provider` with that name. It does not construct a second vendor client and it does not bypass those factories.

**Immutable after publish.** `agent_version_id`, `role`.

**Mutable.** While the parent version is `draft`, the set of roles. Not the API key. Not a vendor SDK import.

**Versioning.** The parent AgentVersion.

**Status.** The live status is `configured: bool` from `Settings.public_ai_status()` and `GET /api/v1/ai/status`. Empty keys stay **IMPLEMENTED — NOT CONFIGURED**. The binding row has no status column.

**Foreign keys.** Parent agent version. No foreign key to a secrets table.

**Indexes.** `(agent_version_id, role)`.

**Uniqueness.** One row per `(agent_version_id, role)`.

**Roles.** `reasoning.studio`, `reasoning.scheduler`, `reasoning.trend`, `image.generate`, `image.edit`, `vision.qa`. `reasoning.trend` is the same object as `reasoning.scheduler`. `creative.export` is Canva, not a model provider, and `ProviderRegistry` rejects it. Embedding and moderation are interfaces only. Implementation names are `deepseek`, `openai`, `mock`, and the studio offline model `grounded`. Those names are not columns that store credentials. The in-code binding is `ModelBinding`. No binding table is added.

**Audit.** Logs include the provider name only. Prompts and tool results stay sanitized. The binding must not contain a key field. `creative_validation` still rejects credential-shaped plan keys.

## KnowledgeSource

**Purpose.** A name for context the server already loads. The model sees a sanitized MCP payload. It does not see ciphertext or filesystem paths.

**Ownership.** The source data is owned by `user_id`, except the festival calendar, which is code plus `festivals/data/lunar_dates.json` and is not a tenant row. Campaign rows in `festival_campaigns` are tenant-owned.

**Tenant.** Reads filter by the run's `user_id`. A foreign id is 404 or `ASSET_ACCESS_DENIED`, matching the tool. The binding does not store another user's id.

**Lifecycle.** The source exists as long as the underlying table exists. Binding it to an agent version follows that version's draft and publish rules. Unbinding on a later version does not delete the tenant's rows.

**Immutable.** Catalog name and the tables it reads.

**Mutable.** Tenant content in the existing tables, without publishing a new agent version. The binding list is mutable only on a draft agent version.

**Versioning.** The agent version pins a set of names. It does not pin a snapshot of the business profile. Context is loaded at run time.

**Status.** None. Missing optional data stays the current behavior (`found: false` for a missing logo). The platform does not invent a document.

**Foreign keys.** None to content rows. Allowed names are a closed set.

**Indexes.** None of their own. Existing owner indexes on the source tables.

**Uniqueness.** `(agent_version_id, source_name)` when a binding is stored. Catalog names are unique.

**Audit.** MCP audit line already records tool, tenant, status, duration, error code, and sanitized argument keys. Knowledge bindings add no payload log.

**Catalog.**

| Name | Storage |
| --- | --- |
| `business` | `business_profiles` |
| `brand` | `brand_profiles`, `brand_guidelines` |
| `product` | `products`, `product_assets` |
| `festival` | Code catalog and `festival_campaigns` |
| `account` | Snapshots, `instagram_intelligence_records`, Graph GET with the tenant token |
| `trend` | `trend_sources`, `trend_observations`, `trend_evidence`, `trend_reports`, `content_opportunities` |
| `content_rules` | `automation_settings` and static rules. `get_content_rules` keeps `publishing.available: false` |

The reference agent binds all of these. A future agent binds a subset. No embedding index is part of this model. `SessionContextStore` is not a knowledge source. It stays a partial no-op.

## AssetBinding

**Purpose.** Names the asset **roles** a workflow may resolve. Resolution stays in `services/asset_resolution.py` and the MCP asset tools. The platform does not add a blob store.

**Ownership.** Files are owned by `user_id` on `business_assets`, `product_assets`, and `generated_images`. The binding is owned by the agent version and does not hold bytes.

**Tenant.** A resolved id must belong to the run's user. A foreign asset id is denied. The binding does not store `storage_key`.

**Lifecycle.** The role grant follows the agent version. Uploading a file does not create a binding row. Deleting a file does not publish a new version. A missing production logo stays `found: false`.

**Immutable after publish.** `agent_version_id`, `role`.

**Mutable.** The underlying file rows, through the current upload routes. The grant list, only on a draft.

**Versioning.** The agent version. Concrete asset ids are not pinned on the system agent. A user agent may not pin another tenant's asset id.

**Status.** None on the binding. File existence is a resolver result.

**Foreign keys.** Parent agent version. No foreign key to `business_assets` on the system binding. If a later user pin is stored, `asset_id` is nullable, references `business_assets.id` ON DELETE SET NULL, and the asset's `user_id` must equal the agent owner's `user_id`. `generated_images` stay referenced by the image and post rows that already point at them, not by the agent definition.

**Indexes.** `(agent_version_id, role)`.

**Uniqueness.** `(agent_version_id, role)`.

**Roles.** `logo` (brand logo on `business_assets`), `product_image` (through `product_assets`), `generated_image` (`generated_images` for that user). Prepared public media for Meta stays the existing public route and is not an agent-owned blob.

**Audit.** Do not log `storage_key` or file bytes. MCP asset results stay sanitized.

## Trigger

**Purpose.** Names an entry point that starts one workflow for an agent. It does not add a webhook, a cron expression beyond `daily_post_time`, or a queue.

**Ownership.** The agent version for the binding. The entry point is process code. `SCHEDULER_ENABLED` remains the process switch.

**Tenant.** HTTP triggers use the session user. `scheduler_loop` builds `TenantContext` source `scheduler` for that user's id. The binding cannot name another user.

**Lifecycle.** Active only when the agent is `active` and the published version includes the trigger. `paused` and `retired` do not start work. In-flight tasks continue. `daily_posts_per_day` stays forced to 1.

**Immutable after publish.** `agent_version_id`, `trigger_key`, `workflow_key`.

**Mutable.** The settings row the trigger already gates (`automation_settings`), without a new version. The binding, only on a draft.

**Versioning.** The agent version. A run records the workflow version the published pointer selected at start.

**Status.** No trigger status column. Scheduler job status stays `scheduled_jobs.status`. The runner does not need that table to tick.

**Foreign keys.** Parent agent version. `workflow_key` must be a workflow the same agent version pins.

**Indexes.** `(agent_version_id, trigger_key)`.

**Uniqueness.** One workflow per `(agent_version_id, trigger_key)`.

**Reference map.**

| Trigger key | Workflow key | Entry that already exists |
| --- | --- | --- |
| `studio.http.generate` | `studio.generate` | `POST /api/v1/generation` |
| `studio.http.regenerate` | `studio.regenerate` | `POST /api/v1/generation/{image_id}/regenerate` |
| `studio.http.approve` | `studio.approve` | `POST /api/v1/generation/{image_id}/approve` |
| `studio.http.reject` | none (stop) | `POST /api/v1/generation/{image_id}/reject` |
| `direct.publish` | `direct.publish` | `POST /api/v1/instagram/publish` |
| `automation.run_now` | tick order below | `POST /api/v1/automation/run-now` |
| `scheduler.loop` | same tick | `scheduler_loop` when `SCHEDULER_ENABLED` |

The tick order stays daily, then festival, then trend. Trend may call `scheduler.campaign` only when the lane's auto-publish flag is already on. Reject does not publish.

**Idempotency.** Scheduler triggers use `daily_post_slots`, the daily published-post index, and the festival uniqueness keys. They do not allocate a second execution for the same slot.

**Audit.** `log_step` with tenant and task id when a task exists. The trigger does not log the body of a prompt.

## ApprovalPolicy

**Purpose.** A named function that decides whether a workflow may call `enqueue_instagram_publication`. The policy does not publish.

**Ownership.** Code. Id `instagram.still_image` is the reference policy. Function: `decide_approval` in `scheduler/approval_policy.py`.

**Tenant.** The decision includes `tenant_ownership_failed`. The policy cannot be given a document that sets `publish=true` while skipping that gate. User configuration may toggle `auto_daily_publish` and `auto_festival_publish` only.

**Lifecycle.** Registered in code. Bound by id on the agent version and on workflows that are not themselves the publish runtime. Direct `POST /api/v1/instagram/publish` is an authenticated upload trigger. It still goes through `InstagramAgent`. It is not a second policy a user can copy onto another agent to skip the gateway.

**Immutable.** Policy id, handler, and the gate list.

**Mutable.** Nothing in the policy body through an API. Flags on `automation_settings` remain mutable.

**Versioning.** The agent version stores the policy id string. Replacing the function is a code change, not a row update. In-flight runs keep the code that started them.

**Status.** The decision's existing results: human lanes stay pending with `publish=false`; automatic lanes stay pending with `publish=false` and `retryable=true` when a gate fails; `publish=true` only enqueues the Instagram agent.

**Foreign keys.** None. The id is a catalog string. Workflows that can set `publish=true` must name this id or a later registered policy with the same handoff rule.

**Indexes.** Catalog lookup by id.

**Uniqueness.** Policy id unique in the process.

**Audit.** Approval tests stay the gate (`tests/test_approval_policy.py`). The decision reason is not a secret. Tokens are not part of the decision record.

**Gates that stay.** `image_qa_failed`, `provider_result_malformed`, `product_reference_missing`, `offer_cannot_be_verified`, `required_logo_missing`, `festival_context_invalid`, `tenant_ownership_failed`.

## Execution

**Purpose.** One run of a workflow for one tenant. This is the platform name for a row in `agent_tasks`. It is not a new table.

**Ownership.** `agent_tasks.user_id`. Null user id may exist on older rows. New writes set the session or scheduler user. `get_owned` returns 404 when the id is not that user's.

**Tenant.** `user_id`. There is no second tenant column.

**Lifecycle.** The publish runtime uses `TaskStatus`: `pending`, `planning`, `validating`, `preparing`, `uploading`, `creating_media`, `publishing`, `verifying`, `completed`, `failed`. Other workflows do not translate their results into those states. Their outcome is the handler result: image approval status, trend report status, or an `AppError` code. Ambiguous publication stays inside the publish runtime. The dispatcher must not retry `enqueue_instagram_publication` after certainty `UNKNOWN`.

**Immutable.** `id`, `user_id` once set, `task_type`, `trigger`, `created_at`. When the optional labels exist: agent key, agent version, workflow key, workflow version, and `idempotency_key` are written at start and not updated to follow a later rollback.

**Mutable.** `status`, `current_step`, `started_at`, `completed_at`, `error`, `state_json`. `error` is a redacted `AppError` body. `state_json` must not gain token fields.

**Versioning.** Copies the agent and workflow versions at start. Does not move when the published pointer moves.

**Status.** `agent_tasks.status`. Index already exists.

**Foreign keys.** `user_id` → `users.id` ON DELETE SET NULL, already declared. Do not add a foreign key to a future agent table. The label is a string copy so retiring or deleting a definition does not cascade into history. `instagram_posts.agent_task_id` already points at the task id as a string. Leave that column as it is.

**Indexes.** Existing: primary key `id`, `user_id`, `status`. **PLANNED** only in phase 3, after a dispatcher stores the labels: `(user_id, agent_id, created_at)`. Partial unique `(user_id, idempotency_key)` where `idempotency_key` is not null. Those columns are nullable. Old rows stay null. Readers do not require them.

**Uniqueness.** Primary key `id`. Proposed idempotency unique above. Daily and festival uniqueness stay on their own tables, not on this one.

**Audit.** `agent_events` for publish transitions. `log_step` for `TASK_CREATED` and `TASK_COMPLETED`. SSE for the owning user. `state_json` and `error` stay free of tokens.

**Columns that exist.** `id`, `user_id`, `task_type`, `trigger`, `status`, `current_step`, `started_at`, `completed_at`, `error`, `created_at`, `state_json`.

**Columns that are specified and not added.** `agent_id`, `workflow_id`, `agent_version`, `workflow_version`, `idempotency_key`. All nullable strings or integers. No backfill required for publish correctness.

## ExecutionStep

**Purpose.** The position inside one execution. For the publish runtime this is `agent_tasks.current_step` plus the `agent_events` rows that moved `from_state` to `to_state` with a `tool`. For every other workflow it is the catalog step key inside the handler that already runs the whole pipeline. It is not a table.

**Ownership.** The parent execution. Events do not have their own `user_id`. The owner is `agent_tasks.user_id` through `task_id`.

**Tenant.** Same as the task. Listing events for another user's task is 404 because the task read is 404.

**Lifecycle.** `current_step` moves forward under the publish state machine. That mapping stays in `StateMachine` and `TOOL_TO_STATUS`. The generic dispatcher does not schedule `publish_instagram_media` as a free step. A failed step stops the run. Studio and scheduler plan retries stay capped on the handlers (3). The engine does not add a retry policy.

**Immutable.** Past events, once appended. `current_step` on the task is the mutable pointer.

**Mutable.** `agent_tasks.current_step` only.

**Versioning.** None. The step list is the workflow version copied onto the execution.

**Status.** Publish states are the `TaskStatus` values. Non-publish steps are not given those names.

**Foreign keys.** `agent_events.task_id` → `agent_tasks.id` ON DELETE CASCADE, already declared.

**Indexes.** `agent_events.task_id`, already declared. Order is `timestamp` ascending.

**Uniqueness.** Event `id`. Steps are not unique by tool name: a retry of storage or verification may record the same tool more than once inside the caps. That history is intentional. A second publish after `skip_publish` or success is not.

**Audit.** `observation` JSON stays free of tokens, paths, and secret-like keys, using the existing sanitize pattern. `replace_for_task` must not be used to rewrite a completed execution.

## Task

**Purpose.** The product name for Execution. Same table: `agent_tasks`. Same repository: `AgentTaskRepository`. Creating a second task table would split SSE, `GET /tasks/{id}`, and `instagram_posts.agent_task_id`.

**Ownership.** `user_id`. `get_owned` and `list_for_user` are the reads.

**Tenant.** `user_id`. Scheduler-created tasks use the user the loop is serving.

**Lifecycle.** Same as Execution. `task_type` and `trigger` are the implemented classifiers.

**Immutable.** `id`, `task_type`, `trigger`, `created_at`, and `user_id` once set.

**Mutable.** Status fields listed on Execution. `upsert` may update a live row with the same id. It must not be used to reassign `user_id` to another tenant.

**Versioning.** Same copied labels as Execution, when those columns exist. Until then, `task_type` and `trigger` imply the reference workflow: `INSTAGRAM_PUBLISH` is `instagram.publish` or `direct.publish`; generation and campaign types map to the studio and scheduler workflows. The implication is documentation. It is not a new column requirement.

**Status.** `agent_tasks.status`.

**Foreign keys.** `user_id` → `users.id` ON DELETE SET NULL.

**Indexes.** `user_id`, `status`.

**Uniqueness.** `id`.

**Audit.** Same as Execution.

**Enumerations that stay.** `TaskType`: `INSTAGRAM_PUBLISH`, `GENERATE_IMAGE`, `DAILY_RETAIL_POST`, `FESTIVAL_POST`, `CONTENT_JOB`. `TaskTrigger`: `USER`, `SCHEDULER`, `FESTIVAL`, `SYSTEM`. New agents add a type only when a reviewed handler writes a new task. They do not overload `INSTAGRAM_PUBLISH` for a non-publish run.

## AgentEvent

**Purpose.** One observed transition of a task. Table `agent_events`. This is the execution-history entity. It is not a definition-change log and not a new event bus.

**Ownership.** The task. Cascade-delete with the task. There is no direct `user_id`.

**Tenant.** Through `agent_tasks.user_id`. Do not add `user_id` on the event. A join that forgets the task owner is a bug. Repository reads go through the owned task.

**Lifecycle.** Insert during the run (`append`). Completed runs are not rewritten. Events are ordered by `timestamp`.

**Immutable.** `id`, `task_id`, `from_state`, `to_state`, `tool`, `result`, `observation`, `timestamp`.

**Mutable.** None in the platform contract. The existing `replace_for_task` helper remains an Instagram persistence detail and is not a public edit API.

**Versioning.** None. The parent execution holds the version numbers.

**Status.** `result` is the tool result string already stored. It is not an agent lifecycle status.

**Foreign keys.** `task_id` → `agent_tasks.id` ON DELETE CASCADE.

**Indexes.** `task_id`.

**Uniqueness.** `id` (`String(36)` default). No unique constraint on `(task_id, tool)`. Retries must be able to record the same tool again.

**Audit.** This table **is** the task audit. `observation` must stay free of tokens. Definition audit does not insert here, because SSE consumers treat these rows as publish progress.

**Columns.** `id`, `task_id`, `from_state`, `to_state`, `tool`, `result`, `observation`, `timestamp`. No new columns.

## Relationship summary

```text
users (tenant)
  ├── existing configuration rows (business, brand, products, assets,
  │     instagram account, canva, automation settings)
  └── agent_tasks.user_id          Execution and Task
        └── agent_events.task_id   AgentEvent and ExecutionStep history

system catalog (code, not rows)
  └── Agent instagram_marketing
        └── AgentVersion 1 published
              ├── pins Workflow keys and WorkflowVersion 1
              │     └── WorkflowStep list (closed kinds)
              ├── ToolBinding  (mcp_read, canva_remote, festival_scheduler)
              ├── ModelBinding (roles only)
              ├── KnowledgeSource names
              ├── AssetBinding roles
              ├── Trigger → workflow key
              └── ApprovalPolicy id instagram.still_image

user-owned Agent (not created until a user can create an agent)
  └── same version chain, user_id set, grants limited to the catalog
```

A run joins the two sides by copying catalog ids onto `agent_tasks` when phase 3 says those columns may exist. Until then the join is by `task_type` and `trigger` for the reference agent only.

## Tests when tables exist

Not added now. When a later phase creates user-owned rows or the nullable task labels, tests must assert:

- A user-owned agent key is unique per `user_id` and may repeat on another user.
- `instagram_marketing` cannot be stored with a non-null `user_id`.
- One tenant's agent, version, and configuration are 404 for another tenant.
- Tool bindings cannot name the six Instagram tools or the publish aliases.
- Model bindings have no secret column, and a round trip does not persist an API key.
- Asset and knowledge resolution with a foreign id fails closed.
- Two published versions can exist; only `published_version_id` changes on rollback; an existing task keeps the version it started with.
- At most one draft version per agent.
- `(user_id, idempotency_key)` returns the original task on replay and does not clear `skip_publish`.
- Existing uniqueness tests in `tests/test_database.py` still pass: email, daily slot, festival campaign, scheduled job, and cross-user reads.

Phase 1 catalog tests, when that phase is requested, stay in code and do not need these tables: publish callable is the Instagram agent path, MCP grant and Instagram tools are disjoint, provider roles resolve through the existing factories.
