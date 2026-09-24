# Instagram agent

Related: [Architecture](../ARCHITECTURE.md), [Instagram API](../API/INSTAGRAM_API.md), [Security](../SECURITY/SECURITY.md).

`InstagramAgent` in `agent/agent.py` is the only publisher. `build_registry` registers exactly these tools (`agent/planner.py` `ALLOWED_TOOL_SET`):

| Tool | Step |
| --- | --- |
| `validate_image` | Check type and size |
| `prepare_image` | Resize/encode for Instagram |
| `upload_image` | Place a file Meta can fetch |
| `create_instagram_media` | Graph media container |
| `publish_instagram_media` | Graph publish |
| `verify_publication` | Read the media back |

`ToolRegistry.register` refuses any name outside that set.

## States

`TaskStatus` values used by the agent: `pending`, `planning`, `validating`, `preparing`, `uploading`, `creating_media`, `publishing`, `verifying`, `completed`, `failed`.

The production planner is `DeterministicPlanner`. `LLMPlanner` raises that LLM planning is not enabled for V1. Status: **PARTIAL** (stub, not wired).

## Recovery

`RecoveryPolicy` (`agent/recovery.py`):

- Retries rate limits, storage, container creation, and verification inside configured attempt caps.
- If `publish_instagram_media` returns unknown certainty or times out, sets `skip_publish` and will not publish again until verification.
- Will not publish again when certainty is `SUCCEEDED` or a media id already exists.

`AGENT_TIMEOUT_SECONDS` defaults to 120. `Settings.storage_retry_attempts` and `Settings.verification_retry_attempts` default to 3 and 5. Those two attempt counts are class defaults in `config.py`. `from_env` does not read environment variables for them.

## Entry points

| Entry | Module |
| --- | --- |
| `POST /api/v1/instagram/publish` | Multipart JPEG/PNG upload. Requires `require_password_ok`. Query `wait=false` returns 202 |
| `POST /api/v1/generation/{image_id}/approve` | Approves a stored image and calls `PublicationService` |
| Scheduler pipeline | `CampaignPipeline` calls `PublicationService.publish_generated_image` only when `ApprovalDecision.publish` is true |
| `enqueue_instagram_publication` | `agent/instagram_tasks.py` |

Graph hosts are limited to `graph.facebook.com` and `graph.instagram.com` unless `strict_graph_hosts` is relaxed for localhost tests (`config.py`).

## Whose Instagram account

`PublicationGateway.resolve_credentials` loads the authenticated user's connected account and decrypts that row's token. User A cannot publish with user B's account id or token. The scheduler calls `publish_generated_image` without the environment fallback, and `DailyScheduler` / `FestivalScheduler` stop with `INSTAGRAM_NOT_CONNECTED` when `publication_block` says the owned account cannot publish.

`META_ACCESS_TOKEN` and `INSTAGRAM_ACCOUNT_ID` are not used for production or for automatic publishing. They are a development-only compatibility path: `APP_ENV` in `development` / `dev` / `local` and `INSTAGRAM_LEGACY_ENV_FALLBACK=true`. The default `APP_ENV` is `production`, so the path stays off when the variable is unset. The agent still never returns the token.

## What it does not publish

Reels, stories, and carousels. Captions are still images plus optional caption text (max 2200 characters on the upload route).
