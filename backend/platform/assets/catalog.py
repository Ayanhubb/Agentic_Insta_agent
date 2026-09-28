"""Asset capabilities. Every capability is optional. No agent is assumed to need a logo."""

from __future__ import annotations

from enum import Enum


class AssetCapability(str, Enum):
    BRAND = "brand"
    PRODUCT = "product"
    DOCUMENT = "document"
    REFERENCE_IMAGE = "reference_image"
    CAMPAIGN = "campaign"
    AGENT = "agent"


class AssetRole(str, Enum):
    COMPANY_LOGO = "company_logo"
    PRODUCT_IMAGE = "product_image"
    BRAND_GUIDE = "brand_guide"
    CAMPAIGN = "campaign"
    DOCUMENT = "document"
    REFERENCE_IMAGE = "reference_image"
    AGENT_ASSET = "agent_asset"


class ProviderTarget(str, Enum):
    IMAGE_EDIT = "image_edit"
    TEXT = "text"


# Roles the Instagram reference agent may bind. Presence of a file is still optional.
INSTAGRAM_ASSET_ROLES = frozenset(
    {
        AssetRole.COMPANY_LOGO,
        AssetRole.PRODUCT_IMAGE,
        AssetRole.BRAND_GUIDE,
        AssetRole.CAMPAIGN,
    }
)

ROLE_CAPABILITY = {
    AssetRole.COMPANY_LOGO: AssetCapability.BRAND,
    AssetRole.PRODUCT_IMAGE: AssetCapability.PRODUCT,
    AssetRole.BRAND_GUIDE: AssetCapability.DOCUMENT,
    AssetRole.CAMPAIGN: AssetCapability.CAMPAIGN,
    AssetRole.DOCUMENT: AssetCapability.DOCUMENT,
    AssetRole.REFERENCE_IMAGE: AssetCapability.REFERENCE_IMAGE,
    AssetRole.AGENT_ASSET: AssetCapability.AGENT,
}

# Platform role → directory scope used by the existing asset path helper.
ROLE_SCOPE = {
    AssetRole.COMPANY_LOGO: "brand",
    AssetRole.PRODUCT_IMAGE: "product",
    AssetRole.BRAND_GUIDE: "brand",
    AssetRole.DOCUMENT: "brand",
    AssetRole.CAMPAIGN: "campaign",
    AssetRole.REFERENCE_IMAGE: "campaign",
    AssetRole.AGENT_ASSET: "campaign",
}

# Existing ``business_assets.role`` values projected into platform roles.
STORED_ROLE_MAP = {
    "logo_png": AssetRole.COMPANY_LOGO,
    "logo_svg": AssetRole.COMPANY_LOGO,
    "product_image": AssetRole.PRODUCT_IMAGE,
    "guideline": AssetRole.BRAND_GUIDE,
    "campaign": AssetRole.CAMPAIGN,
    "other": AssetRole.AGENT_ASSET,
}

REFERENCE_ROLES = frozenset(
    {
        AssetRole.COMPANY_LOGO,
        AssetRole.PRODUCT_IMAGE,
        AssetRole.CAMPAIGN,
        AssetRole.REFERENCE_IMAGE,
    }
)
REFERENCE_MIME_TYPES = frozenset({"image/png", "image/jpeg"})


def is_reference_eligible(role: AssetRole, mime_type: str, stored_role: str) -> bool:
    """Raster files a pixel provider may receive. SVG, PDF, and text never qualify."""
    if stored_role == "logo_svg" or mime_type == "image/svg+xml":
        return False
    return role in REFERENCE_ROLES and mime_type in REFERENCE_MIME_TYPES
