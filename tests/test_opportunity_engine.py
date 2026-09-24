"""Content opportunity engine: grounded retail and food briefs, no publishing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.opportunity_engine import OpportunityEngine
from festivals.intelligence import known_festival
from models.content import ApprovalStatus, ContentMode
from models.opportunity import (
    BrandGuidelinesFact,
    BusinessProfileFacts,
    BusinessVertical,
    EvidenceItem,
    FestivalContextFact,
    OfferFact,
    OpportunityKind,
    OpportunityRequest,
    PerformanceFact,
    ProductFact,
    RecommendedFormat,
    TrendBrief,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
ENGINE = OpportunityEngine()
FAKE_PRICE = "₹9,999"
FAKE_PRODUCT = "Platinum tiara"


def _trend(**overrides) -> TrendBrief:
    payload = {
        "id": "trend-1",
        "title": "Giftable jewellery interest",
        "summary": "Shoppers are comparing giftable jewellery.",
        "evidence": [EvidenceItem(statement="The brief reports higher interest in giftable jewellery.")],
        "confidence": 0.84,
        "expires_at": datetime(2026, 10, 20, tzinfo=timezone.utc),
        "region": "Kolkata",
        "themes": [],
        "suggested_product_names": ["Gold ring"],
    }
    payload.update(overrides)
    return TrendBrief(**payload)


def _business(**overrides) -> BusinessProfileFacts:
    payload = {
        "business_name": "Ayan Jewels",
        "business_type": "Jewellery store",
        "business_category": "Retail",
        "description": "Family jeweller in Kolkata.",
        "location": "Kolkata",
    }
    payload.update(overrides)
    return BusinessProfileFacts(**payload)


def _product(**overrides) -> ProductFact:
    payload = {
        "id": "prod-ring",
        "name": "Gold ring",
        "description": "22k everyday gold ring",
        "price": FAKE_PRICE,
        "is_active": True,
    }
    payload.update(overrides)
    return ProductFact(**payload)


def _request(**overrides) -> OpportunityRequest:
    payload = {
        "trend": _trend(),
        "business": _business(),
        "products": [_product()],
        "offers": [],
        "brand": BrandGuidelinesFact(style="warm gold", audience="local buyers"),
        "now": NOW,
    }
    payload.update(overrides)
    return OpportunityRequest(**payload)


def _kinds(result) -> set[OpportunityKind]:
    return {item.kind for item in result.opportunities}


def test_retail_opportunities() -> None:
    launch = ENGINE.evaluate(_request(trend=_trend(themes=["new arrival"])))
    assert OpportunityKind.PRODUCT_LAUNCH in _kinds(launch)
    showcase = ENGINE.evaluate(_request(trend=_trend(themes=[])))
    assert OpportunityKind.PRODUCT_SHOWCASE in _kinds(showcase)
    seasonal = ENGINE.evaluate(_request(trend=_trend(themes=["monsoon"], suggested_product_names=[])))
    assert OpportunityKind.SEASONAL_CAMPAIGN in _kinds(seasonal)
    offer = ENGINE.evaluate(
        _request(
            trend=_trend(themes=["offer"], suggested_product_names=[]),
            offers=[OfferFact(id="offer-1", text="Complimentary polishing")],
        )
    )
    assert OpportunityKind.OFFER_CAMPAIGN in _kinds(offer)
    assert any(item.recommended_offer == "Complimentary polishing" for item in offer.opportunities)
    educational = ENGINE.evaluate(_request(trend=_trend(themes=["how to care"], suggested_product_names=[])))
    assert OpportunityKind.EDUCATIONAL_CONTENT in _kinds(educational)
    brand = ENGINE.evaluate(_request(trend=_trend(themes=["brand"], suggested_product_names=[]), products=[]))
    assert OpportunityKind.BRAND_STORYTELLING in _kinds(brand)

    selected = showcase.opportunities[0]
    public = selected.public()
    assert public["recommended_format"] == RecommendedFormat.SINGLE_IMAGE.value
    assert selected.instagram_can_publish is True
    assert selected.vertical == BusinessVertical.RETAIL
    assert FAKE_PRICE not in str(public)
    assert "saves: 12.0" not in str(public)

    measured = ENGINE.evaluate(
        _request(
            performance=[PerformanceFact(metric_name="saves", metric_value=12)],
        )
    )
    assert "saves: 12.0" in measured.opportunities[0].evidence
    assert "saves: 99" not in measured.opportunities[0].evidence

    for theme, expected in (
        ("reel", RecommendedFormat.REEL_CONCEPT),
        ("carousel", RecommendedFormat.CAROUSEL),
        ("story concept", RecommendedFormat.STORY_CONCEPT),
    ):
        framed = ENGINE.evaluate(_request(trend=_trend(themes=[theme])))
        assert framed.opportunities[0].recommended_format == expected
        assert framed.opportunities[0].instagram_can_publish is False
        assert "cannot publish" in framed.opportunities[0].creative_direction
        assert framed.published is False
        assert framed.handoff_ready is False


def test_food_and_beverage_opportunities() -> None:
    result = ENGINE.evaluate(
        _request(
            trend=_trend(
                title="Kosha mangsho weekend",
                summary="Diners are asking for kosha mangsho.",
                themes=["menu", "behind the scenes", "dining experience"],
                region="Kolkata",
                suggested_product_names=["Kosha Mangsho"],
                festival_name=None,
            ),
            business=_business(
                business_name="Bhojohori",
                business_type="Restaurant",
                business_category="Food",
                description="Bengali kitchen.",
                location="Kolkata",
            ),
            products=[
                _product(id="dish-1", name="Kosha Mangsho", description="Slow-cooked mutton.", price=None)
            ],
            brand=BrandGuidelinesFact(style="warm brass", audience="families in Kolkata"),
        )
    )
    kinds = _kinds(result)
    assert OpportunityKind.DISH_SHOWCASE in kinds
    assert OpportunityKind.MENU_PROMOTION in kinds
    assert OpportunityKind.BEHIND_THE_SCENES in kinds
    assert OpportunityKind.RESTAURANT_EXPERIENCE in kinds
    assert OpportunityKind.LOCAL_RELEVANCE in kinds
    assert result.opportunities[0].vertical == BusinessVertical.FOOD_AND_BEVERAGE
    assert all(item.recommended_product in {None, "Kosha Mangsho"} for item in result.opportunities)
    seasonal = ENGINE.evaluate(
        _request(
            trend=_trend(
                title="Monsoon menu",
                summary="Rainy-day dining.",
                themes=["monsoon"],
                suggested_product_names=[],
                region=None,
            ),
            business=_business(business_name="Bhojohori", business_type="Restaurant", location="Kolkata"),
            products=[_product(id="dish-1", name="Kosha Mangsho")],
        )
    )
    assert OpportunityKind.SEASONAL_MENU in _kinds(seasonal)


def test_festival_opportunity_uses_stored_date() -> None:
    stored = known_festival("Diwali", year=2026)
    assert stored and stored["date"]
    result = ENGINE.evaluate(
        _request(
            trend=_trend(title="Diwali gifting", summary="Diwali shopping has started.", festival_name="Diwali"),
            festival=FestivalContextFact(name="Diwali", date=stored["date"], campaign_id="camp-1"),
        )
    )
    festival_rows = [item for item in result.opportunities if item.kind == OpportunityKind.FESTIVAL_CAMPAIGN]
    assert festival_rows
    assert festival_rows[0].festival_context["name"] == "Diwali"
    assert festival_rows[0].festival_context["date"] == stored["date"]
    assert festival_rows[0].festival_context["campaign_id"] == "camp-1"
    assert "2020-01-01" not in festival_rows[0].creative_direction

    rejected = ENGINE.evaluate(
        _request(
            trend=_trend(title="Diwali gifting", summary="Diwali shopping has started.", festival_name="Diwali"),
            festival=FestivalContextFact(name="Diwali", date="2020-01-01"),
        )
    )
    assert all(item.festival_context is None for item in rejected.opportunities)
    assert OpportunityKind.FESTIVAL_CAMPAIGN not in _kinds(rejected)


def test_regional_festival_is_not_attached_outside_its_place() -> None:
    stored = known_festival("Onam", year=2026)
    assert stored and stored["date"]
    outside = ENGINE.evaluate(
        _request(
            trend=_trend(
                title="Onam sadhya",
                summary="Onam is being discussed.",
                festival_name="Onam",
                region="Kolkata",
                suggested_product_names=[],
            ),
            business=_business(location="Kolkata"),
            festival=FestivalContextFact(name="Onam", date=stored["date"]),
            products=[],
        )
    )
    assert all(item.festival_context is None for item in outside.opportunities)

    inside = ENGINE.evaluate(
        _request(
            trend=_trend(
                title="Onam sadhya",
                summary="Onam is being discussed.",
                festival_name="Onam",
                region="Kerala",
                suggested_product_names=[],
            ),
            business=_business(business_name="Kerala Stores", location="Kerala"),
            festival=FestivalContextFact(name="Onam", date=stored["date"]),
            products=[],
        )
    )
    matched = [item for item in inside.opportunities if item.festival_context]
    assert matched
    assert matched[0].festival_context["date"] == stored["date"]
    assert matched[0].festival_context["name"] == "Onam"


def test_no_product_does_not_invent_one() -> None:
    result = ENGINE.evaluate(
        _request(
            trend=_trend(themes=["brand"], suggested_product_names=[FAKE_PRODUCT]),
            products=[],
        )
    )
    rendered = str([item.public() for item in result.opportunities])
    assert result.opportunities
    assert all(item.recommended_product is None for item in result.opportunities)
    assert FAKE_PRODUCT not in rendered
    assert FAKE_PRICE not in rendered


def test_expired_trend_yields_no_opportunities() -> None:
    result = ENGINE.evaluate(
        _request(trend=_trend(expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    )
    assert result.opportunities == []
    assert result.reasons == ["trend_expired"]
    assert result.content_request is None
    assert result.published is False
    assert result.handoff_ready is False


def test_low_confidence_trend_stays_manual() -> None:
    result = ENGINE.evaluate(
        _request(trend=_trend(confidence=0.2)),
        automation={"auto_daily_publish": True},
        qa_passed=True,
    )
    assert result.opportunities
    assert result.opportunities[0].confidence == 0.2
    assert result.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert result.requires_approval is True
    assert result.handoff_ready is False
    assert result.published is False


def test_missing_offer_is_not_invented() -> None:
    result = ENGINE.evaluate(
        _request(
            trend=_trend(
                title="Deal seekers",
                summary="People are asking what is available.",
                themes=["discount"],
                suggested_offer_ids=["offer-missing"],
                suggested_product_names=["Gold ring"],
            ),
            offers=[],
        )
    )
    rendered = str([item.public() for item in result.opportunities])
    assert result.opportunities
    assert all(item.recommended_offer is None for item in result.opportunities)
    assert OpportunityKind.OFFER_CAMPAIGN not in _kinds(result)
    assert FAKE_PRICE not in rendered
    assert "% off" not in rendered
    assert "Do not mention a price or an offer." in result.opportunities[0].creative_direction


def test_manual_approval() -> None:
    result = ENGINE.evaluate(_request(), automation={"auto_daily_publish": False}, user_id="user-a")
    assert result.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert result.requires_approval is True
    assert result.handoff_ready is False
    assert result.published is False
    assert result.content_request is not None
    assert result.content_request.user_id == "user-a"
    assert result.content_request.authenticated is True
    assert result.content_mode == ContentMode.DAILY
    assert "human_approval_required" in result.reasons


def test_automatic_approval_does_not_publish() -> None:
    result = ENGINE.evaluate(
        _request(),
        automation={"auto_daily_publish": True},
        qa_passed=True,
        user_id="user-a",
    )
    assert result.approval_status == ApprovalStatus.AUTO_APPROVED
    assert result.handoff_ready is True
    assert result.requires_approval is False
    assert result.published is False
    assert result.content_request is not None
    assert result.content_request.mode == ContentMode.DAILY.value
    assert result.content_request.product_id == "prod-ring"
    assert FAKE_PRICE not in (result.content_request.user_prompt or "")

    blocked = ENGINE.evaluate(_request(), automation={"auto_daily_publish": True}, qa_passed=False)
    assert blocked.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert blocked.handoff_ready is False
    assert blocked.published is False

    source = Path(__file__).resolve().parents[1].joinpath("agent", "opportunity_engine.py").read_text(encoding="utf-8")
    assert "publish_instagram_media" not in source
    assert "media_publish" not in source
