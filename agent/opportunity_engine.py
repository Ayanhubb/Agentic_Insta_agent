"""Turn a trend brief into content opportunities for the existing Content Agent.

The engine copies supplied products, offers, brand text, evidence, and stored
festival dates. It does not invent those facts and it does not publish.
Instagram publishing remains the Instagram Agent, and only for a single still image.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from festivals.intelligence import (
    canonical_name,
    known_festival,
    matches_place,
    resolve_place,
)
from models.content import ApprovalStatus, ContentMode, ContentType
from models.creative import ContentOrchestrationRequest
from models.opportunity import (
    INSTAGRAM_AGENT_FORMATS,
    BrandGuidelinesFact,
    BusinessProfileFacts,
    BusinessVertical,
    ContentOpportunity,
    FestivalContextFact,
    OfferFact,
    OpportunityKind,
    OpportunityRequest,
    PerformanceFact,
    ProductFact,
    RecommendedFormat,
    TrendBrief,
)
from scheduler.approval_policy import decide_approval

LOW_CONFIDENCE = 0.55

_FOOD_MARKERS = (
    "restaurant",
    "cafe",
    "café",
    "food",
    "beverage",
    "bakery",
    "kitchen",
    "dhaba",
    "f&b",
    "food_and_beverage",
    "sweet",
)
_SEASONAL = ("season", "summer", "winter", "monsoon", "spring")
_LAUNCH = ("launch", "new arrival", "just in")
_EDUCATIONAL = ("educat", "how to", "how-to", "tip", "care guide")
_BEHIND = ("behind the scenes", "behind-the-scenes", "kitchen", "prep")
_EXPERIENCE = ("experience", "dine", "dining", "ambiance", "ambience")
_OFFER_WORDS = ("offer", "discount", "promotion", "deal")
_FORMAT_NOTE = (
    "This is a concept only. The Instagram Agent publishes a single still image "
    "and cannot publish carousels, reels, or stories."
)
_STILL_NOTE = (
    "The Instagram Agent can publish this as one still image after approval. "
    "It does not publish carousels, reels, or stories."
)

_KIND_LABEL = {
    OpportunityKind.PRODUCT_LAUNCH: "Product launch",
    OpportunityKind.PRODUCT_SHOWCASE: "Product showcase",
    OpportunityKind.SEASONAL_CAMPAIGN: "Seasonal campaign",
    OpportunityKind.FESTIVAL_CAMPAIGN: "Festival campaign",
    OpportunityKind.OFFER_CAMPAIGN: "Offer campaign",
    OpportunityKind.BRAND_STORYTELLING: "Brand storytelling",
    OpportunityKind.EDUCATIONAL_CONTENT: "Educational content",
    OpportunityKind.DISH_SHOWCASE: "Dish showcase",
    OpportunityKind.MENU_PROMOTION: "Menu promotion",
    OpportunityKind.FESTIVAL_FOOD: "Festival food",
    OpportunityKind.SEASONAL_MENU: "Seasonal menu",
    OpportunityKind.OFFER: "Offer",
    OpportunityKind.RESTAURANT_EXPERIENCE: "Restaurant experience",
    OpportunityKind.BEHIND_THE_SCENES: "Behind the scenes",
    OpportunityKind.LOCAL_RELEVANCE: "Local relevance",
}
_KIND_PRIORITY = {kind: index for index, kind in enumerate(_KIND_LABEL)}
_CONTENT_TYPE = {
    OpportunityKind.PRODUCT_LAUNCH: ContentType.NEW_ARRIVAL,
    OpportunityKind.PRODUCT_SHOWCASE: ContentType.PRODUCT,
    OpportunityKind.SEASONAL_CAMPAIGN: ContentType.SEASONAL,
    OpportunityKind.FESTIVAL_CAMPAIGN: ContentType.FESTIVAL,
    OpportunityKind.OFFER_CAMPAIGN: ContentType.PROMOTION,
    OpportunityKind.BRAND_STORYTELLING: ContentType.BRAND,
    OpportunityKind.EDUCATIONAL_CONTENT: ContentType.EDUCATIONAL,
    OpportunityKind.DISH_SHOWCASE: ContentType.PRODUCT,
    OpportunityKind.MENU_PROMOTION: ContentType.PRODUCT,
    OpportunityKind.FESTIVAL_FOOD: ContentType.FESTIVAL,
    OpportunityKind.SEASONAL_MENU: ContentType.SEASONAL,
    OpportunityKind.OFFER: ContentType.PROMOTION,
    OpportunityKind.RESTAURANT_EXPERIENCE: ContentType.LIFESTYLE,
    OpportunityKind.BEHIND_THE_SCENES: ContentType.BRAND,
    OpportunityKind.LOCAL_RELEVANCE: ContentType.CUSTOMER_FOCUSED,
}


class OpportunityBridge(BaseModel):
    """Handoff into the Content Agent. ``published`` stays false."""

    model_config = ConfigDict(extra="ignore")

    opportunities: list[ContentOpportunity] = Field(default_factory=list)
    approval_status: ApprovalStatus = ApprovalStatus.PENDING_APPROVAL
    requires_approval: bool = True
    handoff_ready: bool = False
    published: Literal[False] = False
    content_request: ContentOrchestrationRequest | None = None
    content_mode: ContentMode | None = None
    reasons: list[str] = Field(default_factory=list)


def recommend_format(trend: TrendBrief) -> RecommendedFormat:
    blob = _blob(trend.title, trend.summary, " ".join(trend.themes))
    if re.search(r"\breels?\b|\bvideo\b", blob):
        return RecommendedFormat.REEL_CONCEPT
    if re.search(r"\bstories\b|\bstory\b", blob):
        return RecommendedFormat.STORY_CONCEPT
    if re.search(r"\bcarousel\b", blob):
        return RecommendedFormat.CAROUSEL
    return RecommendedFormat.SINGLE_IMAGE


class OpportunityEngine:
    def __init__(self, *, low_confidence: float = LOW_CONFIDENCE) -> None:
        self._low_confidence = low_confidence

    def evaluate(
        self,
        request: OpportunityRequest,
        automation: dict[str, Any] | None = None,
        *,
        qa_passed: bool = True,
        user_id: str = "tenant",
    ) -> OpportunityBridge:
        trend = request.trend
        if trend.expires_at <= request.now:
            return OpportunityBridge(reasons=["trend_expired"])

        vertical = request.vertical or _vertical(request.business)
        products = _matched_products(trend, request.products)
        offers = _matched_offers(trend, request.offers, request.products)
        festival = _sourced_festival(request.festival, trend, request.business)
        evidence = _evidence(trend, request.performance)
        confidence = _confidence(trend, evidence, self._low_confidence)
        chosen_format = recommend_format(trend)
        kinds = _kinds(
            vertical,
            has_product=bool(products),
            launch=_contains(trend, _LAUNCH),
            has_offer=bool(offers),
            has_festival=festival is not None,
            seasonal=_contains(trend, _SEASONAL) and festival is None,
            educational=_contains(trend, _EDUCATIONAL),
            behind=_contains(trend, _BEHIND),
            experience=_contains(trend, _EXPERIENCE) or (vertical == BusinessVertical.FOOD_AND_BEVERAGE and not products),
            local=bool(trend.region or request.business.location) and (_contains(trend, ("local",)) or bool(trend.region)),
            brand=bool(request.brand.style or request.brand.claims or request.business.description or not products),
            menu=_contains(trend, ("menu",)) or len(products) > 1,
        )
        product = products[0] if products else None
        offer = offers[0] if offers else None
        opportunities = [
            _build_opportunity(
                kind=kind,
                vertical=vertical,
                trend=trend,
                business=request.business,
                brand=request.brand,
                product=product,
                offer=offer,
                festival=festival,
                evidence=evidence,
                confidence=confidence,
                chosen_format=chosen_format,
            )
            for kind in kinds
        ]
        opportunities.sort(key=lambda item: _KIND_PRIORITY[item.kind])
        return _bridge(
            opportunities,
            request=request,
            automation=automation or {},
            qa_passed=qa_passed,
            user_id=user_id,
            low_confidence=self._low_confidence,
        )


def _bridge(
    opportunities: list[ContentOpportunity],
    *,
    request: OpportunityRequest,
    automation: dict[str, Any],
    qa_passed: bool,
    user_id: str,
    low_confidence: float,
) -> OpportunityBridge:
    if not opportunities:
        return OpportunityBridge(reasons=["no_grounded_opportunity"])
    selected = opportunities[0]
    mode = ContentMode.FESTIVAL if selected.festival_context else ContentMode.DAILY
    flags = dict(automation)
    if selected.confidence < low_confidence or not selected.instagram_can_publish:
        flags["auto_daily_publish"] = False
        flags["auto_festival_publish"] = False
    decision = decide_approval(
        mode=mode,
        automation=flags,
        plan=_approval_plan(selected),
        profile=_approval_profile(request),
        qa_passed=qa_passed,
        qa_malformed=False,
        ownership_ok=True,
        festival_valid=selected.festival_context is not None,
        provider_malformed=False,
    )
    auto = decision.publish and decision.status == "APPROVED"
    status = ApprovalStatus.AUTO_APPROVED if auto else ApprovalStatus.PENDING_APPROVAL
    content_request = ContentOrchestrationRequest(
        user_id=user_id,
        authenticated=True,
        mode=mode.value,
        user_prompt=selected.creative_direction,
        festival=(selected.festival_context or {}).get("name"),
        product_id=selected.product_id,
        offer_id=selected.offer_id,
    )
    return OpportunityBridge(
        opportunities=opportunities,
        approval_status=status,
        requires_approval=not auto,
        handoff_ready=auto,
        published=False,
        content_request=content_request,
        content_mode=mode,
        reasons=list(decision.reasons),
    )


def _approval_plan(opportunity: ContentOpportunity) -> SimpleNamespace:
    return SimpleNamespace(
        content_type=_CONTENT_TYPE[opportunity.kind],
        featured_product_or_service=opportunity.recommended_product,
        product_ids=[opportunity.recommended_product] if opportunity.recommended_product else [],
        offer_text=opportunity.recommended_offer,
        caption_hint=opportunity.creative_direction,
        caption=opportunity.creative_direction,
        theme=opportunity.title,
        image_prompt=opportunity.creative_direction,
        business_context=opportunity.title,
        reason=opportunity.why_now,
        qa_requirements={},
        logo_required=False,
        asset_ids=[],
    )


def _approval_profile(request: OpportunityRequest) -> dict[str, Any]:
    return {
        "business_name": request.business.business_name,
        "business_type": request.business.business_type,
        "description": request.business.description or "",
        "products": [item.name for item in request.products] + list(request.business.products),
        "services": [item.text for item in request.offers] + list(request.business.services),
        "brand_style": request.brand.style or "",
    }


def _build_opportunity(
    *,
    kind: OpportunityKind,
    vertical: BusinessVertical,
    trend: TrendBrief,
    business: BusinessProfileFacts,
    brand: BrandGuidelinesFact,
    product: ProductFact | None,
    offer: OfferFact | None,
    festival: dict[str, Any] | None,
    evidence: list[str],
    confidence: float,
    chosen_format: RecommendedFormat,
) -> ContentOpportunity:
    needs_product = kind in {
        OpportunityKind.PRODUCT_LAUNCH,
        OpportunityKind.PRODUCT_SHOWCASE,
        OpportunityKind.DISH_SHOWCASE,
        OpportunityKind.MENU_PROMOTION,
    }
    needs_offer = kind in {OpportunityKind.OFFER_CAMPAIGN, OpportunityKind.OFFER}
    needs_festival = kind in {OpportunityKind.FESTIVAL_CAMPAIGN, OpportunityKind.FESTIVAL_FOOD}
    chosen_product = product if needs_product else None
    chosen_offer = offer if needs_offer else None
    chosen_festival = festival if needs_festival else None
    publishable = chosen_format in INSTAGRAM_AGENT_FORMATS
    return ContentOpportunity(
        title=f"{business.business_name}: {_KIND_LABEL[kind]}",
        why_now=_why_now(trend),
        evidence=list(evidence),
        recommended_format=chosen_format,
        recommended_product=chosen_product.name if chosen_product else None,
        recommended_offer=chosen_offer.text if chosen_offer else None,
        festival_context=chosen_festival,
        creative_direction=_direction(
            kind=kind,
            business=business,
            brand=brand,
            trend=trend,
            product=chosen_product,
            offer=chosen_offer,
            festival=chosen_festival,
            publishable=publishable,
        ),
        audience_context=brand.audience,
        expiration=trend.expires_at,
        confidence=confidence,
        kind=kind,
        vertical=vertical,
        product_id=chosen_product.id if chosen_product else None,
        offer_id=chosen_offer.id if chosen_offer else None,
        instagram_can_publish=publishable,
    )


def _why_now(trend: TrendBrief) -> str:
    until = trend.expires_at.date().isoformat()
    sentence = f'The trend "{trend.title}" is active until {until}.'
    if trend.evidence:
        return f"{sentence} {trend.evidence[0].statement}"
    return sentence


def _direction(
    *,
    kind: OpportunityKind,
    business: BusinessProfileFacts,
    brand: BrandGuidelinesFact,
    trend: TrendBrief,
    product: ProductFact | None,
    offer: OfferFact | None,
    festival: dict[str, Any] | None,
    publishable: bool,
) -> str:
    lines = [
        f"Create a {recommend_format(trend).value.replace('_', ' ')} for {business.business_name}.",
        f"Opportunity: {_KIND_LABEL[kind]}.",
        trend.summary.strip(),
    ]
    if product is not None:
        lines.append(f"Feature the existing product {product.name}.")
        if product.description:
            lines.append(product.description)
    else:
        lines.append("Do not name a product that was not supplied.")
    if offer is not None:
        lines.append(f"Use only this existing offer: {offer.text}.")
    else:
        lines.append("Do not mention a price or an offer.")
    if festival is not None:
        lines.append(f"Festival: {festival['name']} on {festival['date']}.")
    else:
        lines.append("Do not add a festival date.")
    if brand.style:
        lines.append(f"Brand style: {brand.style}.")
    lines.extend(brand.claims)
    if business.location and kind == OpportunityKind.LOCAL_RELEVANCE:
        lines.append(f"Location: {business.location}.")
    lines.append(_STILL_NOTE if publishable else _FORMAT_NOTE)
    return " ".join(lines)


def _kinds(vertical: BusinessVertical, **flags: bool) -> list[OpportunityKind]:
    selected: list[OpportunityKind] = []
    if vertical == BusinessVertical.RETAIL:
        if flags["has_festival"]:
            selected.append(OpportunityKind.FESTIVAL_CAMPAIGN)
        if flags["seasonal"]:
            selected.append(OpportunityKind.SEASONAL_CAMPAIGN)
        if flags["has_product"] and flags["launch"]:
            selected.append(OpportunityKind.PRODUCT_LAUNCH)
        elif flags["has_product"]:
            selected.append(OpportunityKind.PRODUCT_SHOWCASE)
        if flags["has_offer"]:
            selected.append(OpportunityKind.OFFER_CAMPAIGN)
        if flags["educational"]:
            selected.append(OpportunityKind.EDUCATIONAL_CONTENT)
        if flags["brand"]:
            selected.append(OpportunityKind.BRAND_STORYTELLING)
    else:
        if flags["has_festival"]:
            selected.append(OpportunityKind.FESTIVAL_FOOD)
        if flags["seasonal"]:
            selected.append(OpportunityKind.SEASONAL_MENU)
        if flags["has_product"]:
            selected.append(OpportunityKind.DISH_SHOWCASE)
        if flags["has_product"] and flags["menu"]:
            selected.append(OpportunityKind.MENU_PROMOTION)
        if flags["has_offer"]:
            selected.append(OpportunityKind.OFFER)
        if flags["behind"]:
            selected.append(OpportunityKind.BEHIND_THE_SCENES)
        if flags["experience"]:
            selected.append(OpportunityKind.RESTAURANT_EXPERIENCE)
        if flags["local"]:
            selected.append(OpportunityKind.LOCAL_RELEVANCE)
    return selected


def _matched_products(trend: TrendBrief, products: list[ProductFact]) -> list[ProductFact]:
    blob = _blob(trend.title, trend.summary, " ".join(trend.themes), " ".join(trend.suggested_product_names))
    hinted = {name.casefold() for name in trend.suggested_product_names}
    matched: list[ProductFact] = []
    for product in products:
        if not product.is_active:
            continue
        name = product.name.casefold()
        if name in hinted or name in blob:
            matched.append(product)
    return matched


def _matched_offers(trend: TrendBrief, offers: list[OfferFact], products: list[ProductFact]) -> list[OfferFact]:
    catalog = list(offers)
    for product in products:
        if product.offer:
            catalog.append(OfferFact(id=f"offer:{product.id}", text=product.offer, product_id=product.id, price=product.price))
    by_id = {item.id: item for item in catalog}
    chosen = [by_id[item_id] for item_id in trend.suggested_offer_ids if item_id in by_id]
    if chosen:
        return chosen
    blob = _blob(trend.title, trend.summary, " ".join(trend.themes))
    mentioned = [item for item in catalog if item.text.casefold() in blob]
    if mentioned:
        return mentioned
    if any(word in blob for word in _OFFER_WORDS) and len(catalog) == 1:
        return catalog
    return []


def _sourced_festival(
    festival: FestivalContextFact | None,
    trend: TrendBrief,
    business: BusinessProfileFacts,
) -> dict[str, Any] | None:
    if festival is None or festival.date is None:
        return None
    canon = canonical_name(festival.name)
    if canon is None:
        return None
    if trend.festival_name:
        trend_name = canonical_name(trend.festival_name) or trend.festival_name
        if str(trend_name).casefold() != canon.casefold():
            return None
    elif canon.casefold() not in _blob(trend.title, trend.summary, " ".join(trend.themes)):
        return None
    stored = known_festival(canon, year=festival.date.year)
    if not stored or not stored.get("date"):
        return None
    if str(stored["date"]) != festival.date.isoformat():
        return None
    if not _region_ok(stored, business.location, trend.region):
        return None
    return {
        "name": stored["festival_name"],
        "date": stored["date"],
        "year": stored.get("year") or festival.date.year,
        "region": stored.get("region"),
        "campaign_id": festival.campaign_id,
    }


def _region_ok(stored: dict[str, Any], location: str | None, trend_region: str | None) -> bool:
    place_text = (trend_region or location or "").strip()
    if not place_text:
        return bool(stored.get("pan_india") or stored.get("region") == "National")
    place = resolve_place(place_text)
    if place is None:
        region = str(stored.get("region") or "")
        folded = place_text.casefold()
        return folded in region.casefold() or region.casefold() in folded
    return matches_place(stored, place, include_national=True)


def _evidence(trend: TrendBrief, performance: list[PerformanceFact]) -> list[str]:
    lines = [item.statement for item in trend.evidence]
    for row in performance:
        if row.metric_name and row.metric_value is not None:
            lines.append(f"{row.metric_name}: {row.metric_value}")
        elif row.theme:
            lines.append(f"Previous theme: {row.theme}")
    return lines


def _confidence(trend: TrendBrief, evidence: list[str], low_confidence: float) -> float:
    value = float(trend.confidence)
    if not evidence:
        value = min(value, max(0.0, low_confidence - 0.01))
    return round(min(1.0, max(0.0, value)), 4)


def _vertical(business: BusinessProfileFacts) -> BusinessVertical:
    blob = _blob(business.business_type, business.business_category, business.description)
    if any(marker in blob for marker in _FOOD_MARKERS):
        return BusinessVertical.FOOD_AND_BEVERAGE
    return BusinessVertical.RETAIL


def _contains(trend: TrendBrief, needles: tuple[str, ...]) -> bool:
    blob = _blob(trend.title, trend.summary, " ".join(trend.themes))
    return any(needle in blob for needle in needles)


def _blob(*parts: Any) -> str:
    return " ".join(str(part) for part in parts if part).casefold()
