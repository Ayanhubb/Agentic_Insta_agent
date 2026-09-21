# Database layer

SQLite is the local default. The schema is SQLAlchemy 2.x and is intended to move to PostgreSQL later by changing `DATABASE_URL` only.

## Local setup

```text
DATABASE_URL=sqlite:///./data/agentic.db
TOKEN_ENCRYPTION_KEY=<fernet-or-passphrase>
```

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Any non-empty `TOKEN_ENCRYPTION_KEY` also works: the encryptor derives a Fernet key from SHA-256. Plaintext Instagram tokens are never stored.

Initialize tables:

```bash
python scripts/init_db.py
```

`create_app()` also calls `init_db()` on startup. Alembic is available for later PostgreSQL migrations:

```bash
alembic upgrade head
```

Default file path if `DATABASE_URL` is empty: `data/agentic.db`.

PostgreSQL later:

```text
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/agentic
```

## Boundaries

| Layer | Module | Responsibility |
| --- | --- | --- |
| Engine / session | `db/session.py` | Engine, `init_db`, `get_db`, `bootstrap_database` |
| ORM | `db/models.py` | Tables listed below |
| Pydantic | `db/schemas.py` | Create/read DTOs |
| Repositories | `db/repositories.py` | All queries. Not FastAPI routes |
| Unit of work | `db/uow.py` | `Database(session, encryptor)` |
| Agent snapshot | `db/persist.py` | `AgentState` → `agent_tasks` / `agent_events` / `instagram_posts` |
| Token crypto | `db/crypto.py` | `TokenEncryptor`, `encrypt_token`, `decrypt_token` |

Do not put SQL in FastAPI route handlers. Use `Database` or a repository.

## Tables

Required product tables:

- `users`
- `instagram_accounts`
- `business_profiles`
- `generated_images`
- `instagram_posts`
- `agent_tasks`
- `agent_events`
- `scheduled_jobs`
- `festival_campaigns`
- `festival_posts`
- `automation_settings`

Supporting tables (used by auth/scheduler without changing the Instagram Agent):

- `sessions` — JWT `jti` rows (`AuthSession`)
- `daily_post_slots` — scheduler claim/idempotency for a local date

`instagram_posts.scheduled_date` is required for `DAILY_RETAIL_POST` duplicate protection. It is the calendar date in the account timezone (`Asia/Kolkata` by default).

`agent_tasks.id` is the V1 `AgentState.task_id` string. `agent_tasks.state_json` holds a full `AgentState` snapshot so `TaskStore.get()` can reload after process restart.

`instagram_accounts.instagram_account_id` is Meta’s IG user id. `instagram_posts.instagram_account_id` is a foreign key to `instagram_accounts.id`.

## Publication counting

Only `status = PUBLISHED` on a **verified** row counts.

These do **not** count:

- `GENERATED`
- `APPROVED`
- `PUBLISHING`
- `FAILED`
- `AMBIGUOUS_PUBLICATION`

`InstagramPostRepository.create(..., status=PUBLISHED)` requires `verified=True` and `instagram_media_id`.

Partial unique index `uq_ig_posts_daily_published`:

```text
UNIQUE (user_id, instagram_account_id, scheduled_date)
WHERE status = 'PUBLISHED' AND post_type = 'DAILY_RETAIL_POST'
```

Failed / generated / ambiguous daily attempts do not block a later verified publish on that date. A second verified daily publish for the same user/account/date raises `DuplicateRecordError`.

Festival posts are independent (`festival_posts` + `post_type=FESTIVAL`). They do not use the daily unique index. `FestivalCampaign.published_posts` is refreshed only from `festival_posts.status = PUBLISHED`.

`scheduled_jobs` is unique on `(user_id, instagram_account_id, job_type)` so two autonomous daily jobs cannot be attached to the same account.

## `TaskStore`

`TaskStore.save` / `get` / `subscribe` is unchanged for callers. SSE queues stay in-process.

Durability is **`persist_agent_state(session, state)`**, not `TaskStore.save()`. `save(..., persist=True)` is opt-in and only writes on `COMPLETED`/`FAILED`. Do not persist from `TaskStore` while a request or scheduler session still holds the SQLite write lock. `get()` can reload `state_json` after restart.

V1 publish still uses the Instagram Agent. Persistence is additive. `user_id` on tasks/posts is nullable until auth attaches a user.

## Agent 3 — available interfaces

Import from `db` or `db.repositories`.

### Session / FastAPI

```python
from db.session import get_db, bootstrap_database, session_factory, init_db
from db.uow import Database, get_database
from db.crypto import TokenEncryptor, encrypt_token, decrypt_token
```

`create_app` already runs `init_db`, `bind_runtime`, and `TaskStore(session_factory=...)`.

`auth.deps.get_db` yields a SQLAlchemy `Session` from `app.state.session_factory`.

### Unit of work

```python
db = Database(session, encryptor=TokenEncryptor.from_settings(settings))
db.users
db.instagram_accounts
db.business_profiles
db.generated_images
db.instagram_posts
db.agent_tasks
db.agent_events
db.scheduled_jobs
db.festival_campaigns
db.festival_posts
db.automation_settings
db.sessions
db.daily_slots
db.commit()
```

### Repositories (canonical names and aliases)

| Canonical | Alias used by other agents | Notes |
| --- | --- | --- |
| `UserRepository` | | `create(UserCreate)` or `create(email=..., password_hash=..., is_admin=...)`, `get_by_id`, `get_by_email`, `list_all`, `list_active` |
| `SessionRepository` | | `create(user_id, token_hash, expires_at)`, `get_valid(jti)`, `count_for_user`, `revoke`, `revoke_all_for_user` |
| `InstagramAccountRepository` | | `create(InstagramAccountCreate)` encrypts plaintext; `upsert(user_id, ig_id, encrypted)` stores a pre-encrypted token; `get_primary`, `get_owned`, `get_access_token`, `disconnect` |
| `BusinessProfileRepository` | `BusinessRepository` | `get_for_user`, `upsert(user_id, BusinessProfileWrite)` or `upsert(user_id, **fields)` |
| `GeneratedImageRepository` | | `create(GeneratedImageCreate)` or kwargs; `get_owned`; `list_for_user(user_id, limit=)` |
| `InstagramPostRepository` | `PostRepository` | `create`, `count_published(user_id, since=)`, `has_published_daily`, `list_for_user` |
| `AgentTaskRepository` | `TaskRepository` | `upsert` / `create(**kwargs)`, `get_owned`, `events_for_task` |
| `AgentEventRepository` | | `append`, `list_for_task`, `replace_for_task` |
| `ScheduledJobRepository` | `JobRepository` | `create`, `upsert(user_id, job_type, **fields)` |
| `FestivalCampaignRepository` | `FestivalRepository` | `create`, `get_or_create_campaign`, `list_campaigns`, `remaining_posts` on the model |
| `FestivalPostRepository` | | `create`, `list_for_campaign`, `mark_published` |
| `AutomationSettingsRepository` | `AutomationRepository` | `get_or_create(user_id, timezone=None)`, `upsert` |
| `DailySlotRepository` | | `get`, `claim` for scheduler idempotency |

### Schemas

`db.schemas`: `UserCreate`, `UserRead`, `UserRecord`, `InstagramAccountCreate`, `InstagramAccountRead`, `BusinessProfileWrite`, `GeneratedImageCreate`, `InstagramPostCreate`, `AgentTaskCreate`, `AgentEventCreate`, `ScheduledJobCreate`, `FestivalCampaignCreate`, `FestivalPostCreate`, `AutomationSettingsWrite`.

`UserRead` does not include `password_hash`. `UserRecord` does. Never return `access_token_encrypted` or decrypted tokens to React.

### Errors

- `DuplicateRecordError` — unique constraint (duplicate email, daily publish, job, festival campaign)
- `RecordNotFoundError`
- `TokenEncryptionError` — missing key or decrypt failure

Map these to HTTP in auth/platform routes. Do not catch them inside repositories.

### Enums (`db.enums`)

`AccountStatus`, `ImageSource` (`USER_PROMPT`, `DAILY_AUTOMATION`, `FESTIVAL_AUTOMATION`), `GenerationStatus`, `ApprovalStatus`, `PostStatus`, `PostType` (`USER_PROMPT`, `DAILY_RETAIL_POST`, `FESTIVAL`), `TaskType`, `TaskTrigger`, `JobType`, `FestivalPostStatus`.

ORM status columns are VARCHAR so later agents may pass compatible strings (`PUBLISHING`, `PUBLISH`, …). Canonical published value remains `PUBLISHED`.

### What this layer does not do

Authentication routes, password hashing, JWT issuance, OpenAI calls, React, and the scheduler loop are not implemented here. Tables and repositories exist so those agents can attach without rewriting the Instagram Agent.
