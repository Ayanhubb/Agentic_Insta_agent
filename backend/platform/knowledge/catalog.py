"""Named knowledge sources. A binding is a subset. Nothing here is global to every agent."""

from __future__ import annotations

from enum import Enum


class KnowledgeSource(str, Enum):
    BUSINESS = "business"
    BRAND = "brand"
    PRODUCT = "product"
    DOCUMENT = "document"
    FESTIVAL = "festival"
    ACCOUNT = "account"
    TREND = "trend"
    CONTENT_RULES = "content_rules"


# Live rows and catalogs. Loaded through MCP at run time. Not copied into a second store.
LIVE_SOURCES = frozenset(
    {
        KnowledgeSource.BUSINESS,
        KnowledgeSource.BRAND,
        KnowledgeSource.PRODUCT,
        KnowledgeSource.FESTIVAL,
        KnowledgeSource.ACCOUNT,
        KnowledgeSource.TREND,
        KnowledgeSource.CONTENT_RULES,
    }
)

# Versioned in the platform store: structured business snapshots and documents.
VERSIONED_SOURCES = frozenset({KnowledgeSource.BUSINESS, KnowledgeSource.DOCUMENT})

# The reference agent. ``document`` is a separate optional source.
INSTAGRAM_KNOWLEDGE_SOURCES = frozenset(
    {
        KnowledgeSource.BUSINESS,
        KnowledgeSource.BRAND,
        KnowledgeSource.PRODUCT,
        KnowledgeSource.FESTIVAL,
        KnowledgeSource.ACCOUNT,
        KnowledgeSource.TREND,
        KnowledgeSource.CONTENT_RULES,
    }
)

# MCP tool used when that source is granted. ``document`` has no tool.
SOURCE_TOOLS = {
    KnowledgeSource.BUSINESS: "get_business_profile",
    KnowledgeSource.BRAND: "get_brand_guidelines",
    KnowledgeSource.PRODUCT: "get_active_offers",
    KnowledgeSource.FESTIVAL: "get_upcoming_festivals",
    KnowledgeSource.ACCOUNT: "get_account_summary",
    KnowledgeSource.TREND: "get_current_trends",
    KnowledgeSource.CONTENT_RULES: "get_content_rules",
}

# Asset roles that already have an allowlisted MCP read. Others come from the asset store.
ASSET_TOOLS = {
    "company_logo": "get_company_logo",
    "product_image": "get_product_image",
}
