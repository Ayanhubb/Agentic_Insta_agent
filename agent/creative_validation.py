"""Reject creative plans that add facts the MCP context does not contain."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from agent.planner import ALLOWED_TOOL_SET
from models.creative import (
    CampaignType,
    CanvaAction,
    CreativeContext,
    CreativePlan,
)
from models.errors import AppError, ErrorCode

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_PRICE = re.compile(
    r"(?:₹|rs\.?|inr|usd|\$|€|£)\s*\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:₹|rs\b|rupees|inr|usd|\$)",
    re.IGNORECASE,
)
_OFFER = re.compile(
    r"\b(?:discount|%\s*off|percent off|flat\s+\d+|buy\s+one\s+get|bogo|free\s+(?:gift|shipping)|limited[- ]time offer|special offer)\b",
    re.IGNORECASE,
)
_SPEC = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:karats?|carats?|kts?|ml|kg|gms?|grams?|cm|mm|oz)\b",
    re.IGNORECASE,
)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTH_DAY = re.compile(
    r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)

_FORBIDDEN_KEYS = frozenset(
    {
        "command",
        "code",
        "script",
        "python",
        "shell",
        "url",
        "http",
        "https",
        "token",
        "access_token",
        "api_key",
        "tool",
        "tools",
        "eval",
        "exec",
        "publish",
    }
)


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _plan_text(plan: CreativePlan) -> str:
    festival = plan.festival or ""
    return " ".join(
        [
            plan.caption,
            plan.creative_direction,
            plan.image_prompt,
            festival,
            plan.business_type or "",
            plan.audience or "",
            " ".join(plan.hashtags),
            " ".join(plan.qa_requirements),
        ]
    )


def _commercial_blob(context: CreativeContext) -> str:
    parts: list[str] = []
    for product in context.products:
        if product.price:
            parts.append(product.price)
        if product.offer:
            parts.append(product.offer)
    for offer in context.offers:
        parts.append(offer.text)
        if offer.price:
            parts.append(offer.price)
    return " ".join(parts)


def _detail_blob(context: CreativeContext) -> str:
    parts = [context.business.name, context.business.description or ""]
    for product in context.products:
        parts.append(product.name)
        parts.append(product.description or "")
    return " ".join(parts).casefold()


def _parse_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_dates(text: str) -> list[date | None]:
    found: list[date | None] = []
    for match in _ISO.finditer(text):
        found.append(_parse_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in _NUMERIC.finditer(text):
        day_first = _parse_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        month_first = _parse_date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        found.append(day_first or month_first)
    for match in _DAY_MONTH.finditer(text):
        month = _MONTHS.get(match.group(2).casefold())
        found.append(_parse_date(int(match.group(3)), month, int(match.group(1))) if month else None)
    for match in _MONTH_DAY.finditer(text):
        month = _MONTHS.get(match.group(1).casefold())
        found.append(_parse_date(int(match.group(3)), month, int(match.group(2))) if month else None)
    return found


def reject_untrusted_plan(raw: Any) -> None:
    """Refuse plans that try to publish, call tools, or carry credentials."""
    if isinstance(raw, CreativePlan):
        payload: Any = raw.model_dump()
    else:
        payload = raw
    if isinstance(payload, dict):
        keys = {str(key).lower() for key in payload}
        if keys & _FORBIDDEN_KEYS:
            raise AppError(ErrorCode.CONTENT_POLICY_VIOLATION, "The creative plan included disallowed fields.")
        blob = json.dumps(payload).lower()
    else:
        blob = str(payload).lower()
    if any(name in blob for name in ALLOWED_TOOL_SET):
        raise AppError(ErrorCode.CONTENT_POLICY_VIOLATION, "The creative plan attempted to select a publish tool.")
    if any(token in blob for token in ("os.system", "subprocess", "shell=true", "eval(", "exec(")):
        raise AppError(ErrorCode.CONTENT_POLICY_VIOLATION, "The creative plan attempted to execute code.")


def validate_creative_plan(plan: CreativePlan, context: CreativeContext) -> None:
    """The plan may only use products, offers, prices, assets, and festival dates that were sourced."""
    if plan.campaign_type != context.campaign_type:
        raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The campaign type does not match the sourced campaign.")

    expected_festival = context.festival.name if context.festival else None
    if (plan.festival or None) != expected_festival:
        if expected_festival is None or (plan.festival or "").casefold() != expected_festival.casefold():
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The festival does not match the sourced festival.")

    if context.business.business_type:
        if (plan.business_type or "").casefold() != context.business.business_type.casefold():
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The business type does not match the business profile.")
    elif plan.business_type:
        raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The plan invented a business type.")

    if context.brand.audience:
        if (plan.audience or "").casefold() != context.brand.audience.casefold():
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The audience does not match the brand profile.")
    elif plan.audience:
        raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The plan invented an audience.")

    known_products = {product.id: product for product in context.products}
    if not set(plan.product_ids) <= set(known_products):
        raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The plan used a product that was not supplied.")
    selected = set(plan.product_ids)
    text = _plan_text(plan)
    lowered = text.casefold()
    for product in context.products:
        if len(product.name) >= 3 and product.name.casefold() in lowered and product.id not in selected:
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The plan mentions a product it did not select.")

    known_assets = set(context.brand.asset_ids) | set(context.canva.asset_ids)
    for product in context.products:
        if product.asset_id:
            known_assets.add(product.asset_id)
    if not set(plan.asset_ids) <= known_assets:
        raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The plan used an asset that was not supplied.")

    if not context.canva.queried and plan.canva_action != CanvaAction.NONE:
        raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "Canva was not requested for this campaign.")
    if plan.canva_action != CanvaAction.NONE:
        if not plan.asset_ids or not set(plan.asset_ids) <= set(context.canva.asset_ids):
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The Canva action is not backed by a sourced asset.")

    blob = _norm(_commercial_blob(context))
    for pattern in (_PRICE, _OFFER):
        for match in pattern.finditer(text):
            if _norm(match.group(0)) not in blob:
                raise AppError(
                    ErrorCode.CONTENT_INVALID_PLAN,
                    "The plan includes a price or offer that was not supplied.",
                )

    details = _detail_blob(context)
    for match in _SPEC.finditer(text):
        if match.group(0).casefold() not in details:
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The plan includes a product detail that was not supplied.")

    allowed_date = context.festival.date if context.festival else None
    for found in _extract_dates(text):
        if found is None or found != allowed_date:
            raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "The plan includes a festival date that was not supplied.")

    if context.campaign_type == CampaignType.FESTIVAL and context.festival is None:
        raise AppError(ErrorCode.CONTENT_INVALID_PLAN, "Festival content requires a sourced festival.")
