"""Approval policy for scheduled content.

Human mode always stops at PENDING_APPROVAL.
Automatic mode publishes only when every gate passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from models.content import ContentMode, ContentType

_PRODUCT_TYPES = {ContentType.PRODUCT, ContentType.PROMOTION, ContentType.NEW_ARRIVAL}
_OFFER_RE = re.compile(
    r"(₹\s*\d[\d,]*|\brs\.?\s*\d[\d,]*|\b\d+\s*%\s*off\b|\bflat\s+\d+\b|\bbuy\s+one\s+get\s+one\b)",
    re.IGNORECASE,
)


@dataclass
class ApprovalDecision:
    status: str
    publish: bool
    reasons: list[str] = field(default_factory=list)
    retryable: bool = False

    @property
    def reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None


def _flag(automation: Any, name: str) -> bool:
    if automation is None:
        return False
    if isinstance(automation, dict):
        return bool(automation.get(name))
    return bool(getattr(automation, name, False))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;\n]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def catalog_names(profile: Any) -> list[str]:
    names: list[str] = []
    for key in ("products", "services"):
        names.extend(_as_list(_attr(profile, key)))
    return names


def _profile_blob(profile: Any) -> str:
    parts: list[str] = []
    for key in ("business_name", "business_type", "description", "products", "services", "brand_style"):
        value = _attr(profile, key)
        if isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def _matches_catalog(value: str, catalog: list[str]) -> bool:
    needle = value.strip().lower()
    if not needle:
        return False
    for name in catalog:
        hay = name.lower()
        if needle == hay or needle in hay or hay in needle:
            return True
    return False


def _qa_requirements(plan: Any) -> dict[str, Any]:
    raw = _attr(plan, "qa_requirements") or {}
    return raw if isinstance(raw, dict) else {}


def _requires_product(plan: Any) -> bool:
    requirements = _qa_requirements(plan)
    if requirements.get("product_reference") or requirements.get("product_required"):
        return True
    content_type = _attr(plan, "content_type")
    if isinstance(content_type, ContentType):
        return content_type in _PRODUCT_TYPES
    return str(content_type or "").upper() in {item.value for item in _PRODUCT_TYPES}


def _product_reference_missing(plan: Any, profile: Any) -> bool:
    product_ids = _as_list(_attr(plan, "product_ids"))
    featured = str(_attr(plan, "featured_product_or_service") or "").strip()
    catalog = catalog_names(profile)
    if product_ids:
        if not catalog or not any(_matches_catalog(item, catalog) for item in product_ids):
            return True
    if featured:
        if catalog and not _matches_catalog(featured, catalog):
            return True
        return False
    return _requires_product(plan)


def _logo_missing(plan: Any) -> bool:
    requirements = _qa_requirements(plan)
    required = bool(_attr(plan, "logo_required")) or bool(
        requirements.get("logo_required") or requirements.get("required_logo")
    )
    if not required:
        return False
    if str(_attr(plan, "logo_asset_id") or "").strip():
        return False
    assets = _as_list(_attr(plan, "asset_ids"))
    return not any("logo" in asset.lower() for asset in assets)


def _offer_unverified(plan: Any, profile: Any) -> bool:
    blob = _profile_blob(profile)
    offer_text = str(_attr(plan, "offer_text") or "").strip()
    if offer_text and offer_text.lower() not in blob:
        return True
    copy = " ".join(
        str(_attr(plan, key) or "")
        for key in ("caption_hint", "caption", "theme", "image_prompt", "business_context", "reason")
    )
    for match in _OFFER_RE.finditer(copy):
        claim = re.sub(r"\s+", " ", match.group(0)).strip().lower()
        if claim and claim not in blob:
            return True
    return False


def _automatic(mode: ContentMode, automation: Any) -> bool:
    if mode == ContentMode.DAILY:
        return _flag(automation, "auto_daily_publish")
    if mode == ContentMode.FESTIVAL:
        return _flag(automation, "auto_festival_publish")
    return False


def decide_approval(
    *,
    mode: ContentMode,
    automation: Any,
    plan: Any,
    profile: Any,
    qa_passed: bool,
    qa_malformed: bool,
    ownership_ok: bool,
    festival_valid: bool,
    provider_malformed: bool,
) -> ApprovalDecision:
    """Return PENDING_APPROVAL unless automatic mode clears every gate."""
    reasons: list[str] = []
    if not qa_passed:
        reasons.append("image_qa_failed")
    if qa_malformed or provider_malformed:
        reasons.append("provider_result_malformed")
    if _product_reference_missing(plan, profile):
        reasons.append("product_reference_missing")
    if _logo_missing(plan):
        reasons.append("required_logo_missing")
    if _offer_unverified(plan, profile):
        reasons.append("offer_cannot_be_verified")
    if mode == ContentMode.FESTIVAL and not festival_valid:
        reasons.append("festival_context_invalid")
    if not ownership_ok:
        reasons.append("tenant_ownership_failed")

    if not _automatic(mode, automation):
        human_reasons = ["human_approval_required", *reasons]
        return ApprovalDecision(
            status="PENDING_APPROVAL",
            publish=False,
            reasons=human_reasons,
            retryable=False,
        )
    if reasons:
        return ApprovalDecision(status="PENDING_APPROVAL", publish=False, reasons=reasons, retryable=True)
    return ApprovalDecision(status="APPROVED", publish=True, reasons=[], retryable=False)
