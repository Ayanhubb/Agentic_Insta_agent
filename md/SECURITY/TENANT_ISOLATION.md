# Tenant isolation

Related: [MCP architecture](../MCP/MCP_ARCHITECTURE.md), [Authentication](../API/AUTHENTICATION.md), [Database](../DATABASE/DATABASE.md).

The tenant id is the authenticated `users.id`. Routes pass `user.id` into repositories. They do not trust a user id in the JSON body.

## Database

Repository methods used by the API take `user_id` and query `get_owned` or `list_for_user`. A miss is 404. Tables with `user_id` include business, brand, products, assets, posts, generated images, automation, festivals, trends, opportunities, sessions, and Canva connections.

`instagram_posts.user_id` is nullable for older rows. New platform writes set it. Daily uniqueness is `(user_id, instagram_account_id, scheduled_date)` for published `DAILY_RETAIL_POST` rows.

Festival campaigns are unique on `(user_id, festival_name, year)`.

## MCP

`trusted_tenant` builds a `TenantContext` from the server-side user id. `strip_model_tenant_arguments` drops `tenant_id`, `user_id`, and similar keys if a model adds them. Handlers call `RepositoryGateway` with `tenant.tenant_id` only.

Asset tools raise `ASSET_ACCESS_DENIED` when the id exists for a different user. Festival detail omits another tenant's `campaign_id`. Trend evidence requires the observation's `user_id` to match.

`get_content_rules` reads `automation_settings` for that tenant and still reports publishing as unavailable to the model.

## Files

`resolve_owned_media` and asset media routes check the owner before opening a path. Reference image bytes are loaded only after the same check (`services/image_reference.py`).

## Tests

`tests/test_auth.py`, `tests/test_database.py`, `tests/test_mcp.py`, `tests/test_generation_api.py`, `tests/test_brand_assets.py`.

## Known exception

`META_ACCESS_TOKEN` plus `INSTAGRAM_ACCOUNT_ID` can publish without a per-user row. That path is not isolated per tenant. See [Security](SECURITY.md).
