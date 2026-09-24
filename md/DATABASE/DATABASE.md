# Database

Related: [Schema](DATABASE_SCHEMA.md), [Migrations](MIGRATIONS.md), [Secrets](../SECURITY/SECRETS.md).

## Engine

`DATABASE_URL` defaults to SQLite at `data/agentic.db` when empty (`Settings.resolved_database_url`). PostgreSQL is supported as a URL (`postgresql+psycopg://...` is the comment in `.env.example`). `init_db` runs at application startup.

Sessions are SQLAlchemy sessions from `db/session.py`. Repositories in `db/repositories.py`, `db/asset_repositories.py`, and `db/trend_repositories.py` take a session and filter by `user_id`.

## Models

All tables are declared on `Base` in `db/models.py`. Names and columns are listed in [Schema](DATABASE_SCHEMA.md). There is no separate ORM for a table that migrations create outside that file: Alembic revisions alter or create the same models.

## What is stored encrypted

| Column | Algorithm |
| --- | --- |
| `instagram_accounts.access_token_encrypted` | Fernet (`db/crypto.py` `TokenEncryptor`) |
| `canva_connections.access_token_encrypted` and `refresh_token_encrypted` | Fernet |
| `canva_oauth_states.code_verifier_encrypted` | Fernet |
| `users.password_hash` | bcrypt (`auth/passwords.py`) |
| `sessions.token_hash` | Hash of the JWT id, not the raw token |

API keys for OpenAI and DeepSeek are not columns. Both are **IMPLEMENTED — NOT CONFIGURED** in the environment. `META_ACCESS_TOKEN` is not a user row and is not used for production publish. Per-user Instagram tokens live only in `instagram_accounts.access_token_encrypted`. Logo and product files, once uploaded, are `business_assets` rows. None are loaded as production assets yet.

## Tests

`tests/test_database.py` checks schema, uniqueness, and cross-user reads.
