# Final implementation report

Inspection date: 21 September 2026. Claims below were checked against this repository, pytest, Vitest, the frontend production build, and a live `docker compose up --build`.

## Implemented

Verified in code and tests unless noted.

- **Architecture:** React → FastAPI → auth → Content Agent → LLM → image generation → local storage → approval / automation → Instagram Agent → verification → database → dashboard. The Instagram Agent remains the only publisher (`services/publication.py` → `agent/agent.py` → six allowlisted tools).
- **V1 upload path:** `POST /api/v1/instagram/publish` still accepts a JPEG/PNG multipart file and runs validate → prepare → host → create media → publish → verify. It now requires a logged-in user so posts are scoped to `user_id`. Path and JSON shape are unchanged (`tests/test_platform_flows.py::test_v1_publish_still_works`).
- **Manual flow (FLOW 1):** Register → login → business profile → prompt → Content Agent (mocked OpenAI in default tests) → generated image on disk → preview payload → approve → Instagram Agent → verified `PUBLISHED` → dashboard `published_posts == 1`.
- **Daily automation (FLOW 2):** Enable daily + auto-publish → `POST /api/v1/automation/run-now` twice → one successful daily post (`todays_posts == 1`). Duplicate protection uses `daily_post_slots` plus a partial unique index on published `DAILY_RETAIL_POST` rows.
- **Festival automation (FLOW 3):** Diwali 2026 campaign with `required_posts = 2`: pre-day + festival-day both verified, `remaining_posts = 0`. HTTP test: `test_festival_two_required_posts_and_partial_failure`.
- **Festival partial failure:** First verified post, second `FAILED` → `required = 2`, `published = 1`, `remaining = 1` (`test_festival_second_post_failure_keeps_published_count`). Failures do not reset the campaign.
- **Ambiguous publication (FLOW 4):** Publish timeout / unknown result → `VERIFY_FIRST` → `AMBIGUOUS_PUBLICATION`. No second `media_publish`. Count does not increment (`tests/test_instagram_agent_integration.py`, `tests/test_agentic_workflow.py`, scheduler ambiguous tests).
- **User isolation (FLOW 5):** User B cannot read User A’s images (including media bytes), posts, business profile, automation flags, festival campaign ids, connected Instagram account, tasks, or events.
- **Auth:** bcrypt, JWT, httpOnly cookie, revocable sessions, forced admin password change, admin-only user list.
- **OpenAI:** Key is read only from backend env. React has no `OPENAI_API_KEY` / `VITE_OPENAI_*`. Default pytest monkeypatches `AsyncOpenAI` so a real client is never constructed. Missing live config returns `OPENAI_CONFIGURATION_ERROR` (HTTP 503). Without a key, the app uses mock LLM and mock image providers.
- **Database:** SQLAlchemy models with FKs, unique email, unique Instagram account per user, unique daily slot, unique festival campaign `(user, name, year)`, unique festival post `(campaign, sequence)`, partial unique Instagram media id, published-requires-media-id check. SQLite WAL + busy timeout. Scheduler commits generated rows before the Agent opens another connection.
- **Security scan (project source, not `.venv`):** No hard-coded `OPENAI_API_KEY` or `META_ACCESS_TOKEN` values. Logging redacts passwords, JWTs, `Authorization`, OpenAI, and Meta material. Content Agent rejects plans containing `os.system`, `subprocess`, `eval(`, `exec(`. Application code does not call `os.system` / `subprocess` / `eval` / `exec` to run LLM output. Per-user Instagram tokens are Fernet-encrypted. Dockerfiles contain no secrets.
- **Frontend:** Vitest 19/19 passed. `npm run build` (`tsc` + Vite) succeeded. Manual generate does not auto-publish.
- **Docker:** `docker compose up --build` runs backend (`GET /api/v1/health` → `{"status":"ok"}`) and frontend (HTTP 200 on port 3000). Nginx `/api/` proxy to backend works. Register + login against the container succeeded. Images persist on volume `agent-storage`.
- **Festival package rename:** The former `calendar/` package shadowed stdlib `calendar` inside Docker (`ModuleNotFoundError: calendar.festival_service`). It is now `festivals/`.

## Partially implemented

- **Live OpenAI / live Meta:** Default tests and Docker smoke tests use mocks / dummy clients. Real Graph and OpenAI suites exist (`@pytest.mark.real_openai`, `test_real_instagram.py`) and are not part of default pytest.
- **PostgreSQL:** Schema is URL-portable. This pass verified SQLite only.
- **ESLint / mypy:** Not configured. Python syntax compiles. Ruff is in `requirements.txt`; no F821/undefined-name issues. Unused imports remain (F401). Frontend typecheck is `tsc` via `npm run build`.
- **Leftover route modules:** `api/instagram_account_routes.py` and `api/generation.py` are not mounted (platform routes cover those URLs). Empty `frontend/src/api` and `frontend/src/auth` directories remain after the Agent 9 layout move.
- **V1 demo UI:** `GET /` still serves `api/static/index.html` when present. The product UI is the React app.
- **Environment Instagram fallback:** `POST /api/v1/instagram/publish` may use `META_ACCESS_TOKEN` when `allow_environment_fallback=True` and the user has no connected account. Per-user connect is the dashboard path.
- **Compose JWT default:** If `.env` is missing, Compose sets a development `JWT_SECRET`. Replace before any shared host.
- **Same-day festival posts:** Blocked by default (`allow_same_day_festival_posts=false`). Catch-up is only for campaigns that already started, within 7 days after the festival date.

## Not implemented

- Facebook page posts, Reels, Stories, carousels, captions/hashtags as first-class publish types.
- Multi-worker scheduler (documented: one uvicorn worker when `SCHEDULER_ENABLED=true`).
- Semantic embeddings diversity backend (`SemanticSimilarityBackend.enabled = False`).
- Production HTTPS image hosting for Meta fetch (local public URL is still the operator’s responsibility).

## Known limitations

- SQLite is for local/dev. Concurrent Agent + scheduler writes are mitigated but PostgreSQL is the intended production store.
- Meta cannot fetch `http://127.0.0.1` image URLs. Real publishes need `IMAGE_PUBLIC_BASE_URL` on a public HTTPS host.
- Lunar/Islamic festival dates are a yearly table, not computed. Update `festivals/data/lunar_dates.json`.
- `JWT_SECRET` falls back to `dev-change-me` in `Settings.from_env` if unset (local only).
- Default admin password in `.env.example` is a bootstrap placeholder and forces change on first login.

## Required environment variables

See `.env.example` and README. Minimum for a local mocked studio:

- `JWT_SECRET`
- `TOKEN_ENCRYPTION_KEY`
- `DATABASE_URL` (optional; defaults to `data/agentic.db`)
- `CORS_ORIGINS`

For live generation: `OPENAI_API_KEY`, `LLM_MODEL`, `IMAGE_MODEL`.

For live Instagram: per-user connect token, or `META_ACCESS_TOKEN` + `INSTAGRAM_ACCOUNT_ID`, plus a public `IMAGE_PUBLIC_BASE_URL`.

## How to run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --host 127.0.0.1 --port 8000

cd frontend
npm install
npm run dev
```

Docker:

```bash
docker compose up --build
```

Backend http://localhost:8000 — Frontend http://localhost:3000.

## How to test

```bash
pytest
cd frontend
npm test
npm run build
```

Default pytest never calls OpenAI (`conftest.py` blocks `AsyncOpenAI`; `pytest.ini` excludes `real_openai`).

## Security notes

- Do not put keys in Dockerfiles or frontend env prefixes.
- Logs go through `services.logging.redact_text`.
- LLM output is JSON for a content plan, not executable code.
- Instagram Agent tools are allowlisted; RecoveryPolicy will not republish after an unknown result.
- User isolation is `user_id` on every product table plus `get_owned` lookups.

## Future improvements

- Move default `DATABASE_URL` to PostgreSQL and run Alembic in CI.
- Add ESLint and mypy to CI; clean F401 imports.
- Delete unused `api/generation.py` / `api/instagram_account_routes.py` after OpenAPI review.
- Host generated JPEGs on a public HTTPS origin automatically.
- Multi-instance scheduler with a distributed lock.
- Optional semantic diversity backend when an embeddings model is configured.

## Acceptance flows

| Flow | Result |
| --- | --- |
| 1 Login → prompt → LLM → image → storage → approve → Instagram → verify → DB → dashboard | Pass (pytest, mocked OpenAI/Meta) |
| 2 Scheduler → daily content → auto-approve → Instagram → post count 1 on double tick | Pass |
| 3 Festival campaign, 2 required posts, completion tracking | Pass |
| 4 Timeout → verify → no duplicate publish | Pass |
| 5 User A cannot access User B data | Pass |
