# Instagram Agentic AI — Architecture Audit (V1 → V2)

**Audience:** Agent 2 and subsequent implementation agents  
**Audit type:** Read-only inspection of the existing repository  
**Constraint:** V1 Instagram publishing behavior must be preserved. This document does not change application code, tests, or runtime behavior.

**Critical architectural rule (non-negotiable):**

```text
User / Scheduler
  → Content Agent
    → LLM
      → Structured Plan
        → Policy Validation
          → ToolRegistry
            → Executor
              → Observation
                → Recovery
                  → Verification
                    → Database
```

The existing **Instagram Agent remains the only publishing authority**.

The LLM must never:

- call Instagram APIs
- execute shell commands
- execute arbitrary Python
- access OAuth tokens
- bypass `ToolRegistry`

---

## 1. Current architecture

V1 is a **single-purpose Instagram image publishing agent**. The only supported operation is:

```text
UPLOAD IMAGE → VALIDATE → PREPARE → HOST → CREATE INSTAGRAM MEDIA → PUBLISH → VERIFY
```

It is **not** a chatbot, scheduler, multi-user product, or image generator. There is no authentication, no database, no OpenAI client, no React app, no festival calendar, and no per-user Instagram OAuth.

### 1.1 Layering

| Layer | Location | Responsibility |
| --- | --- | --- |
| HTTP / demo UI | `api/`, `api/static/index.html` | Accept one image, create a task, stream progress, serve hosted JPEGs |
| Agent loop | `agent/agent.py` | Plan → select tool → execute → observe → recover → verify |
| Planner | `agent/planner.py` | Produce a structured list of allowlisted tool names |
| Registry | `agent/registry.py` | Allowlist-gated tool lookup. Unregistered names are rejected |
| Executor | `agent/executor.py` | Run one tool; convert exceptions into `Observation` |
| Recovery | `agent/recovery.py` | Deterministic continue / retry / skip / verify-first / fail |
| State machine | `agent/state_machine.py` | Legal `TaskStatus` transitions only |
| Tools | `tools/` | Validate, prepare, host, create, publish, verify |
| Graph HTTP | `tools/instagram_media.py` (`InstagramMediaService`) | Official Meta Content Publishing API |
| Models | `models/` | Errors, observations, agent state, API responses |
| Config | `config.py` | Environment settings. Secrets are not hard-coded |
| Process memory | `api/task_store.py` | In-memory tasks + SSE fan-out. Lost on restart |

### 1.2 Runtime composition

`create_app()` in `api/app.py`:

1. Loads `Settings` (`get_settings()`).
2. Creates **one process-wide** `TaskStore`.
3. Creates **one process-wide** `InstagramGraphClient` from env `META_ACCESS_TOKEN` + `INSTAGRAM_ACCOUNT_ID`.
4. Builds an `agent_factory` that constructs a new `InstagramAgent` per run, sharing that client.
5. Mounts `/api/v1` routes and a static demo UI at `GET /`.

`InstagramAgent.run()` is the observe–decide–act loop. It never imports Instagram HTTP helpers ad hoc. It always does `registry.get(tool_name)` then `executor.execute(tool, state)`.

### 1.3 What V1 explicitly does not do

Documented in `README.md` and confirmed in code:

- No Facebook posts, Reels, Stories, carousels
- No captions or hashtags on `POST /{ig-user-id}/media`
- No scheduling
- No image generation
- No multi-platform publishing
- No production LLM planner (`LLMPlanner` is a stub)
- No user accounts
- No Instagram account connection flow (token is a single env var)

---

## 2. Current directory tree

Project files only (excluding `.venv`, `__pycache__`, committed binary uploads):

```text
agentic/
├── ARCHITECTURE_AUDIT.md          ← this document
├── README.md
├── config.py                      Settings, Graph host allowlist, directories
├── main.py                        ASGI: create_app()
├── errors.py                      Re-export of models.errors
├── logger.py                      Compatibility wrapper over services.logging
├── requirements.txt
├── pytest.ini
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env.example
├── .gitignore
├── agent/
│   ├── __init__.py
│   ├── agent.py                   InstagramAgent + build_registry()
│   ├── planner.py                 Planner ABC, DeterministicPlanner, LLMPlanner stub
│   ├── executor.py
│   ├── registry.py
│   ├── recovery.py
│   ├── state_machine.py
│   └── state.py                   UNUSED helper functions (see §7)
├── api/
│   ├── __init__.py
│   ├── app.py                     FastAPI factory
│   ├── routes.py                  Health, publish, tasks, SSE, media
│   ├── task_store.py              In-memory TaskStore
│   ├── middleware.py              Request ID + upload size check
│   ├── dependencies.py            UNUSED get_agent() cache
│   └── static/
│       └── index.html             V1 demo UI (not React)
├── models/
│   ├── errors.py
│   ├── observations.py
│   ├── requests.py
│   ├── responses.py
│   └── state.py                   AgentState, TaskStatus, TraceStep, Transition
├── tools/
│   ├── base.py                    Tool + ToolResult
│   ├── image_validator.py
│   ├── image_preparer.py
│   ├── image_storage.py           ImageStorageProvider + MockImageStorage
│   ├── instagram_media.py         Graph client + create/publish tools
│   └── instagram_verifier.py
├── services/
│   ├── instagram_client.py        Re-export of tools.instagram_media client
│   └── logging.py                 JSON logs + secret redaction
├── tests/                         Fully mocked default suite
├── scripts/
│   └── real_publish_once.py       Opt-in live publish, not pytest
├── storage/                       Hosted JPEGs (gitignored)
├── tmp/                           Upload scratch (gitignored)
├── input/                         Reserved
└── output/
    ├── prepared/                  Prepared JPEGs
    └── hosted/                    Alternate hosted dir (storage_dir is used in V1)
```

**Frontend:** there is **no React/Vite/TypeScript app**. The only UI is `api/static/index.html`.

**Database:** none. No SQLAlchemy, Alembic, SQLite file, or Postgres service.

**Auth:** none. No JWT, sessions, passwords, or Meta OAuth routes.

---

## 3. Existing execution flow

### 3.1 HTTP happy path

```text
POST /api/v1/instagram/publish  (multipart field `image` or `file`)
  → MIME allowlist (JPEG/PNG)
  → stream to tmp/{uuid}.jpg|.png with size cap
  → AgentState(task_id=uuid, image_path=tmp file)
  → TaskStore.save
  → if wait=true: run agent inline, return 200/error
  → else: asyncio.create_task(agent.run), return 202
```

### 3.2 Agent loop

```text
InstagramAgent.run(state)
  1. PLANNING
  2. planner.create_plan(state)  → Plan of 6 tools
  3. _validate_plan: every name ∈ ALLOWED_TOOL_SET and registry.get(name) succeeds
  4. while status ∉ {COMPLETED, FAILED}:
       a. _select_next_action(plan, observations)
            - honor state.force_next_tool
            - skip publish if _must_not_publish (media id already present,
              skip_publish, or publish_certainty ∈ {SUCCEEDED, UNKNOWN})
            - skip create if container id already exists
       b. registry.get(tool)
       c. state_machine.enter_tool
       d. executor.execute → Observation
       e. state.apply_observation
       f. recovery.decide → CONTINUE | RETRY | SKIP | VERIFY_FIRST | FAIL
  5. COMPLETED only if instagram_media_id is set
  6. cleanup temp files under settings.temp_dir
```

### 3.3 Tool sequence (V1 plan)

| Order | Tool name | Class | Writes into AgentState |
| --- | --- | --- | --- |
| 1 | `validate_image` | `ImageValidator` | (observation data only; path unchanged) |
| 2 | `prepare_image` | `ImagePreparer` | `prepared_image_path`, `temp_paths` |
| 3 | `upload_image` | `ImageStorage` | `image_url`, `storage_key` |
| 4 | `create_instagram_media` | `InstagramMediaCreator` | `instagram_container_id` |
| 5 | `publish_instagram_media` | `InstagramPublisher` | `instagram_media_id` |
| 6 | `verify_publication` | `InstagramVerifier` | `instagram_media_id`, `permalink` |

### 3.4 Instagram Graph contract used today

Implemented in `InstagramMediaService`:

1. `POST /{ig-user-id}/media` with `image_url` (no caption)
2. `GET /{container-id}?fields=status_code` until `FINISHED` / `PUBLISHED`
3. `POST /{ig-user-id}/media_publish` with `creation_id` (**HTTP retry disabled**)
4. `GET /{media-id}?fields=id,media_type,permalink,timestamp`

Image URL must be public HTTPS. Localhost is rejected before the HTTP call.

---

## 4. Existing API endpoints

Router prefix: `/api/v1` (`api/app.py`).

| Method | Path | Auth | Persistence | Behavior |
| --- | --- | --- | --- | --- |
| `GET` | `/` | none | none | Demo UI `index.html` |
| `GET` | `/static/*` | none | none | Static files if directory exists |
| `GET` | `/api/v1/health` | none | none | `{"status":"ok"}` |
| `POST` | `/api/v1/instagram/publish` | none | memory | Multipart image. `wait=true` blocks. Else 202 + background task |
| `GET` | `/api/v1/tasks/{task_id}` | none | memory | Full `TaskStatusResponse` including `steps` / `execution_trace` |
| `GET` | `/api/v1/tasks/{task_id}/events` | none | memory | SSE `event: status` until COMPLETED/FAILED. Polling fallback in UI |
| `GET`/`HEAD` | `/api/v1/media/{filename}` | none | disk | Serves hosted JPEG. Path traversal blocked |

**Not present:** CORS middleware, login, register, logout, Instagram OAuth, content jobs, approvals, history, post counts, schedules, festivals, users.

**Contract notes for React later:**

- Publish field name is `image` (UI) or `file` (accepted).
- Task progress payload already includes `steps[]` with `tool`, `status`, `duration_ms`, `decision`.
- SSE media type is `text/event-stream`.
- Errors are `{ success: false, status: "failed", error: { code, message } }`. Messages are redacted.

**README vs code:** README lists `GET /media/{filename}`. The live route is `GET /api/v1/media/{filename}`. `PUBLIC_IMAGE_BASE_URL` must therefore include `/api/v1/media` for Meta to fetch the file this process serves.

---

## 5. Existing models

### 5.1 Errors (`models/errors.py`)

`ErrorCode` is a string enum with aliases (e.g. `INVALID_IMAGE = "INVALID_FILE"`).  
`AppError` carries `http_status`, `retryable`, `certainty` (`failed` / `succeeded` / `unknown`), and `details`.

Certainty is the duplicate-protection signal. `UNKNOWN` after publish means **do not publish again**.

### 5.2 Observation (`models/observations.py`)

```text
Observation(success, tool, data, error, duration_ms)
```

`summary()` exposes only safe keys: format, width, height, image_url, container/media ids, verified.

### 5.3 AgentState (`models/state.py`) — the V1 source of truth

Important fields:

- Identity: `task_id`, `request_id`
- Image: `image_path`, `original_filename`, `prepared_image_path`, `image_url`, `storage_key`, `temp_paths`
- Control: `status`, `current_step`, `plan`, `completed_tools`, `force_next_tool`, `skip_publish`, `publish_certainty`
- Instagram: `instagram_container_id`, `instagram_media_id`, `permalink`
- Timeline: `execution_trace` (`TraceStep`), `execution_history` (`Transition` with aliases `from`/`to`)
- Error: `error: ErrorBody | None`
- Unused: `state_transitions` is declared and never written

**Missing for V2 (do not invent by rewriting AgentState usage):** `user_id`, `instagram_account_id`, `content_job_id`, `caption`, `source` (`upload` / `generated` / `scheduled` / `festival`), `approval_status`.

### 5.4 Request / response

- `InstagramPublishTask` — internal; rarely used because routes build `AgentState` directly.
- `InstagramPublishRequest` — documented only; routes use multipart, not JSON.
- `PublishResponse` / `TaskStatusResponse` — public API. Preserve these shapes; extend later with optional fields.

### 5.5 Plan models (`agent/planner.py`)

`PlannedAction(tool, purpose)`, `PlanStep`, `Plan`.  
`Planner` ABC: `create_plan(task, available_tools=None) -> Plan`.

---

## 6. Existing tools

`ALLOWED_TOOLS` in `agent/planner.py` **is** the V1 allowlist. `ToolRegistry.register()` refuses anything else.

| Name | File | Side effects | Recovery relevance |
| --- | --- | --- | --- |
| `validate_image` | `tools/image_validator.py` | Reads file; Pillow verify+load; JPEG/PNG; size/dimension limits | Validation failures are terminal |
| `prepare_image` | `tools/image_preparer.py` | Writes `output/prepared/{task_id}.jpg`; does not overwrite original; EXIF transpose; 4:5–1.91 crop; 1080 width; JPEG encode | Preparation failures are terminal |
| `upload_image` | `tools/image_storage.py` | Copies to `storage/{task_id}.jpg`; returns HTTPS URL | Temporary storage failures retry |
| `create_instagram_media` | `tools/instagram_media.py` | Graph `POST /media`; poll container | Unknown + no id: retry once; existing id: skip |
| `publish_instagram_media` | `tools/instagram_media.py` | Graph `POST /media_publish` with **retry=False**; in-process container→media cache | UNKNOWN/timeout → verify-first, never republish |
| `verify_publication` | `tools/instagram_verifier.py` | `GET /{media-id}` must match id and `IMAGE`; HTTP 200 alone is not proof | Delay retries verify only |

### 6.1 Storage abstraction (reuse)

`ImageStorageProvider` is the correct extension point:

- `MockImageStorage` — local disk + HTTPS URL (used in V1)
- `CloudImageStorage` — explicit 503 stub; do not pretend it works

`resolve_public_file()` uses `Path.name` and parent-equality to block `..`. Keep this.

### 6.2 Graph client (reuse, do not let LLM touch)

`InstagramClient` protocol:

- `create_image_container`
- `get_container_status`
- `publish_container`
- `get_media`
- `list_recent_media`
- `aclose`

Host allowlist: `graph.facebook.com`, `graph.instagram.com`. HTTPS only (localhost HTTP only if `allow_insecure_graph_http` for tests). Redirects disabled. Bearer token from settings, never from tool arguments.

---

## 7. Existing state machine

`TaskStatus`:

```text
pending → planning → validating → preparing → uploading
        → creating_media → publishing → verifying
        → completed | failed
```

Legal transitions (`agent/state_machine.py`):

| From | To |
| --- | --- |
| PENDING | PLANNING, FAILED |
| PLANNING | VALIDATING, FAILED |
| VALIDATING | PREPARING, FAILED |
| PREPARING | UPLOADING, FAILED |
| UPLOADING | CREATING_MEDIA, FAILED |
| CREATING_MEDIA | PUBLISHING, VERIFYING, FAILED |
| PUBLISHING | VERIFYING, FAILED |
| VERIFYING | COMPLETED, VERIFYING, FAILED |
| COMPLETED / FAILED | (terminal) |

`creating_media → verifying` exists so an unknown create/publish path can skip a second publish.

`enter_tool()` maps tool name → status via `TOOL_TO_STATUS`. Unknown tools cannot enter the machine.

**Unused module:** `agent/state.py` duplicates some helpers (`begin_planning`, `record_success`, …) and is **not imported** by the agent loop. Do not treat it as the state machine. Do not delete it in V2 unless a later cleanup agent is explicitly assigned; Agent 2 should ignore it.

---

## 8. Existing recovery behavior

`RecoveryPolicy.decide(observation, state, attempt)` is deterministic. **Do not replace this policy.** V2 content/LLM recovery must be a **separate** policy or an additive branch that cannot override publish duplicate-protection.

| Observation | Decision |
| --- | --- |
| success | CONTINUE |
| Invalid/corrupted/unsupported/too-large/too-small image, invalid URL/account, preparation failure | FAIL immediately |
| AUTHENTICATION / PERMISSION / CONFIGURATION | FAIL immediately |
| `publish_instagram_media` + certainty UNKNOWN | `skip_publish=True`, VERIFY_FIRST (`verify_publication`) |
| Publish TIMEOUT / INSTAGRAM_TIMEOUT | same as unknown: never republish |
| RATE_LIMITED with `Retry-After` ∈ (0, max_retry_after] and attempt < 2 | RETRY once |
| RATE_LIMITED otherwise | FAIL |
| STORAGE_TEMPORARY_FAILURE | RETRY up to `storage_retry_attempts` |
| `verify_publication` + VERIFICATION_DELAY | RETRY verify only |
| Verify failed and publish_certainty UNKNOWN | FAIL as ambiguous (agent maps to `AMBIGUOUS_PUBLICATION`) |
| Create media UNKNOWN, no container id, attempt < 2 | RETRY |
| Create media UNKNOWN, container id exists | SKIP → publish |
| Other retryable Instagram errors on non-publish tools, attempt < 2 | RETRY |
| Else | FAIL |

Agent-level duplicate guards (`_must_not_publish`):

- `skip_publish`
- `instagram_media_id` already set
- `publish_certainty` SUCCEEDED or UNKNOWN

`InstagramMediaService.publish_container` also refuses to republish if Graph reports container status `PUBLISHED`, and caches container_id → media_id **in process memory only**.

---

## 9. Existing tests

Default command: `pytest` (fully mocked; no Meta credentials).

| File | What it locks in |
| --- | --- |
| `tests/test_agent.py` | Real image tools + fake Instagram client → COMPLETED |
| `tests/test_agentic_workflow.py` | Plan/observe/recover: validation stop, prep fail, storage retry, API/auth/permission fail, rate-limit retry/stop, timeout does not republish, ambiguous verify-first, verify retry without second publish, duplicate publish skip |
| `tests/test_planner.py` | Deterministic 6-step plan; LLMPlanner disabled; sanitize drops shell/url; registry allowlist; illegal transition |
| `tests/test_api.py` | Publish 200, invalid MIME, missing file, task status, health |
| `tests/test_image_pipeline.py` / `test_image_validator.py` | JPEG/PNG, GIF reject, corruption, oversize, tiny, idempotent prepare, mock HTTPS storage, localhost rejected, cloud stub |
| `tests/test_instagram_media.py` | Mocked Graph success, unconfigured, auth 190, rate limit, permission, localhost URL, timeout, 500 |
| `tests/test_instagram_verifier.py` | HTTP 200 with wrong id is not verification |
| `tests/test_errors.py` | Executor hides unexpected exceptions; no token leak |
| `tests/test_state.py` | `from`/`to` aliases on transitions |
| `tests/test_real_instagram.py` / `test_instagram_integration.py` | Opt-in only; default pytest does not publish |

**Helpers to keep:** `tests/helpers.py` (`ScriptedTool`, `make_agent`, `DummyInstagramClient`, `write_jpeg`). Subsequent agents must keep this suite green. Do not delete tests.

---

## 10. Extension points

Use these. Do not fork a second Instagram publisher.

| Extension point | How to extend |
| --- | --- |
| `Planner` ABC | Add a **ContentPlanner** / OpenAI structured-output planner. Do not reuse `LLMPlanner` as-is for Instagram tools |
| `ALLOWED_TOOLS` / `ALLOWED_TOOL_SET` | Split into **content allowlist** vs **publish allowlist**. Today they are the same 6 names |
| `ToolRegistry` | Instantiate **two registries** (content vs publish), or add `kind=` without weakening `get()` |
| `Tool` / `ToolResult` | New tools: `generate_image`, `generate_caption`, `store_generated_image`, `record_approval`. They must not accept tokens or URLs to execute |
| `ImageStorageProvider` | User-scoped local paths: `storage/{user_id}/{object_key}` |
| `InstagramClient` protocol | Construct **per connected account**, not process-wide. Optional later: `caption` on create |
| `RecoveryPolicy` | Keep publish policy. Add `ContentRecoveryPolicy` for generation/LLM failures |
| `StateMachine` | Keep for publish. Add a **content job** state machine (`drafting` → `generating` → `preview` → `awaiting_approval` → `ready_to_publish`) |
| `create_app` / `agent_factory` | Inject db session, current user, per-account Graph client |
| `TaskStore` | Keep the interface (`save`, `get`, `subscribe`). Back it with DB + in-process SSE queues |
| `Settings` | Add OpenAI, auth, DB URL, scheduler flags. Keep Graph host allowlist |
| `Observation` + `on_change` | Already streams timeline to the UI. Persist the same snapshots |
| `sanitize_actions` on `LLMPlanner` | Pattern to keep: drop `command`/`code`/`script`/`python`/`shell`/`url`/`http`/`token` keys |

### 10.1 Where each V2 requirement plugs in

| Requirement | Plug-in location |
| --- | --- |
| A. User authentication | New `api/auth.py` + FastAPI dependencies on `/api/v1/*` except health and public media |
| B. Prompt → LLM → image → preview → approval → publish | New Content Agent + new routes; hand off file path to existing `InstagramAgent.run` |
| C. Daily autonomous posts | New scheduler → Content Agent (auto-approve policy) → InstagramAgent |
| D. Indian festival automation | Festival table + scheduler jobs; still InstagramAgent for publish |
| E. Local image storage | Existing `MockImageStorage`; add user isolation |
| F. Persistent database | New `db/` package; replace memory `TaskStore` internals |
| G. Post count / history | Query `posts` / `agent_runs` by `user_id` + `instagram_account_id` |
| H. React dashboard | New `frontend/`; FastAPI CORS + existing SSE |
| I. Instagram account connection | Meta OAuth routes; store encrypted tokens per user/account |
| J. Agent execution timeline | Persist `execution_trace` already produced by V1 |
| K. Safe failure recovery | Keep `RecoveryPolicy` + `AMBIGUOUS_PUBLICATION` |
| L. Multi-user isolation | `user_id` on every row; per-account Graph client; scoped storage paths |
| M. OpenAI through FastAPI | `services/openai_client.py` called only by content tools, never from the browser |

---

## 11. Files that should be preserved

Treat these as **publishing authority**. Later agents may add parameters, not replace algorithms.

**Do not replace / do not weaken:**

- `agent/recovery.py` — duplicate-protection decisions
- `agent/executor.py` — observation conversion, exception hiding
- `agent/state_machine.py` — legal publish transitions
- `tools/image_validator.py`
- `tools/image_preparer.py`
- `tools/instagram_media.py` — host allowlist, no-redirect HTTP, `publish_container(..., retry=False)`, Graph error mapping, `_published_containers` dedupe
- `tools/instagram_verifier.py` — matching media id + IMAGE type
- `tools/image_storage.py` — `ImageStorageProvider`, path traversal guard, HTTPS/localhost checks
- `tools/base.py`
- `services/logging.py` — redaction
- `models/errors.py` — `OperationCertainty`, `AMBIGUOUS_PUBLICATION`
- `models/observations.py`
- Existing tests under `tests/`
- `scripts/real_publish_once.py` — keep opt-in live path separate from pytest

**Preserve behavior of:**

- `InstagramAgent._must_not_publish`
- `InstagramAgent._validate_plan`
- `LLMPlanner.sanitize_actions` policy (extend, do not loosen)

---

## 12. Files that should be modified later

Modify incrementally. Do not rewrite.

| File | Later change |
| --- | --- |
| `agent/planner.py` | Split allowlists. Keep `DeterministicPlanner` for Instagram. Add content planner. **Remove Instagram tool names from any LLM-facing allowlist** |
| `agent/registry.py` | Support content tools without allowing them into the Instagram agent registry |
| `agent/agent.py` | Accept per-account `InstagramClient`; optional `user_id` on state; do not give the agent an OpenAI client |
| `agent/__init__.py` | Export new content agent only after it exists |
| `api/app.py` | CORS, lifespan (DB, scheduler), auth middleware, **stop sharing one Graph client** |
| `api/routes.py` | Auth dependency; new routers; keep publish route as the InstagramAgent entry |
| `api/task_store.py` | Durable implementation, same interface |
| `api/middleware.py` | Auth context; keep size limit |
| `api/dependencies.py` | Replace unused `get_agent()` with `get_current_user`, `get_db`, `get_instagram_agent_for_account` |
| `models/state.py` | Additive fields: `user_id`, `instagram_account_id`, `content_job_id`, `caption`, `source` |
| `models/responses.py` | Optional dashboard fields; keep V1 keys |
| `config.py` | `DATABASE_URL`, `OPENAI_API_KEY`, `JWT`/`SESSION` secrets, `CORS_ORIGINS`, scheduler timezone `Asia/Kolkata` |
| `tools/instagram_media.py` | Optional `caption` on create; construct with account-scoped token/id |
| `tools/image_storage.py` | Namespace by `user_id` |
| `requirements.txt` | DB + OpenAI + auth + scheduler + CORS — add when that agent implements it |
| `docker-compose.yml` | Frontend service later; DB later if chosen |
| `Dockerfile` | Copy new packages; do not bake secrets |
| `.env.example` | New keys, empty values |
| `api/static/index.html` | Leave as V1 demo until React replaces it |

**Do not modify in Agent 2 beyond additive persistence wiring:** recovery, verifier, image validator/preparer algorithms.

---

## 13. Proposed V2 architecture

Two agents, one executor/registry/observation pattern, **hard isolation of Instagram side effects**.

```text
                    ┌──────────────────────────────────────────────┐
 React dashboard    │ FastAPI (auth, CORS, policy)                 │
 Scheduler          │                                              │
                    │  Content Agent                               │
                    │    LLM (OpenAI structured output only)       │
                    │    → Content plan (prompt, caption, asset)   │
                    │    → PolicyValidation (no IG tools, no token)│
                    │    → Content ToolRegistry                    │
                    │         generate_caption                     │
                    │         generate_image                       │
                    │         store_generated_image                │
                    │    → Executor → Observation → ContentRecovery│
                    │    → preview in DB (awaiting_approval        │
                    │       or auto-approved by schedule policy)   │
                    │                                              │
                    │  InstagramAgent  ← ONLY publisher            │
                    │    DeterministicPlanner (publish tools only) │
                    │    Publish ToolRegistry (existing 6 tools)   │
                    │    Executor → RecoveryPolicy (existing)      │
                    │    StateMachine (existing)                   │
                    │    Verification (existing)                   │
                    └──────────────┬───────────────────────────────┘
                                   │
                    Local disk     │  Database
                    storage/{uid}  │  users, accounts, jobs, posts,
                                   │  agent_runs, events, festivals
```

### 13.1 Content Agent vs Instagram Agent

| | Content Agent (new) | Instagram Agent (existing) |
| --- | --- | --- |
| Trigger | User prompt, daily schedule, festival job | Approved local image path |
| Planner | OpenAI structured plan | `DeterministicPlanner` |
| Tools | generation + storage only | existing 6 publish tools |
| Tokens | OpenAI key in server settings | Per-account Meta token inside Graph client |
| LLM visibility | Tool names + JSON schema only | None |
| Success | preview image + caption + job id | verified `instagram_media_id` |

**Handoff contract:** Content Agent writes a JPEG under the user's storage prefix and a `content_jobs` row with `status=approved` (or `auto_approved`). FastAPI then calls `InstagramAgent.run(AgentState(image_path=..., user_id=..., instagram_account_id=..., caption=...))`. The LLM is out of the process before Graph is touched.

### 13.2 Policy validation (mandatory before any tool)

Reject a content plan if it contains:

- any name in the Instagram publish allowlist
- keys `command`, `code`, `script`, `python`, `shell`, `url`, `http`, `https`, `token`, `access_token`
- tool arguments other than the declared JSON schema
- a request to call Graph, curl, or subprocess

`InstagramAgent._validate_plan` already rejects unknown tools. Keep that as the last gate even if a future planner is miswired.

### 13.3 Caption

V1 does not send captions. V2 daily/festival posts need them. Add an **optional** `caption` argument to `create_image_container` only. The LLM may **propose** caption text; only the Instagram tool sends it to Meta.

---

## 14. Proposed database architecture

Start with **SQLite** (`DATABASE_URL=sqlite:///./data/agentic.db`) so local V2 does not require a Windows Postgres service. Keep the schema portable to PostgreSQL later (SQLAlchemy 2.x + Alembic).

### 14.1 Tables

**users**

- `id` (uuid pk)
- `email` unique
- `password_hash`
- `created_at`, `updated_at`

**sessions** (or JWT with rotation table)

- `id`, `user_id` fk, `refresh_token_hash`, `expires_at`

**instagram_accounts**

- `id` uuid
- `user_id` fk
- `ig_user_id` (Graph account id)
- `username` nullable
- `access_token_encrypted`  ← never returned to React, never passed to LLM
- `token_expires_at`
- `graph_base_url` (facebook vs instagram host)
- `is_active`
- unique `(user_id, ig_user_id)`

**content_jobs**

- `id` uuid
- `user_id`, `instagram_account_id`
- `source`: `prompt` \| `daily` \| `festival`
- `user_prompt` / `brief`
- `image_prompt`, `caption`
- `local_image_path`, `preview_url`
- `status`: `draft` \| `generating` \| `preview` \| `awaiting_approval` \| `approved` \| `rejected` \| `publishing` \| `published` \| `failed`
- `festival_id` nullable
- `scheduled_for` nullable
- `error_code`, `error_message`
- timestamps

**agent_runs** (maps 1:1 to today's `AgentState.task_id`)

- `id` = `task_id`
- `user_id`, `instagram_account_id`, `content_job_id` nullable
- `kind`: `publish` \| `content`
- `status`, `current_step`
- `image_path`, `image_url`, `caption`
- `instagram_container_id`, `instagram_media_id`, `permalink`
- `publish_certainty`, `skip_publish`
- `error` JSON
- `created_at`, `updated_at`, `completed_at`

**agent_events** (timeline)

- `id`, `run_id`
- `seq`
- `tool`, `purpose`, `status`, `decision`
- `duration_ms`, `error_code`
- `observation_summary` JSON
- `transition_from`, `transition_to`
- `created_at`

**posts** (history + counts)

- `id`
- `user_id`, `instagram_account_id`, `run_id`, `content_job_id`
- `instagram_media_id` unique where not null
- `source`, `festival_id`
- `published_at`
- `local_image_path`, `caption`

**schedules**

- `id`, `user_id`, `instagram_account_id`
- `enabled`
- `posts_per_day` default 1
- `timezone` default `Asia/Kolkata`
- `preferred_hour_local`

**festivals**

- `id`
- `name` (e.g. Diwali, Holi, Eid, Christmas, Republic Day, Independence Day, Ganesh Chaturthi, Navratri, Dussehra, Onam)
- `region` default `IN`
- `starts_on`, `ends_on` (date)
- `min_posts` default 2
- `prompt_template`

**festival_subscriptions**

- `user_id`, `instagram_account_id`, `festival_id`
- `enabled`, `posts_required`

**daily_post_counters** (idempotency)

- unique `(instagram_account_id, local_date)`
- `published_count`
- Prevents scheduler double-fire after restart

### 14.2 Persistence of the current TaskStore

Replace the dict in `TaskStore` with:

1. Write `agent_runs` + `agent_events` on every `save()`
2. Keep in-process `asyncio.Queue` subscribers for SSE
3. `get()` reads DB (or memory cache + DB)

V1 API can stay in-memory-compatible if `user_id` is nullable for the demo route until auth is enforced.

### 14.3 Isolation rules

Every query for jobs, runs, posts, media metadata filters `user_id = current_user.id`.  
Graph tokens are loaded only inside `InstagramGraphClient` construction on the server.

---

## 15. Proposed authentication architecture

### 15.1 App users (dashboard)

- Register / login against `users.password_hash` (passlib/bcrypt or argon2)
- HTTP-only secure cookie **or** Bearer JWT issued by FastAPI
- FastAPI `Depends(get_current_user)` on all mutating routes and all task/history routes

**Where to add:**

- New router `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`
- Dependency used by `POST /instagram/publish`, `GET /tasks/*`, future content/schedule routes
- Leave `GET /api/v1/health` public
- Leave `GET /api/v1/media/{filename}` public **by design** (Meta must fetch). Mitigate with unguessable object keys (already uuid) + optional short-lived signed URLs later

### 15.2 Instagram account connection

Do **not** keep a single process env token as the product model.

- `GET /api/v1/instagram/oauth/start` → Meta OAuth
- `GET /api/v1/instagram/oauth/callback` → exchange code, store encrypted user-scoped token + `ig_user_id`
- Env `META_ACCESS_TOKEN` remains a **dev fallback** for V1 tests/scripts only

`create_app` must stop constructing one global client from env for authenticated product routes.

### 15.3 Token hygiene

- Encrypt at rest (`ACCOUNT_TOKEN_FERNET_KEY`)
- Redaction already exists in logs — keep using it
- Never put Meta or OpenAI tokens in React, SSE payloads, or `Observation.data`

---

## 16. Proposed OpenAI architecture

OpenAI lives **only** in the FastAPI backend.

```text
React  --prompt JSON-->  FastAPI Content route
                           → Content Agent
                             → services/openai_client.py
                                chat.completions / images.generate
                             → structured JSON {image_prompt, caption, tool}
                             → PolicyValidation
                             → ToolRegistry.get("generate_image")
```

**Rules:**

1. Browser never holds `OPENAI_API_KEY`.
2. No function-calling that includes Instagram tools.
3. Model output is JSON matching a Pydantic schema. Extra keys dropped.
4. Image bytes are stored locally via `ImageStorageProvider`; OpenAI URLs are downloaded server-side then discarded.
5. `LLMPlanner` in V1 must **not** be pointed at OpenAI with `ALLOWED_TOOLS` unchanged — that allowlist includes `publish_instagram_media`.
6. Rate-limit and cost caps per `user_id`.

**New files (later OpenAI agent, not Agent 2):**

- `services/openai_client.py`
- `tools/content_generate.py` (caption + image prompt + image)
- `agent/content_agent.py`
- `agent/content_planner.py`
- `agent/content_recovery.py`

---

## 17. Proposed scheduler architecture

New package `scheduler/` running inside FastAPI lifespan (APScheduler AsyncIOScheduler) **or** a dedicated worker later. First version: in-process, timezone `Asia/Kolkata`.

**Daily retail/business content**

- Tick: every N minutes, select accounts with `schedules.enabled`
- Idempotency: `daily_post_counters` must be `< posts_per_day` (default 1) for that local date
- Create `content_jobs.source=daily`
- Content Agent generates image + caption
- Policy: auto-approve daily jobs (product default) **or** require approval if user setting says so
- InstagramAgent publishes
- Increment counter only after `TaskStatus.COMPLETED` and verified media id
- If `AMBIGUOUS_PUBLICATION`, do **not** increment as success and do **not** enqueue a second publish that day without operator review

**Festival automation**

- Nightly or hourly: festivals where `starts_on <= today <= ends_on`
- For each subscription, `COUNT(posts)` for that festival must reach `min_posts` (default 2)
- Spread posts across the festival window; never dump two publishes in one recovery retry
- Same Content Agent → InstagramAgent path
- Festival prompt templates stay in DB, not in the LLM system prompt as executable tools

**Crash safety:** scheduler creates a job row first (`status=generating`). A duplicate tick seeing an in-flight row for the same `(account, local_date, source)` must no-op.

---

## 18. Proposed React architecture

New `frontend/` (Vite + React + TypeScript). Do not grow `api/static/index.html` into the product UI.

**Dev:** Vite proxy `/api` → `http://127.0.0.1:8000`  
**Prod:** FastAPI serves `frontend/dist` **or** compose two services with CORS `CORS_ORIGINS`

### Screens

1. Login / register
2. Connect Instagram account
3. Prompt studio: textarea → generate → preview image/caption → Approve / Reject
4. Publish timeline: reuse SSE `GET /api/v1/tasks/{task_id}/events` (same `steps[]` contract)
5. History: post count, media ids, festival vs daily vs prompt
6. Schedule + festival toggles

### API the frontend should consume

Keep V1:

- `POST /api/v1/instagram/publish` (manual upload still valid)
- `GET /api/v1/tasks/{id}`
- `GET /api/v1/tasks/{id}/events`

Add later:

- `/api/v1/auth/*`
- `/api/v1/instagram/accounts`
- `/api/v1/content/jobs` (create from prompt, approve, list)
- `/api/v1/posts` (history + counts)
- `/api/v1/schedules`
- `/api/v1/festivals`

React must not call OpenAI or Graph.

---

## 19. Risks

### 19.1 Security (current)

| Risk | Severity | Notes |
| --- | --- | --- |
| No authentication | High | Anyone who can reach the port can publish with the env token |
| Guessable-but-uuid task ids are world-readable | High once exposed | `GET /tasks/{id}` and SSE have no owner check |
| Process-wide Meta token | High for multi-user | All requests share `META_ACCESS_TOKEN` |
| Public media route | Medium | Required for Meta fetch; keys are uuids; no expiry |
| `asyncio.create_task` publish | Medium | Untracked tasks; lost on process restart; can still complete a live publish |
| No CORS yet | Low | Fine for same-origin demo; required and must be strict for React |
| Bind `0.0.0.0` in Docker | Medium | Expected for containers; do not expose without auth |
| `.env` on disk | Info | gitignored; never commit; Docker uses `env_file` |

V1 mitigations that **must remain:** upload size/MIME limits, generated filenames, temp cleanup, Graph host allowlist, no redirects, log redaction, tool allowlist, executor exception scrubbing.

### 19.2 Product / architecture

| Risk | Mitigation |
| --- | --- |
| Wiring `LLMPlanner` to OpenAI with current `ALLOWED_TOOLS` would let the model **propose** `publish_instagram_media` | Split allowlists before enabling OpenAI |
| Layer inversion: Graph HTTP lives in `tools/instagram_media.py`, re-exported by `services/` | Later extract client to `services/` without changing tool names |
| `ToolRegistry` ↔ `planner.ALLOWED_TOOL_SET` coupling | Keep a shared constants module; avoid circular imports (`registry` already imports `planner`) |
| `agent/__init__.py` eager-imports `InstagramAgent` | New packages should not import `agent` from tools/models |
| In-memory `_published_containers` | Persist container→media on `agent_runs` so restart cannot double-publish |
| Prepared files deleted; hosted files in `storage/` accumulate | User-scoped retention job later |
| Captionless V1 vs branded V2 posts | Additive caption field only |
| Festival double-post after ambiguous publish | Same verify-first policy; festival counter only on verified posts |
| Scheduler + uvicorn `--reload` duplicate workers | Document single worker for in-process scheduler |
| `get_agent()` lru_cache in `api/dependencies.py` | Unused; dangerous if adopted — one shared agent. Delete or replace when touching dependencies |

### 19.3 Circular-dependency map (current)

There is **no hard import cycle** today, but the graph is tight:

```text
api.app → agent.agent → tools.* → models, config
api.app → services.instagram_client → tools.instagram_media
agent.registry → agent.planner.ALLOWED_TOOL_SET
agent.agent → agent.{planner, registry, executor, recovery, state_machine}
models stays leaf (good)
```

**Do not** let `tools/` import `agent/`.  
**Do not** let `services/openai_client.py` import Instagram tools.  
**Do not** let `models/` import FastAPI or OpenAI.

Future split: `agent/constants.py` for allowlists so `registry` does not import `planner`.

---

## 20. Recommended implementation order

1. **Persistence foundation (Agent 2)** — schema, SQLAlchemy, `TaskStore` durability, additive `AgentState` fields, keep V1 publish green  
2. **Authentication + user isolation** — users/sessions; scope tasks; stop anonymous publish in product mode  
3. **Per-user Instagram OAuth / account records** — Graph client factory from `instagram_accounts`  
4. **Local storage namespacing** — `storage/{user_id}/...`  
5. **Content Agent + OpenAI tools** — still no Instagram tools on the LLM allowlist  
6. **Preview + approval API** — then call existing `InstagramAgent`  
7. **React dashboard** — auth, preview, SSE timeline, history  
8. **Scheduler daily cap** — 1 verified post/day/account  
9. **Festival catalog + min 2 posts**  
10. **Hardening** — signed media URLs, token encryption, Alembic migrations, optional Postgres

Never reorder 5 before 1–3 in a way that lets OpenAI run with the process-wide Meta token.

---

## Reusable code (summary)

Reuse as-is:

- Entire observe–decide–act loop in `InstagramAgent`
- Deterministic publish plan
- Tool contract, observations, recovery, state machine
- Image validate/prepare/host
- Graph client error mapping and publish-once semantics
- SSE `TaskStore.subscribe`
- Redacting logger
- Test helpers / mocked Graph suite
- Demo multipart publish route as the InstagramAgent HTTP adapter

---

## Code that must NOT be replaced

- Duplicate-protection (`skip_publish`, UNKNOWN certainty, verify-first, no second `media_publish`)
- Tool allowlist enforcement
- Graph HTTPS host allowlist
- Verifier “id + IMAGE type” rule
- Temp-file cleanup after runs
- Default pytest isolation from live Meta

---

## Agent 2 — exact implementation scope

Agent 2 implements **persistence and schema only**. No OpenAI, no React, no scheduler, no festivals, no real login UI, no behavior change to Instagram publishing.

### Implement

1. Add a `db/` package: engine, session, SQLAlchemy models matching §14 (all tables may be created now even if unused).
2. Use SQLite by default via `DATABASE_URL` in `config.py` / `.env.example`. Create `data/` (gitignored).
3. Add Alembic **or** `create_all` on startup for local SQLite; document the choice.
4. Keep the `TaskStore` public interface (`save`, `get`, `subscribe`). Persist `agent_runs` + `agent_events` on `save()`. Reload `get()` from DB. Keep in-process SSE queues.
5. Extend `AgentState` **additively** with optional `user_id`, `instagram_account_id`, `content_job_id`, `caption`, `source`. Defaults must leave V1 tests passing with `user_id=None`.
6. On publish COMPLETED with a media id, upsert a `posts` row (nullable `user_id` allowed in V1).
7. Wire `create_app` lifespan to open/close the DB. Do **not** switch the Graph client model yet; env token remains V1 behavior.
8. Add tests for store restart durability (save → new TaskStore/DB session → get). Do not remove existing tests.
9. Update `requirements.txt` only with the persistence libraries used.
10. Leave `RecoveryPolicy`, Instagram tools, demo UI, and publish recovery tests unchanged.

### Do not implement

- JWT/login routes (schema for `users` is OK; no auth enforcement yet)
- OpenAI client or content tools
- React app
- APScheduler / festival jobs
- Encrypting tokens / OAuth
- Splitting `ALLOWED_TOOLS` (document only; Agent 2 may add `agent/constants.py` if needed to avoid cycles, without changing the 6-name publish list)
- Deleting `agent/state.py`, `api/dependencies.py`, or any test

### Done when

- `pytest` is green
- V1 `POST /api/v1/instagram/publish` still validates → prepares → hosts → creates → publishes → verifies
- Restarting the process no longer loses task history that was saved
- Subsequent agents can attach `user_id` and query `posts` / `agent_events` without rewriting the Instagram Agent
