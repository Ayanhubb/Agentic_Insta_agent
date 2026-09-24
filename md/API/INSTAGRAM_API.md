# Instagram API

Related: [Instagram agent](../AGENTS/INSTAGRAM_AGENT.md), [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md), [Authentication](AUTHENTICATION.md).

## App routes

| Method | Path | Role |
| --- | --- | --- |
| POST | `/api/v1/instagram/publish` | Upload a JPEG or PNG and run the agent |
| GET | `/api/v1/instagram/status` | This user's connection, secrets removed |
| POST | `/api/v1/instagram/connect` | Save account id and access token |
| POST | `/api/v1/instagram/disconnect` | Remove the connection |
| POST | `/api/v1/generation/{image_id}/approve` | Publish a generated still |

## Graph publishing

Implemented in `tools/instagram_media.py`. Base URL is `META_GRAPH_API_BASE_URL` plus `META_API_VERSION` (default `https://graph.facebook.com/v26.0`). Hosts are limited to `graph.facebook.com` and `graph.instagram.com`.

```text
POST /{ig-user-id}/media            image_url → container id
GET  /{container-id}?fields=status_code
POST /{ig-user-id}/media_publish    creation_id → media id
GET  media                          verify_publication
```

Ready container statuses: `FINISHED`, `PUBLISHED`. Failed: `ERROR`, `EXPIRED`.

The agent will not call `media_publish` again when the previous publish result is unknown. Verification is a separate tool. HTTP 200 on publish is not treated as verified by itself (`tests/test_instagram_verifier.py`).

Graph error codes mapped in that module include auth (`102`, `190`, `467`), permission (`10`), rate limit (`4`, `17`, `32`, `613`), and image URL fetch failures (`9004`, `36000`, `36001`, `36003`). Unknown codes become `API_ERROR`.

## Reads

`services/instagram_reader.py` is GET-only. Metrics are listed in [Instagram intelligence](../INTELLIGENCE/INSTAGRAM_INTELLIGENCE.md).

## What is not implemented

- Reels, stories, carousels
- Comment or DM APIs
- OAuth code flow for Instagram. Connect takes a token the user already has
- Publishing from MCP, DeepSeek, OpenAI, or Canva

## Tests

`tests/test_instagram_media.py` mocks Graph HTTP. `tests/test_instagram_integration.py` is marked `integration`. `tests/test_real_instagram.py` is marked `real_instagram`. Neither runs unless you select that marker.
