# Instagram Agentic AI

Multi-user Instagram content studio: React dashboard, FastAPI backend, OpenAI content plans and images, local storage, human or automatic approval, and a deterministic Instagram publishing Agent.

The Instagram Agent is the only publisher. The LLM never calls Meta APIs.

## Architecture

```text
React
  → FastAPI
    → Authentication (JWT + httpOnly cookie)
      → Content Agent
        → LLM (OpenAI, backend-only)
          → Image generation (OpenAI, backend-only)
            → Local storage
              → Approval (manual) or auto-approve (automation)
                → Instagram Agent
                  → Verification
                    → Database
                      → Dashboard
```

V1 upload path is preserved:

```text
Upload image → validate → prepare → host → create media → publish → verify
POST /api/v1/instagram/publish
```

That endpoint still accepts a JPEG/PNG multipart upload. It now requires a logged-in user so posts are isolated per account.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full map.

## Installation

Python 3.11+ and Node 20+ are required.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

cd frontend
npm install
```

Fill `.env` (never commit it). Generate a JWT secret and token encryption key before any real use.

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Environment variables

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Backend-only OpenAI secret. Never `VITE_*` / `NEXT_PUBLIC_*` |
| `LLM_PROVIDER` | `openai` (default). Empty key uses in-process mocks |
| `LLM_MODEL` | Chat model name; required for live LLM |
| `IMAGE_PROVIDER` | `openai` (default) |
| `IMAGE_MODEL` | Image model name; required for live image generation |
| `META_ACCESS_TOKEN` | Fallback Graph token for V1-style publish when a user has no connected account |
| `INSTAGRAM_ACCOUNT_ID` | Fallback Instagram professional account id |
| `META_GRAPH_API_BASE_URL` | `https://graph.facebook.com` or `https://graph.instagram.com` |
| `META_API_VERSION` | Default `v26.0` |
| `DATABASE_URL` | Default `sqlite:///./data/agentic.db` |
| `JWT_SECRET` | Signs access tokens |
| `JWT_EXPIRE_MINUTES` | Default `480` |
| `JWT_COOKIE_SECURE` | Set `true` behind HTTPS |
| `TOKEN_ENCRYPTION_KEY` | Encrypts per-user Instagram tokens |
| `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` | Bootstrap admin; forced password change |
| `CORS_ORIGINS` | Comma-separated browser origins |
| `SCHEDULER_ENABLED` | `true` to run the in-process daily/festival loop |
| `SCHEDULER_INTERVAL_SECONDS` | Tick interval, default `60` |
| `DEFAULT_TIMEZONE` | Default `Asia/Kolkata` |
| `IMAGE_PUBLIC_BASE_URL` | Public HTTPS origin Meta can fetch |
| `MAX_IMAGE_SIZE_MB` | Upload cap, default `8` |

Missing `OPENAI_API_KEY` with a configured live model returns structured `OPENAI_CONFIGURATION_ERROR` (HTTP 503). Default tests never construct a real OpenAI client.

## OpenAI setup

1. Create an OpenAI API key.
2. Put it only in the backend `.env` as `OPENAI_API_KEY`.
3. Set `LLM_MODEL` and `IMAGE_MODEL` to the models your account can call.
4. Restart FastAPI. React never reads this key.

Without a key, the API still runs using `MockLLMProvider` and `MockImageGenerationProvider` so local UI and pytest work.

## Meta setup

Use Meta's Instagram Content Publishing API. Do not automate instagram.com with a username/password.

1. Create a Meta app.
2. Instagram professional account (Business or Creator).
3. Permissions depend on login path:
   - Facebook Login: `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`
   - Instagram Login: `instagram_business_basic`, `instagram_business_content_publish`
4. Complete OAuth. Each dashboard user should connect their own account via `POST /api/v1/instagram/connect` (token is stored encrypted). `META_ACCESS_TOKEN` remains a V1 fallback for the upload route.

The image URL Meta fetches must be public HTTPS. `http://127.0.0.1` is not valid for a real post.

## Database

SQLite is the local default (`data/agentic.db`). Tables are created on app startup (`init_db`). Alembic lives under `db/migrations/` for later PostgreSQL moves:

```text
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/agentic
alembic upgrade head
```

User isolation, unique daily slots, unique Instagram media ids, and festival `(campaign, sequence)` uniqueness are enforced in SQLAlchemy models. See [DATABASE.md](DATABASE.md).

## Authentication

bcrypt password hashes, JWT access tokens, httpOnly cookie `access_token`, and revocable `sessions` rows. Product routes require `require_password_ok`. Bootstrap admins must change `DEFAULT_ADMIN_PASSWORD` on first login.

See [AUTHENTICATION.md](AUTHENTICATION.md).

## Daily automation

Enable `daily_enabled` and `auto_daily_publish` on `PUT /api/v1/automation`. The scheduler (timezone Asia/Kolkata unless overridden) generates one retail image, auto-approves, and hands it to the Instagram Agent.

Running the scheduler twice on the same local date produces **one** successful daily post. Duplicate protection: `daily_post_slots` unique `(user, account, date)` plus a partial unique index on published `DAILY_RETAIL_POST` rows.

`POST /api/v1/automation/run-now` forces a tick for the current user. See [SCHEDULER.md](SCHEDULER.md).

## Festival automation

Each campaign defaults to `required_posts = 2`: one pre-festival day, one festival day. Only verified publications increment `published_posts`. Failures do not reset the campaign.

```text
required = 2, published = 1  → remaining = 1
required = 2, published = 2  → campaign completed
```

Calendar: [festivals/india_festivals.py](festivals/india_festivals.py) plus [festivals/data/lunar_dates.json](festivals/data/lunar_dates.json). See [FESTIVAL_AUTOMATION.md](FESTIVAL_AUTOMATION.md).

## React

Vite + TypeScript dashboard in `frontend/`.

```bash
cd frontend
npm run dev
```

Open http://localhost:5173 (proxies `/api` to FastAPI in dev). Production Docker serves the built SPA on port 3000 and reverse-proxies `/api/` to the backend.

The UI never reads `OPENAI_API_KEY` or `META_ACCESS_TOKEN`. Manual generate never publishes until **Approve & Post**. See [FRONTEND.md](FRONTEND.md).

## Docker

Do not put secrets in Dockerfiles. Pass them through `.env` or Compose environment.

```bash
copy .env.example .env
docker compose up --build
```

- Backend: http://localhost:8000 (`GET /api/v1/health`)
- Frontend: http://localhost:3000
- SQLite file: Docker volume `agent-storage`

`docker-compose.yml` reads `.env` when present (`required: false`). Compose still sets a development JWT secret if you have not provided one — replace it before any shared deployment.

## Testing

```bash
pytest
cd frontend
npm test
npm run build
```

Default pytest is fully mocked (OpenAI and Meta). Real OpenAI tests are marked `real_openai` and are excluded by `pytest.ini`. Real Graph tests require explicit selection.

Coverage that was re-run in the final integration pass:

- Register / login / business / generate / approve / Instagram / dashboard
- Daily scheduler twice → one published post
- Festival required_posts=2 → two verified posts, then complete
- Festival post 1 success + post 2 failure → published=1, remaining=1
- Publish timeout → `AMBIGUOUS_PUBLICATION`, no second publish, no count
- User A cannot read User B images, posts, business, automation, festivals, Instagram, tasks, events
- V1 `POST /api/v1/instagram/publish`

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `OPENAI_CONFIGURATION_ERROR` | `OPENAI_API_KEY` and `LLM_MODEL` / `IMAGE_MODEL` on the backend |
| `INSTAGRAM_NOT_CONNECTED` | Connect an account in the dashboard, or set Meta env vars for V1 fallback |
| Duplicate daily post skipped | Expected: one verified post per account per local date |
| Festival remaining stays 1 | Second slot not yet due, same-day block, or second publish `FAILED`/`AMBIGUOUS_PUBLICATION` |
| SQLite `database is locked` | Single uvicorn worker; scheduler commits before the Agent opens another connection |
| Meta cannot fetch the image | `IMAGE_PUBLIC_BASE_URL` must be public HTTPS |
| React 401 after login | Cookie + CORS origin must match; `credentials: include` |
| Docker engine errors | Start Docker Desktop, then `docker compose up --build` |

## Security

- Secrets live in environment variables. Source and Dockerfiles do not hard-code API keys.
- `services/logging.py` redacts passwords, JWTs, `Authorization`, OpenAI keys, and Meta tokens.
- Instagram user tokens are Fernet-encrypted at rest.
- The Content Agent rejects plans that mention `os.system`, `subprocess`, `eval(`, or `exec(`.
- The Instagram Agent executes only the six registered tools.
- Generated-image paths are owner-scoped and sanitized.

## API endpoints

| Method | Path | Auth |
| --- | --- | --- |
| `GET` | `/api/v1/health` | public |
| `POST` | `/api/v1/auth/register` | public |
| `POST` | `/api/v1/auth/login` | public |
| `POST` | `/api/v1/auth/logout` | authenticated |
| `GET` | `/api/v1/auth/me` | authenticated |
| `POST` | `/api/v1/auth/change-password` | authenticated |
| `GET`/`PUT` | `/api/v1/business` | authenticated |
| `POST`/`GET` | `/api/v1/generation` | authenticated |
| `GET` | `/api/v1/generation/{image_id}` | owner |
| `POST` | `/api/v1/generation/{image_id}/approve` | owner |
| `POST` | `/api/v1/generation/{image_id}/reject` | owner |
| `POST` | `/api/v1/generation/{image_id}/regenerate` | owner |
| `GET` | `/api/v1/media/generated/{image_id}` | owner |
| `GET` | `/api/v1/posts` | owner |
| `GET`/`PUT` | `/api/v1/automation` | authenticated |
| `POST` | `/api/v1/automation/run-now` | authenticated |
| `GET` | `/api/v1/festivals` | authenticated |
| `GET` | `/api/v1/festivals/campaigns` | owner |
| `PUT` | `/api/v1/festivals/settings` | authenticated |
| `GET` | `/api/v1/instagram/status` | authenticated |
| `POST` | `/api/v1/instagram/connect` | authenticated |
| `POST` | `/api/v1/instagram/disconnect` | authenticated |
| `POST` | `/api/v1/instagram/publish` | authenticated (V1 upload) |
| `GET` | `/api/v1/dashboard` | authenticated |
| `GET` | `/api/v1/tasks/{task_id}` | owner |
| `GET` | `/api/v1/tasks/{task_id}/events` | owner (SSE) |
| `GET` | `/api/v1/admin/users` | admin |
| `GET` | `/api/v1/settings` | authenticated |

## How to run locally

```bash
.venv\Scripts\activate
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm run dev
```

Set `SCHEDULER_ENABLED=true` only with a single worker if you want unattended daily/festival posts.

## Related documents

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [AUTHENTICATION.md](AUTHENTICATION.md)
- [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md)
- [MEDIA_STORAGE.md](MEDIA_STORAGE.md)
- [CONTENT_AGENT.md](CONTENT_AGENT.md)
- [SCHEDULER.md](SCHEDULER.md)
- [FESTIVAL_AUTOMATION.md](FESTIVAL_AUTOMATION.md)
- [INSTAGRAM_AGENT.md](INSTAGRAM_AGENT.md)
- [FRONTEND.md](FRONTEND.md)
- [DATABASE.md](DATABASE.md)
- [FINAL_IMPLEMENTATION_REPORT.md](FINAL_IMPLEMENTATION_REPORT.md)
