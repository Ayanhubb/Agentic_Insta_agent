# Agent platform concept

> **Status on 2026-09-25.** Later code added the packages this page describes. They are **PARTIAL**: present as libraries or in-memory APIs, not mounted as the live studio path, and not stored in Alembic. Read [the final audit](AGENT_PLATFORM_FINAL_AUDIT.md). Sentences below that say the package does not exist yet are historical. Database tables, webhooks, and replacing `POST /api/v1/instagram/publish` are still not done.

Proposal date: 2026-09-25. Inspected git HEAD `a63569e`. Existing product docs in this folder were last verified on 2026-09-24 against `8ad7dc4` and FastAPI version `2.0.0`. This proposal does not change application behavior.

`brain.md` is not in the tree. The documentation audit folded its current facts into [ARCHITECTURE.md](ARCHITECTURE.md) and removed the scratch pad. This proposal uses that page, [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md), the MCP, scheduler, intelligence, database, authentication, API, and testing pages, and the modules they name.

## Status words

| Word | Meaning in these pages |
| --- | --- |
| **IMPLEMENTED** | Present in the working tree. Empty API keys stay **IMPLEMENTED — NOT CONFIGURED**, the same wording as the product docs. |
| **PLANNED** | A later code change that wraps or records what already runs, without a new product behavior. |
| **PROPOSED** | A platform abstraction that does not exist yet. It is limited to capabilities the current code already has. |

Nothing in these pages is a commitment to build a feature the repository does not already support. Reels, stories, carousels, a second Meta client, DeepSeek image generation, OpenAI vision QA, and DeepSeek web browsing stay out of scope. Those are already documented as absent.

## Why a platform abstraction is needed

The product today is a multi-user Instagram still-image studio. The modules that make it work are already separated in code, but the separation is Instagram-shaped:

- `InstagramAgent` (`agent/agent.py`) plans six fixed tools and is the only Meta publisher.
- `ContentOrchestrator` and `ContentAgent` plan still-image creatives and set `published=False`.
- `backend/mcp/` is an in-process allowlist of 22 read tools, plus a separate Canva adapter that never publishes.
- `LLM_PROVIDER` and `IMAGE_PROVIDER` select DeepSeek and OpenAI, with mocks when keys are empty.
- `AutomationRunner` runs daily, festival, and trend ticks for the signed-in user's rows.
- `decide_approval` (`scheduler/approval_policy.py`) is the gate in front of `PublicationService`.

Those pieces cannot be reused for another agent without copying Instagram assumptions. `AgentState.platform` is `Literal["instagram"]`. `DeterministicPlanner` rejects any other platform. `ToolRegistry.register` refuses names outside the six Instagram tools. API errors include `"platform": "instagram"`. Trend, festival, and account intelligence exist to feed that studio.

The manager's requirement is to treat this repository as the start of a reusable agent platform. Users will eventually configure agents from shared workflows, providers, tools, knowledge, assets, triggers, approvals, and execution policies. The Instagram studio becomes the first reference agent on that platform. It does not become a rewrite, and it does not become one of several publishers.

The abstraction is needed so that:

1. A future agent can be added by registering capabilities the backend already owns.
2. The generic runtime never contains Instagram tool names, Graph hosts, or festival rules.
3. DeepSeek and OpenAI stay provider entries selected by configuration.
4. MCP stays an allowlisted capability registry. Models do not receive a way to run arbitrary code.
5. Publishing to Meta stays inside `InstagramAgent`, reached only through `enqueue_instagram_publication`.

## What the platform is

```text
AGENT PLATFORM  (control plane: FastAPI, auth, policy, registries)
    |
    +-- Agent definitions          PROPOSED records; none stored today
    +-- Workflow definitions       PROPOSED records of pipelines that already run
    +-- Agent runtime              PROPOSED dispatcher; InstagramAgent stays specialized
    +-- Provider registry          IMPLEMENTED facade over existing factories
    +-- MCP / tool registry        IMPLEMENTED in-process allowlist
    +-- Knowledge / context        IMPLEMENTED as owner-scoped tables + MCP reads
    +-- Asset layer                IMPLEMENTED as business_assets and media paths
    +-- Triggers / scheduler       IMPLEMENTED in-process daily, festival, trend
    +-- Approval / policy          IMPLEMENTED decide_approval + human approve routes
    +-- Task / execution           IMPLEMENTED agent_tasks, agent_events, SSE
    +-- Observability              IMPLEMENTED logs, redaction, counters, traces
    +-- Multi-tenancy              IMPLEMENTED users.id as tenant
    +-- Security                   IMPLEMENTED JWT, Fernet, publish boundary
    |
    +-- Reference agents
          +-- Instagram Marketing Agent    IMPLEMENTED behavior, PROPOSED registration
          +-- future agents                PROPOSED, only by new registrations
```

The backend remains the policy and control plane. React calls FastAPI. Models, MCP handlers, Canva, and the scheduler do not choose credentials and do not call Meta create/publish/verify.

## Platform boundaries

| Boundary | Rule already in code | Platform rule |
| --- | --- | --- |
| Who may publish | `InstagramAgent` only. MCP `assert_tool_allowed` raises `TOOL_NOT_ALLOWED` for publish aliases and the six agent tools | Unchanged. A workflow step that publishes Instagram content calls `enqueue_instagram_publication` and nothing else |
| Who may name a tenant | `TenantContext` from `authenticated`, `scheduler`, or `system`. Model arguments `tenant_id` / `user_id` are stripped | Unchanged. Agent configuration cannot supply another user's id |
| What a model may do | Return schema-validated plans (`ContentPlan`, `CreativePlan`, `TrendBrief`). `creative_validation` rejects publish tools and credential fields. `LLMPlanner` is a stub | Models propose structured output. The runtime executes registered handlers |
| What a tool may do | MCP handlers are read-only. Instagram tools are registered only on `ToolRegistry` | Every tool has a side-effect class. External side effects are not granted to MCP or to model tool-calling |
| Where secrets live | Process env for provider keys. Fernet columns for Instagram and Canva tokens. Responses pass through `strip_secrets`. Logs use `redact_text` | Providers and tools resolve secrets on the server. Prompt packets and tool results stay sanitized |
| What "provider" means | `LLM_PROVIDER` default `deepseek`. `IMAGE_PROVIDER` default `openai`. `VISION_PROVIDER` default `deepseek` | A role plus an implementation name. The runtime does not import a vendor SDK |

## Map of the twenty topics

| Topic | Where it is specified |
| --- | --- |
| Why the abstraction is needed | This page |
| Platform boundaries | This page, [architecture](AGENT_PLATFORM_ARCHITECTURE.md) |
| Agent definition and lifecycle | [Agent model](AGENT_PLATFORM_AGENT_MODEL.md) |
| Domain entities, reuse, versioning | [Data model](AGENT_PLATFORM_DATA_MODEL.md) |
| Workflow definition | [Workflow model](AGENT_PLATFORM_WORKFLOW_MODEL.md) |
| Generic workflow engine | [Workflow engine](AGENT_PLATFORM_WORKFLOW_ENGINE.md) |
| Tool definition and MCP | [MCP](AGENT_PLATFORM_MCP.md) |
| LLM, image, and vision providers | [Provider model](AGENT_PLATFORM_PROVIDER_MODEL.md) |
| Knowledge, assets, triggers | [Architecture](AGENT_PLATFORM_ARCHITECTURE.md), bound in [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md) |
| Approval and policy | [Security](AGENT_PLATFORM_SECURITY.md) |
| Runtime, tasks, observability | [Runtime](AGENT_PLATFORM_RUNTIME.md) |
| Tenant isolation and security | [Security](AGENT_PLATFORM_SECURITY.md) |
| Instagram as reference, future agents | [Instagram reference](AGENT_PLATFORM_INSTAGRAM_REFERENCE.md), [Agent model](AGENT_PLATFORM_AGENT_MODEL.md) |
| Migration | [Migration plan](AGENT_PLATFORM_MIGRATION_PLAN.md) |
| Decisions and rejected alternatives | [Decisions](AGENT_PLATFORM_DECISIONS.md) |

## What stays Instagram-specific on purpose

These modules are the reference agent's implementation. The generic runtime must call them. It must not absorb them:

- `agent/agent.py`, `agent/registry.py`, `agent/planner.py`, `agent/recovery.py`, `agent/state_machine.py`
- `tools/instagram_media.py`, `tools/instagram_verifier.py`, `services/instagram_client.py`
- `services/publication.py` and `agent/instagram_tasks.py`
- `scheduler/approval_policy.py` gates that mention logo, offer, festival, and product references
- `festivals/` India catalog and `Asia/Kolkata` campaign dates
- Trend packet tools that read Instagram account intelligence

`ContentOrchestrator`, `ContentAgent`, MCP context tools, brand and product assets, and the provider factories are reusable only after they are described as bindings. Today their schemas (`CreativePlan`, `ContentPlan`, caption limits, still-image QA) are still the Instagram studio's schemas. The platform names that honestly: output schema `instagram.creative_plan`, not "generic plan".

## Current agents in the tree

The word "agent" is already used for four implementations. They are not rows in an agent table.

| Name in docs | Code | Publishes |
| --- | --- | --- |
| Instagram Agent | `InstagramAgent` | Yes, still image only |
| Content agent | `ContentOrchestrator` (studio) and `ContentAgent` (scheduler) | No |
| Trend intelligence | `TrendIntelligenceScheduler`, `DeepSeekTrendAnalyst`, `backend/trends/` | No. May call the campaign pipeline only when existing auto-publish flags allow it |
| Account intelligence | `AccountIntelligenceService` and MCP account tools | No. Graph GET only |

`LLMPlanner` in `agent/planner.py` raises that LLM planning is not enabled. Status in the Instagram agent doc: **PARTIAL** stub. The platform does not enable it.

## Related product docs

- [Architecture](ARCHITECTURE.md)
- [AI architecture](AI_ARCHITECTURE.md)
- [MCP](MCP.md)
- [Instagram agent](AGENTS/INSTAGRAM_AGENT.md)
- [Content agent](AGENTS/CONTENT_AGENT.md)
- [Scheduler](AUTOMATION/SCHEDULER.md)
- [Security](SECURITY/SECURITY.md)
- [Tenant isolation](SECURITY/TENANT_ISOLATION.md)
- [Database](DATABASE/DATABASE.md)
- [Authentication](API/AUTHENTICATION.md)
- [API reference](API/API_REFERENCE.md)
- [Testing](TESTING/TESTING.md)
