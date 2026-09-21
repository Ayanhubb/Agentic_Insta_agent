# Architecture

Instagram Agentic AI is a multi-user content studio. React talks only to FastAPI. FastAPI owns authentication, the Content Agent, OpenAI, local image storage, approval, and the Instagram Agent. The Instagram Agent is the only publisher.

```text
React
  → FastAPI
    → Authentication
      → Content Agent
        → LLM (OpenAI or mock)
          → Image generation (OpenAI or mock)
            → Local storage
              → Approval / automation auto-approve
                → Instagram Agent
                  → Verification
                    → Database
                      → Dashboard
```

V1 upload publishing remains on the same path:

```text
POST /api/v1/instagram/publish
  → validate_image
    → prepare_image
      → upload_image (public JPEG URL)
        → create_instagram_media
          → publish_instagram_media
            → verify_publication
```

That route now requires a logged-in user so publications are scoped to `user_id`. The multipart contract and JSON result shape are unchanged.

## Runtime pieces

| Layer | Location | Role |
| --- | --- | --- |
| React dashboard | `frontend/` | Auth, generate, approve, automation, festivals, Instagram connect, dashboard |
| FastAPI | `api/app.py`, `api/platform_routes.py`, `api/routes.py`, `api/auth_routes.py` | HTTP API |
| Auth | `auth/` | bcrypt, JWT + httpOnly cookie, revocable sessions, admin gate |
| Content Agent | `agent/content_agent.py` | Plans copy/image. Never publishes |
| LLM / images | `ai/` | OpenAI providers on the backend only; mocks when the key is missing |
| Storage | `services/media_storage.py`, `tools/image_storage.py` | Owner-scoped generated files + V1 public host |
| Publication gateway | `services/publication.py` | Sole enqueue path into the Instagram Agent |
| Instagram Agent | `agent/agent.py` | Allowlisted tools, recovery, VERIFY_FIRST |
| Scheduler | `scheduler/` | Daily + festival ticks in Asia/Kolkata |
| Database | `db/` | SQLAlchemy SQLite default; PostgreSQL URL-compatible |
| Verification | `tools/instagram_verifier.py` | Confirms media id + IMAGE type |

## Control vs intelligence

The LLM returns a structured content plan. FastAPI still decides:

- whether the plan is allowed
- whether the image is stored
- whether the user must approve
- whether the Instagram Agent may run
- whether a publication counts

The Content Agent cannot select Instagram tools, run shell commands, or `eval`/`exec` model output. Dangerous tokens in a plan (`os.system`, `subprocess`, `eval(`, `exec(`) are rejected.

## Publication counting

Only `status = PUBLISHED` with an `instagram_media_id` increments dashboard / festival / daily counts.

| Outcome | Counts as published | Same-day retry |
| --- | --- | --- |
| `PUBLISHED` | yes | no (daily) / next campaign slot (festival) |
| `FAILED` | no | yes |
| `AMBIGUOUS_PUBLICATION` | no | no (do not publish again) |

After a publish timeout the Agent verifies first. It does not call `media_publish` again.

## User isolation

Every product row carries `user_id`. Repositories load with `get_owned(user_id, id)`. Tasks, events, generated images, posts, business profiles, automation, festival campaigns, and Instagram accounts are not shared across users.

## Process model

Use a single uvicorn worker when `SCHEDULER_ENABLED=true`. Two processes would both tick. SQLite uses WAL and a busy timeout so the scheduler session can commit before the Instagram Agent opens its own connection.

## Related documents

- [AUTHENTICATION.md](AUTHENTICATION.md)
- [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md)
- [CONTENT_AGENT.md](CONTENT_AGENT.md)
- [MEDIA_STORAGE.md](MEDIA_STORAGE.md)
- [SCHEDULER.md](SCHEDULER.md)
- [FESTIVAL_AUTOMATION.md](FESTIVAL_AUTOMATION.md)
- [INSTAGRAM_AGENT.md](INSTAGRAM_AGENT.md)
- [FRONTEND.md](FRONTEND.md)
- [DATABASE.md](DATABASE.md)
