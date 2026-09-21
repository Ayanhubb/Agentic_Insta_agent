# Instagram Agent Publication Architecture

The Instagram Agent is the **only publication authority** in this system. The LLM and the autonomous scheduler never call Instagram APIs. They create Agent tasks. The Agent then runs the existing, already-tested tool chain.

## Authority

```text
React / API / Content Agent / Scheduler
        │
        │  create Agent task only
        ▼
PublicationGateway  (services/publication.py)
        │
        ▼
Instagram Agent
        │
        ▼
Allowlisted tools (never rewritten for this integration)
        │
        ▼
Meta Graph API
```

Public entry points:

- `POST /api/v1/instagram/publish` — V1 upload path, preserved
- `enqueue_instagram_publication(...)` — Content Agent / Scheduler / approval flow
- `enqueue_from_handoff(handoff)` — consumes `InstagramHandoff` from the Content Agent

The Content Agent produces an `InstagramHandoff`. It does not publish. The Scheduler must call `enqueue_instagram_publication` or `enqueue_from_handoff`. It must not import `tools.instagram_media` or `InstagramGraphClient`.

## Existing tools (unchanged)

The Agent still plans and executes only these tools:

1. `validate_image`
2. `prepare_image`
3. `upload_image`
4. `create_instagram_media`
5. `publish_instagram_media`
6. `verify_publication`

Generated images use the same sequence as manual uploads:

```text
Generated image
  → validate
  → prepare
  → upload / host
  → create Instagram media
  → publish
  → verify
  → database record
  → statistics
```

`LLMPlanner` cannot invent tools. Production planning remains `DeterministicPlanner`. The LLM never receives an Instagram access token and never executes `media_publish`.

## User-specific Instagram accounts

Do not use one global `META_ACCESS_TOKEN` for every authenticated user.

| Caller | Credentials |
| --- | --- |
| Unauthenticated V1 `POST /instagram/publish` | Environment token (backward compatible) |
| Authenticated user | That user's connected Instagram professional account |
| Daily / festival automation | The enabled account belonging to that user |

Tokens are encrypted at rest with `db.crypto.encrypt_token`. They are never:

- returned to React
- included in SSE/task payloads
- written into `AgentState`
- logged

Account APIs:

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/api/v1/instagram/status` | `{ connected, instagram_account_id, status, stats }` — no token |
| `POST` | `/api/v1/instagram/connect` | `{ instagram_account_id, access_token }` from the authenticated user |
| `POST` | `/api/v1/instagram/disconnect` | Wipes the encrypted token |
| `POST` | `/api/v1/instagram/publish` | Preserved. Authenticated callers use the connected account |

Connect verifies the professional account through `get_account` before storing the token. Authentication or permission failures **STOP** and do not save the account.

## Recovery (preserved)

The existing `RecoveryPolicy` is unchanged:

| Observation | Decision |
| --- | --- |
| Invalid / corrupted image | **STOP** |
| Temporary storage failure | Bounded retry |
| Authentication / permission | **STOP** |
| Short rate limit (`Retry-After` within policy) | Bounded retry |
| Publish timeout | **Do not publish again** |
| Unknown publish result | **VERIFY FIRST** |
| Verification delay | Retry verification only |
| Unconfirmed result | `AMBIGUOUS_PUBLICATION` |

A timeout or unknown `media_publish` result sets `skip_publish` and forces `verify_publication`. The Agent will not call `publish_instagram_media` again for that task.

## Counting

Only a record with:

```text
status = PUBLISHED
verified = true
```

after successful verification may increment:

- total posts
- daily posts
- festival `published_posts` / remaining = required − published

These statuses **do not count**:

- `FAILED`
- `AMBIGUOUS_PUBLICATION`
- `GENERATED`
- `APPROVED`
- `PUBLISHING`

Agent `TaskStatus.COMPLETED` maps to `PUBLISHED` only when a verified Instagram media id exists.

## Duplicate prevention

Before an Agent task is created:

- The same `generated_image_id` cannot enter `PUBLISHING`, `PUBLISHED`, or `AMBIGUOUS_PUBLICATION` twice.
- A user + Instagram account + scheduled date cannot receive two daily posts in those blocking statuses (`DAILY_RETAIL_POST`).
- `FAILED` daily posts may be retried. `AMBIGUOUS_PUBLICATION` must not be retried blindly because the post may already exist on Instagram.

The database unique index on verified daily posts is an additional guard, not a substitute for the Agent-level check.

## Persistence

`PublicationGateway` writes an in-memory publication store used by the process and by tests. Instagram account connect/disconnect persist through SQLAlchemy (`instagram_accounts`). Agent tasks and `instagram_posts` are written by `TaskStore` / `db.persist` from `AgentState` so SQLite is not dual-written.

Festival campaign `published_posts` is incremented only after verified `PUBLISHED`. Statistics ignore every other status until verification succeeds.

## User isolation

Every account, post, task, campaign, and statistic query is scoped by `user_id`.

- User A cannot read User B tasks (`GET /api/v1/tasks/{id}` returns 404).
- User A cannot disconnect User B's Instagram account.
- Graph calls for User A use User A's decrypted token and professional account id, never User B's and never the process-wide env token.

## Scheduler contract

```text
Scheduler
  → check automation / today's verified daily post
  → if already PUBLISHED (or PUBLISHING / AMBIGUOUS): SKIP
  → Content Strategy Agent (plan + image)
  → enqueue_instagram_publication(...)
  → Instagram Agent (tools + recovery)
  → verify
  → persist PUBLISHED
  → increment counts
```

The scheduler must not construct `InstagramGraphClient` or call `media_publish`.

## Failure mapping

| Agent result | Publication status | Counted |
| --- | --- | --- |
| `COMPLETED` + media id + verified | `PUBLISHED` | yes |
| `AMBIGUOUS_PUBLICATION` / unknown publish | `AMBIGUOUS_PUBLICATION` | no |
| Invalid image, permission, auth, exhausted retries | `FAILED` | no |
| Still running | `PUBLISHING` | no |
