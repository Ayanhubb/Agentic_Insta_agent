# Database schema

Source: `db/models.py`. Related: [Migrations](MIGRATIONS.md), [Database](DATABASE.md).

`DEEPSEEK_API_KEY` and `OPENAI_API_KEY` are not columns. Both are **IMPLEMENTED — NOT CONFIGURED**. `META_ACCESS_TOKEN` is not a column. Publishing uses `instagram_accounts.access_token_encrypted` for that user. Logo and product bytes, once uploaded, are `business_assets` rows. Production files are not uploaded yet.

Types below are shortened: `S` string, `T` text, `DT` datetime, `D` date, `J` JSON, `B` boolean, `I` integer, `N` numeric.

## Identity

| Table | Columns |
| --- | --- |
| `users` | `id`, `email`, `password_hash`, `is_active`, `is_admin`, `must_change_password`, `created_at`, `updated_at` |
| `sessions` | `id`, `user_id`, `token_hash`, `expires_at`, `revoked_at`, `created_at` |
| `instagram_accounts` | `id`, `user_id`, `instagram_account_id`, `access_token_encrypted`, `token_expires_at`, `status`, `connected_at`, `updated_at` |

## Business, brand, products

| Table | Columns |
| --- | --- |
| `business_profiles` | `id`, `user_id`, `business_name`, `business_type`, `business_category`, `description`, `target_audience`, `location`, `brand_style`, `preferred_language`, `products` J, `services` J, timestamps |
| `business_assets` | `id`, `user_id`, `scope`, `role`, `original_filename`, `mime_type`, `size_bytes`, `width`, `height`, `storage_key`, `created_at` |
| `brand_profiles` | `id`, `user_id`, `company_name`, `website`, `instagram_handle`, `brand_colors` J, `fonts` J, `logo_png_asset_id`, `logo_svg_asset_id`, timestamps |
| `brand_guidelines` | `id`, `user_id`, `title`, `body`, `asset_id`, timestamps |
| `products` | `id`, `user_id`, `name`, `description`, `category`, `price`, `sku`, `is_active`, `offer`, timestamps |
| `product_assets` | `id`, `user_id`, `product_id`, `asset_id`, `role`, `created_at` |

## Trends and intelligence

| Table | Columns |
| --- | --- |
| `trend_sources` | `id`, `user_id`, `business_id`, `name`, `source_type`, `url`, `industry`, `region`, `enabled`, timestamps |
| `trend_observations` | `id`, `user_id`, `source_id`, `industry`, `region`, `festival`, `content_type`, `title`, `summary`, `description`, `trend_type`, `keywords` J, `evidence`, `source`, `external_id`, `observed_at`, `published_at`, `valid_until`, `confidence`, `status`, timestamps |
| `trend_evidence` | `id`, `user_id`, `observation_id`, `source_type`, `source_record_id`, `excerpt`, `observed_at`, `valid_until`, `created_at` |
| `trend_reports` | `id`, `user_id`, `business_id`, `title`, `summary`, `industry`, `region`, `observation_count`, `observed_at`, `valid_until`, `status`, `payload` J, `created_at` |
| `content_opportunities` | `id`, `user_id`, `business_id`, `trend_id`, `title`, `why_now`, `creative_direction`, `recommended_format`, `product_id`, `festival_id`, `confidence`, `expires_at`, `status`, timestamps |
| `account_snapshots` | `id`, `user_id`, `business_id`, `instagram_account_id`, `observed_at`, `followers_count`, `media_count`, `metrics` J, `created_at` |
| `media_snapshots` | `id`, `user_id`, `business_id`, `instagram_account_id`, `instagram_post_id`, `instagram_media_id`, `observed_at`, `like_count`, `comments_count`, `reach`, `saved`, `shares`, `metrics` J, `created_at` |
| `insight_snapshots` | `id`, `user_id`, `business_id`, `observed_at`, `insight_type`, `summary`, `payload` J, `created_at` |
| `instagram_intelligence_records` | `id`, `user_id`, `kind`, `external_id`, `payload` J, `captured_at` |

`media_snapshots.shares` is a column. The Graph reader does not currently request a shares metric. See [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md).

## Content and publishing

| Table | Columns |
| --- | --- |
| `generated_images` | `id`, `user_id`, `original_prompt`, `enhanced_prompt`, `model`, `provider`, `filename`, `storage_path`, `mime_type`, `width`, `height`, `generation_status`, `approval_status`, `publication_status`, `source`, `content_type`, `theme`, `qa_status`, `qa_json`, `created_at`, `approved_at` |
| `instagram_posts` | `id`, `user_id`, `instagram_account_id`, `generated_image_id`, `instagram_media_id`, `permalink`, `status`, `post_type`, `published_at`, `error`, `created_at`, `scheduled_date`, `agent_task_id` |
| `agent_tasks` | `id`, `user_id`, `task_type`, `trigger`, `status`, `current_step`, `started_at`, `completed_at`, `error`, `created_at`, `state_json` |
| `agent_events` | `id`, `task_id`, `from_state`, `to_state`, `tool`, `result`, `observation` J, `timestamp` |
| `daily_post_slots` | `id`, `user_id`, `instagram_account_id`, `local_date`, `status`, `post_id`, `created_at` |

## Automation and festivals

| Table | Columns |
| --- | --- |
| `automation_settings` | `id`, `user_id` unique, `daily_enabled`, `daily_posts_per_day`, `daily_post_time`, `festival_enabled`, `festival_posts_per_festival`, `auto_daily_publish`, `auto_festival_publish`, `timezone`, `pre_festival_days`, `allow_same_day_festival_posts`, timestamps |
| `scheduled_jobs` | `id`, `user_id`, `instagram_account_id`, `job_type`, `schedule`, `enabled`, `next_run_at`, `last_run_at`, `status`, timestamps |
| `festival_campaigns` | `id`, `user_id`, `festival_name`, `festival_date`, `year`, `required_posts`, `generated_posts`, `published_posts`, `enabled`, timestamps |
| `festival_posts` | `id`, `campaign_id`, `generated_image_id`, `post_id`, `sequence_number`, `status`, `scheduled_for`, `published_at` |

There is no `festivals` table. The catalog is code plus `festivals/data/lunar_dates.json`.

## Canva

| Table | Columns |
| --- | --- |
| `canva_connections` | `id`, `tenant_id`, `user_id`, `access_token_encrypted`, `refresh_token_encrypted`, `token_expires_at`, `status`, `connected_at`, `updated_at` |
| `canva_oauth_states` | `state` primary key, `tenant_id`, `user_id`, `code_verifier_encrypted`, `expires_at`, `created_at` |
