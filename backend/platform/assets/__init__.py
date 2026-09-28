"""Optional, tenant-owned asset capabilities above the existing catalog.

The studio upload routes stay on ``AssetCatalog``. Agents bind roles. A role
that is not bound is not opened, including a company logo.
"""

from backend.platform.assets.binding import (
    AssetBinding,
    bind_assets,
    instagram_marketing_assets,
)
from backend.platform.assets.catalog import (
    INSTAGRAM_ASSET_ROLES,
    AssetCapability,
    AssetRole,
    ProviderTarget,
)
from backend.platform.assets.errors import AssetAccessError
from backend.platform.assets.prepare import PreparedAsset
from backend.platform.assets.service import PlatformAssetService
from backend.platform.assets.store import AssetStore

__all__ = [
    "INSTAGRAM_ASSET_ROLES",
    "AssetAccessError",
    "AssetBinding",
    "AssetCapability",
    "AssetRole",
    "AssetStore",
    "PlatformAssetService",
    "PreparedAsset",
    "ProviderTarget",
    "bind_assets",
    "instagram_marketing_assets",
]
