# Media storage

Related: [Image generation](../AI/IMAGE_GENERATION.md), [Brand assets](../CONTENT/BRAND_ASSETS.md), [Security](../SECURITY/SECURITY.md).

## Directories

`Settings.ensure_directories` creates:

| Path | Use |
| --- | --- |
| `storage/` and `storage/generated`, `prepared`, `published`, `assets` | Media root (`media_root`, default `storage`) |
| `output/prepared`, `output/hosted` | Legacy prepare/host copies |
| `tmp/`, `input/`, `data/` | Temp, uploads, SQLite |

Generated files use `storage/generated/{user_id}/` via `LocalGeneratedImageStore` (`ai/image_generator.py`) and `services/media_paths.py`. Brand and product files use `services/asset_paths.py` and `business_assets.storage_key`. Production logos and product images are not uploaded yet. A missing file is not synthesized into `storage/assets` and labeled as the company logo. OpenAI receives bytes only after MCP and `asset_resolution` mark the file `AVAILABLE`.

## Who can read a file

| URL | Auth |
| --- | --- |
| `GET /api/v1/media/{filename}` | Public. Used as the URL Meta fetches. Filenames are generated, not user-chosen paths |
| `GET /api/v1/generation/{image_id}/media` | Session. `resolve_owned_media(user.id, ...)` |
| `GET /api/v1/media/generated/{image_id}` | Password ok. Owner row |
| `GET /api/v1/assets/{asset_id}/media` | Password ok. Owner row |
| `GET /api/v1/brand/assets/{asset_id}/media` | Password ok. Owner row |

Path segments containing `..` are rejected.

## Publish copy

The agent prepares a JPEG (target width `TARGET_IMAGE_WIDTH`, default 1080, quality `JPEG_QUALITY` 90) and exposes it on the public media route. `IMAGE_PUBLIC_BASE_URL` must be an HTTPS origin Meta can reach. The unset default `https://cdn.example.test/instagram-agent` is a placeholder and will not publish for real.

Upload limits: `MAX_IMAGE_SIZE_MB` default 8. Allowed upload types: JPEG and PNG (`ALLOWED_IMAGE_MIME_TYPES`). Reference edits also allow WebP (`services/image_reference.py`).

## Retry

`Settings.storage_retry_attempts` defaults to 3. `from_env` does not load that number from the environment. Storage failures are retryable in `RecoveryPolicy`. A failed write does not call Graph.
