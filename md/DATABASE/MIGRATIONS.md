# Migrations

Related: [Schema](DATABASE_SCHEMA.md).

Alembic versions live in `db/migrations/versions/`. Startup also calls `init_db`, which creates tables from `Base.metadata` for a fresh SQLite file. Apply Alembic when moving an existing database forward. These revisions do not store DeepSeek or OpenAI keys. Those keys are **IMPLEMENTED — NOT CONFIGURED** in the environment. Brand and product tables from `002` stay empty of production logos and product images until a user uploads them.

| Revision | File | Change |
| --- | --- | --- |
| `001_initial_schema` | `001_initial_schema.py` | `Base.metadata.create_all` for the initial schema |
| `002_brand_assets` | `002_brand_assets.py` | `business_assets`, `brand_profiles`, `brand_guidelines`, `products`, `product_assets` |
| `003_canva_connections` | `003_canva_connections.py` | `canva_connections`, `canva_oauth_states` |
| `004_generated_image_qa` | `004_generated_image_qa.py` | `generated_images.qa_status`, `generated_images.qa_json` |
| `005_trend_research` | `005_trend_research.py` | `trend_sources`, `trend_observations`, `trend_reports` |
| `006_trend_intelligence` | `006_trend_intelligence.py` | `trend_evidence`, snapshots, `content_opportunities`, extra observation and report columns, indexes |

`scripts/init_db.py` is a manual initializer. It is not a seventh migration.

Downgrade behavior is defined per revision file. Do not assume a downgrade drops data you still need; read that file before running it.
