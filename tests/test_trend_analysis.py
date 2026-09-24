"""DeepSeek trend reasoning, with the model mocked."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from backend.mcp.client import ToolResult
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.mcp.tenant_isolation import trusted_tenant
from backend.trends.analyst import DeepSeekTrendAnalyst
from backend.trends.packet import (
    MCP_EVIDENCE_TOOLS,
    TrendIntelligence,
    build_trend_request,
)
from backend.trends.schemas import (
    INSUFFICIENT_HISTORY,
    AccountInsight,
    CreativeAsset,
    HistoricalPerformance,
    OfferContext,
    ProductContext,
    TrendAnalysisRequest,
    TrendEvidence,
    TrendObservation,
    VisualCreativeNote,
)
from models.content import BusinessProfileSnapshot, FestivalContext
from models.errors import AppError, ErrorCode
from tests.helpers import test_settings

NOW = datetime(2026, 9, 24, 9, tzinfo=timezone.utc)
RECENT = datetime(2026, 9, 20, 9, tzinfo=timezone.utc)
UNTIL = datetime(2026, 10, 15, tzinfo=timezone.utc)
PRODUCT = "Banarasi silk saree"


class ScriptedChat:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.payloads: list[dict] = []

    async def chat(self, payload: dict) -> dict:
        self.payloads.append(payload)
        if not self.contents:
            raise AssertionError("DeepSeek was called more times than the test scripted")
        content = self.contents.pop(0)
        return {
            "id": "req-test",
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        }


class FakeVision:
    def __init__(self, note: VisualCreativeNote | None) -> None:
        self.note = note
        self.calls = 0

    async def describe_creative(self, asset: CreativeAsset) -> VisualCreativeNote | None:
        self.calls += 1
        assert asset.kind in {"instagram_creative", "product_asset"}
        return self.note


def _settings(tmp_path, **overrides):
    return test_settings(tmp_path, deepseek_api_key="ds-test-key", llm_max_attempts=2, **overrides)


def _evidence(evidence_id: str, excerpt: str, observed_at: datetime = RECENT) -> TrendEvidence:
    return TrendEvidence(
        id=evidence_id,
        source_name="Retail News",
        excerpt=excerpt,
        observed_at=observed_at,
    )


def _observation(
    observation_id: str,
    evidence_id: str,
    *,
    observed_at: datetime = RECENT,
    valid_until: datetime | None = UNTIL,
    region: str | None = "Kolkata",
    conflicts_with: list[str] | None = None,
) -> TrendObservation:
    return TrendObservation(
        id=observation_id,
        title="Silk window styling",
        description="Boutiques are styling silk sarees in street-facing windows.",
        trend_type="visual",
        industry="retail",
        region=region,
        keywords=["silk", "window"],
        evidence=[_evidence(evidence_id, "Kolkata boutiques are styling silk sarees in street-facing windows.", observed_at)],
        observed_at=observed_at,
        valid_until=valid_until,
        conflicts_with=conflicts_with or [],
    )


def _insights() -> list[AccountInsight]:
    return [
        AccountInsight(
            id="insight-posts",
            metric="posts",
            value=12,
            statement="The account had 12 posts during the analyzed period.",
            period_start=datetime(2026, 9, 1, tzinfo=timezone.utc),
            period_end=NOW,
            sample_size=12,
        ),
        AccountInsight(
            id="insight-mix",
            metric="product_post_share",
            value=40,
            statement="Product-focused posts represented 40% of the sample.",
            sample_size=12,
        ),
    ]


def _request(**overrides) -> TrendAnalysisRequest:
    payload = {
        "user_id": "user1",
        "analyzed_at": NOW,
        "observations": [_observation("obs-silk", "ev-silk"), _observation("obs-window", "ev-window")],
        "account_insights": _insights(),
        "business_profile": BusinessProfileSnapshot(
            business_name="Loom House",
            business_type="retail",
            location="Kolkata",
        ),
        "products": [ProductContext(id="prod-silk", name=PRODUCT, category="apparel")],
        "offers": [OfferContext(id="offer-festive", name="Festive edit", product_id="prod-silk")],
        "festival": FestivalContext(name="Diwali", date=date(2026, 10, 20), year=2026),
        "historical_performance": HistoricalPerformance(),
    }
    payload.update(overrides)
    return TrendAnalysisRequest(**payload)


def _statement(kind: str, text: str, evidence_ids: list[str]) -> dict:
    return {"kind": kind, "text": text, "evidence_ids": evidence_ids}


def _account(sufficient: bool = True) -> dict:
    return {
        "sufficient_data": sufficient,
        "statements": [
            _statement("OBSERVED", "The account had 12 posts during the analyzed period.", ["insight-posts"]),
            _statement("OBSERVED", "Product-focused posts represented 40% of the sample.", ["insight-mix"]),
            _statement(
                "INFERRED",
                "This may indicate an opportunity to show more product-led content.",
                ["insight-mix"],
            ),
            _statement("RECOMMENDED", "Consider testing a product-led creative.", ["insight-mix"]),
        ]
        if sufficient
        else [],
    }


def _trend(
    evidence_ids: list[str],
    *,
    strength: str,
    freshness: str,
    confidence: str,
    regional: str = "high",
    product: str = "high",
) -> dict:
    return {
        "id": "trend-silk",
        "title": "Silk window styling",
        "summary": (
            f"Sourced reporting describes silk window styling for the {PRODUCT}."
            if product in {"high", "moderate"}
            else "Sourced reporting describes silk window styling."
        ),
        "evidence_ids": evidence_ids,
        "evidence_strength": strength,
        "freshness": freshness,
        "business_relevance": "high",
        "regional_relevance": regional,
        "product_relevance": product,
        "confidence": confidence,
        "statements": [
            _statement("DISCOVERED", "Sourced reporting described silk window styling.", evidence_ids[:1]),
            _statement(
                "DISCOVERED",
                "The supplied sources describe silk sarees in street-facing windows.",
                evidence_ids[:1],
            ),
            _statement(
                "INFERRED",
                "This may indicate demand for a silk window still.",
                evidence_ids[:1],
            ),
            _statement("RECOMMENDED", "Consider testing a product-led creative.", evidence_ids[:1]),
        ],
    }


def _opportunity(
    evidence_ids: list[str],
    *,
    confidence: str,
    festival: bool,
    product: str = "high",
    expiration: str | None = None,
) -> dict:
    why = (
        "This may indicate a Diwali display for the silk window."
        if festival
        else "This may indicate a window to show silk before the season turns."
    )
    if expiration is None:
        expiration = "2026-10-20T00:00:00+00:00" if festival else "2026-10-15T00:00:00+00:00"
    direction = (
        f"Center the {PRODUCT} in a shop window."
        if product in {"high", "moderate"}
        else "Center the fabric in a shop window."
    )
    return {
        "title": "Silk window still",
        "why_now": _statement("INFERRED", why, evidence_ids[:1]),
        "evidence": evidence_ids,
        "business_relevance": "high",
        "product_relevance": product,
        "festival_relevance": "moderate" if festival else "none",
        "recommended_format": "single image",
        "creative_direction": direction,
        "expiration": expiration,
        "confidence": confidence,
    }


def _brief(
    *,
    evidence_ids: list[str],
    strength: str,
    freshness: str,
    confidence: str,
    opportunity_confidence: str,
    account_sufficient: bool = True,
    trends: bool = True,
    festival: bool = True,
    regional: str = "high",
    product: str = "high",
    data_gaps: list[str] | None = None,
    opportunities: bool = True,
    expiration: str | None = None,
) -> str:
    trend_items = []
    if trends:
        trend_items.append(
            _trend(
                evidence_ids,
                strength=strength,
                freshness=freshness,
                confidence=confidence,
                regional=regional,
                product=product,
            )
        )
    plain = (
        _opportunity(
            evidence_ids,
            confidence=opportunity_confidence,
            festival=False,
            product=product,
            expiration=expiration,
        )
        if opportunities
        else None
    )
    festive = (
        _opportunity(
            evidence_ids,
            confidence=opportunity_confidence,
            festival=True,
            product=product,
        )
        if opportunities and festival
        else None
    )
    body = {
        "account_summary": _account(account_sufficient),
        "current_trends": trend_items,
        "business_opportunities": [plain] if plain else [],
        "festival_opportunities": [festive] if festive else [],
        "content_opportunities": [plain] if plain else [],
        "risks": [
            _statement("INFERRED", "This may indicate the window trend is competitive.", evidence_ids[:1])
        ]
        if trends
        else [],
        "data_gaps": data_gaps or [],
    }
    return json.dumps(body)


def _strong_brief() -> str:
    return _brief(
        evidence_ids=["obs-silk", "obs-window"],
        strength="strong evidence",
        freshness="current",
        confidence="high",
        opportunity_confidence="high",
    )


async def _analyze(tmp_path, contents: list[str], request: TrendAnalysisRequest, vision=None):
    chat = ScriptedChat(contents)
    analyst = DeepSeekTrendAnalyst(_settings(tmp_path), transport=chat, vision=vision)
    return await analyst.analyze(request), chat


def test_methodology_has_no_numeric_score():
    from backend.trends.schemas import TREND_CLASSIFICATION_METHODOLOGY

    assert "no numeric trend score" in TREND_CLASSIFICATION_METHODOLOGY
    assert "strong evidence" in TREND_CLASSIFICATION_METHODOLOGY


async def test_valid_analysis(tmp_path):
    brief, chat = await _analyze(tmp_path, [_strong_brief()], _request())
    kinds = [item.kind.value for item in brief.account_summary.statements]
    assert kinds == ["OBSERVED", "OBSERVED", "INFERRED", "RECOMMENDED"]
    trend_kinds = [item.kind.value for item in brief.current_trends[0].statements]
    assert "DISCOVERED" in trend_kinds
    assert "RECOMMENDED" in trend_kinds
    assert [item.value for item in [brief.current_trends[0].evidence_strength]] == ["strong evidence"]
    assert brief.current_trends[0].freshness.value == "current"
    assert brief.current_trends[0].confidence == "high"
    opportunity = brief.content_opportunities[0]
    assert opportunity.why_now.kind.value == "INFERRED"
    assert opportunity.evidence == ["obs-silk", "obs-window"]
    assert opportunity.business_relevance.value == "high"
    assert opportunity.product_relevance.value == "high"
    assert opportunity.festival_relevance.value == "none"
    assert brief.festival_opportunities[0].festival_relevance.value == "moderate"
    assert "Diwali" in brief.festival_opportunities[0].why_now.text
    assert opportunity.recommended_format == "single image"
    assert opportunity.creative_direction
    assert opportunity.confidence == "high"
    assert opportunity.expiration is not None
    assert brief.generated_at == NOW
    assert "trend_score" not in brief.model_dump()
    system = chat.payloads[0]["messages"][0]["content"]
    user = chat.payloads[0]["messages"][1]["content"]
    assert "do not browse" in system
    assert "do not call Meta" in system
    assert "do not publish" in system
    assert "do not invent" in system
    assert "festival_relevance" in user
    assert "tools" not in chat.payloads[0]


async def test_invalid_json(tmp_path):
    with pytest.raises(AppError) as caught:
        await _analyze(tmp_path, ["not json", "still not json"], _request())
    assert caught.value.code is ErrorCode.DEEPSEEK_INVALID_RESPONSE


async def test_missing_evidence(tmp_path):
    invented = _brief(
        evidence_ids=["obs-missing"],
        strength="limited evidence",
        freshness="stale",
        confidence="low",
        opportunity_confidence="low",
        festival=False,
        opportunities=False,
    )
    honest = json.dumps(
        {
            "account_summary": _account(True),
            "current_trends": [],
            "business_opportunities": [],
            "festival_opportunities": [],
            "content_opportunities": [],
            "risks": [],
            "data_gaps": ["No trend observations were supplied."],
        }
    )
    request = _request(observations=[], festival=None)
    brief, chat = await _analyze(tmp_path, [invented, honest], request)
    assert brief.current_trends == []
    assert brief.content_opportunities == []
    assert "observation" in " ".join(brief.data_gaps).lower()
    assert len(chat.payloads) == 2


async def test_stale_trend(tmp_path):
    stale_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    request = _request(
        observations=[_observation("obs-old", "ev-old", observed_at=stale_at, valid_until=datetime(2026, 9, 1, tzinfo=timezone.utc))],
        festival=None,
    )
    wrong = _brief(
        evidence_ids=["obs-old"],
        strength="strong evidence",
        freshness="current",
        confidence="high",
        opportunity_confidence="high",
        festival=False,
    )
    right = _brief(
        evidence_ids=["obs-old"],
        strength="limited evidence",
        freshness="stale",
        confidence="low",
        opportunity_confidence="low",
        festival=False,
        expiration="2026-09-24T09:00:00+00:00",
    )
    brief, chat = await _analyze(tmp_path, [wrong, right], request)
    assert brief.current_trends[0].freshness.value == "stale"
    assert brief.current_trends[0].evidence_strength.value == "limited evidence"
    assert brief.current_trends[0].confidence == "low"
    assert len(chat.payloads) == 2
    assert "methodology" in chat.payloads[1]["messages"][1]["content"]


async def test_conflicting_evidence(tmp_path):
    request = _request(
        observations=[
            _observation("obs-silk", "ev-silk", conflicts_with=["obs-window"]),
            _observation("obs-window", "ev-window", conflicts_with=["obs-silk"]),
        ],
        festival=None,
    )
    payload = _brief(
        evidence_ids=["obs-silk", "obs-window"],
        strength="limited evidence",
        freshness="current",
        confidence="low",
        opportunity_confidence="low",
        festival=False,
    )
    brief, _chat = await _analyze(tmp_path, [payload], request)
    assert brief.current_trends[0].evidence_strength.value == "limited evidence"
    assert brief.current_trends[0].confidence == "low"
    assert brief.content_opportunities[0].confidence == "low"


async def test_insufficient_account_data(tmp_path):
    request = _request(
        observations=[_observation("obs-silk", "ev-silk")],
        account_insights=[],
        historical_performance=HistoricalPerformance(),
        festival=None,
    )
    wrong = _brief(
        evidence_ids=["obs-silk"],
        strength="moderate evidence",
        freshness="current",
        confidence="moderate",
        opportunity_confidence="moderate",
        account_sufficient=True,
        festival=False,
    )
    right = _brief(
        evidence_ids=["obs-silk"],
        strength="moderate evidence",
        freshness="current",
        confidence="moderate",
        opportunity_confidence="moderate",
        account_sufficient=False,
        festival=False,
        data_gaps=["Account insights and historical performance were not supplied."],
    )
    brief, _chat = await _analyze(tmp_path, [wrong, right], request)
    assert brief.account_summary.sufficient_data is False
    assert brief.account_summary.statements == []
    assert "account" in " ".join(brief.data_gaps).lower()


async def test_no_trends(tmp_path):
    payload = json.dumps(
        {
            "account_summary": _account(True),
            "current_trends": [],
            "business_opportunities": [],
            "festival_opportunities": [],
            "content_opportunities": [],
            "risks": [],
            "data_gaps": ["No supplied trend was relevant to the business."],
        }
    )
    brief, _chat = await _analyze(tmp_path, [payload], _request(festival=None))
    assert brief.current_trends == []
    assert brief.business_opportunities == []
    assert brief.data_gaps


async def test_regional_trend(tmp_path):
    request = _request(
        observations=[_observation("obs-east", "ev-east", region="West Bengal")],
        festival=None,
    )
    payload = _brief(
        evidence_ids=["obs-east"],
        strength="moderate evidence",
        freshness="current",
        confidence="moderate",
        opportunity_confidence="moderate",
        festival=False,
        product="moderate",
    )
    brief, _chat = await _analyze(tmp_path, [payload], request)
    trend = brief.current_trends[0]
    assert trend.regional_relevance.value == "high"
    assert request.observations[0].region == "West Bengal"


async def test_festival_opportunity(tmp_path):
    request = _request(observations=[_observation("obs-silk", "ev-silk")])
    payload = _brief(
        evidence_ids=["obs-silk"],
        strength="moderate evidence",
        freshness="current",
        confidence="moderate",
        opportunity_confidence="moderate",
        festival=True,
        product="moderate",
    )
    brief, _chat = await _analyze(tmp_path, [payload], request)
    opportunity = brief.festival_opportunities[0]
    assert opportunity.festival_relevance.value == "moderate"
    assert "Diwali" in opportunity.why_now.text
    assert opportunity.evidence == ["obs-silk"]
    assert opportunity.expiration is not None
    assert opportunity.expiration > NOW


async def test_visual_analysis_skips_sensitive_attributes(tmp_path):
    safe = VisualCreativeNote(
        asset_id="asset-1",
        composition="Square crop with the saree centered.",
        product_visibility="The saree fills the frame.",
        logo_visibility="Logo sits in the lower corner.",
        text_readability="The price line is readable.",
        visual_consistency="Matches the warm shop-window series.",
        repeated_patterns=["centered product"],
    )
    request = _request(
        observations=[_observation("obs-silk", "ev-silk")],
        festival=None,
        creative_assets=[CreativeAsset(id="asset-1", kind="instagram_creative", image_base64="aW1hZ2U=")],
    )
    payload = _brief(
        evidence_ids=["obs-silk"],
        strength="moderate evidence",
        freshness="current",
        confidence="moderate",
        opportunity_confidence="moderate",
        festival=False,
        product="moderate",
    )
    brief, chat = await _analyze(tmp_path, [payload], request, vision=FakeVision(safe))
    assert brief.visual_notes[0].composition == "Square crop with the saree centered."
    assert "Square crop with the saree centered." in chat.payloads[0]["messages"][1]["content"]
    assert "http://" not in chat.payloads[0]["messages"][1]["content"]

    sensitive = VisualCreativeNote(asset_id="asset-1", composition="The subject appears 25 years old.")
    redacted, redacted_chat = await _analyze(tmp_path, [payload], request, vision=FakeVision(sensitive))
    assert redacted.visual_notes == []
    assert "sensitive" in " ".join(redacted.data_gaps).lower()
    assert "years old" not in redacted_chat.payloads[0]["messages"][1]["content"]


async def test_missing_product(tmp_path):
    request = _request(products=[], offers=[], festival=None)
    wrong = _brief(
        evidence_ids=["obs-silk", "obs-window"],
        strength="strong evidence",
        freshness="current",
        confidence="high",
        opportunity_confidence="high",
        festival=False,
        product="high",
    )
    right = _brief(
        evidence_ids=["obs-silk", "obs-window"],
        strength="strong evidence",
        freshness="current",
        confidence="high",
        opportunity_confidence="high",
        festival=False,
        product="none",
        opportunities=False,
    )
    brief, chat = await _analyze(tmp_path, [wrong, right], request)
    assert brief.content_opportunities == []
    assert "product" in " ".join(brief.data_gaps).lower()
    assert len(chat.payloads) == 2


async def test_insufficient_history_does_not_compare(tmp_path):
    request = _request(
        account_insights=[
            AccountInsight(
                id="insight-posts",
                metric="published",
                value=3,
                statement="3 posts are stored for this account.",
                sample_size=3,
            )
        ],
        festival=None,
        observations=[_observation("obs-silk", "ev-silk")],
    )
    compared = _brief(
        evidence_ids=["obs-silk"],
        strength="moderate evidence",
        freshness="current",
        confidence="moderate",
        opportunity_confidence="moderate",
        festival=False,
        product="moderate",
        account_sufficient=True,
    )
    body = json.loads(compared)
    body["account_summary"] = {
        "sufficient_data": True,
        "statements": [
            _statement("OBSERVED", "3 posts are stored for this account.", ["insight-posts"]),
            _statement("INFERRED", "Product posts had higher engagement than the rest.", ["insight-posts"]),
        ],
    }
    honest = json.loads(compared)
    honest["account_summary"] = {
        "sufficient_data": True,
        "statements": [_statement("OBSERVED", "3 posts are stored for this account.", ["insight-posts"])],
    }
    honest["risks"] = []
    brief, chat = await _analyze(tmp_path, [json.dumps(body), json.dumps(honest)], request)
    assert INSUFFICIENT_HISTORY in brief.data_gaps
    rendered = json.dumps(brief.model_dump(mode="json"))
    assert "higher" not in rendered
    assert len(chat.payloads) == 2


async def test_invalid_deepseek_response(tmp_path):
    invalid = json.dumps(
        {
            "account_summary": "not-an-object",
            "current_trends": [],
            "business_opportunities": [],
            "festival_opportunities": [],
            "content_opportunities": [],
            "risks": [],
            "data_gaps": [],
        }
    )
    with pytest.raises(AppError) as caught:
        await _analyze(tmp_path, [invalid, invalid], _request())
    assert caught.value.code is ErrorCode.DEEPSEEK_INVALID_RESPONSE


class TimeoutChat:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, payload: dict) -> dict:
        self.calls += 1
        raise httpx.TimeoutException("timed out")


async def test_provider_timeout(tmp_path):
    chat = TimeoutChat()
    analyst = DeepSeekTrendAnalyst(_settings(tmp_path), transport=chat)
    with pytest.raises(AppError) as caught:
        await analyst.analyze(_request())
    assert caught.value.code is ErrorCode.DEEPSEEK_TIMEOUT
    assert chat.calls == 2


def _mcp_observation(observation_id: str, *, industry: str, region: str, source: str) -> dict:
    return {
        "id": observation_id,
        "title": f"{industry} note",
        "summary": f"{source} described {industry} demand in {region}.",
        "industry": industry,
        "region": region,
        "trend_type": "retail",
        "source_name": source,
        "source_url": "https://www.thehindu.com/example",
        "evidence": f"{source} described {industry} demand in {region}.",
        "observed_at": "2026-09-20T09:00:00+00:00",
        "valid_until": "2026-10-15T00:00:00+00:00",
        "is_current": True,
        "stale": False,
        "confidence": 0.91,
        "primary_source": {"source_name": source, "source_url": "https://www.thehindu.com/example"},
    }


class RecordingMCP:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict]] = []

    async def invoke(self, name: str, arguments: dict, tenant, timeout=None):
        self.calls.append((name, dict(arguments)))
        industry = arguments.get("industry")
        region = arguments.get("region")
        key = name
        if name == "get_current_trends":
            key = f"{name}:{industry or 'all'}"
        elif name == "get_regional_trends":
            key = f"{name}:{region}"
        return ToolResult(name=name, data=self.payloads.get(key, {"found": False}))


def _readings() -> dict[str, dict]:
    retail = _mcp_observation("obs-retail", industry="retail", region="IN-WB", source="The Hindu")
    food = _mcp_observation("obs-food", industry="fnb", region="IN", source="Mint")
    return {
        "get_business_profile": {
            "found": True,
            "profile": {
                "business_name": "Loom House",
                "business_type": "retail",
                "location": "Kolkata",
                "products": ["Banarasi silk saree"],
            },
        },
        "get_brand_guidelines": {"found": True, "guidelines": {"brand_style": "Warm shop windows."}},
        "get_active_offers": {"offers": []},
        "get_account_summary": {
            "found": True,
            "business": {"business_name": "Loom House"},
            "intelligence": {
                "sample_size": 3,
                "captured_at": "2026-09-24T09:00:00+00:00",
                "metrics": [
                    {
                        "metric": "engagement_rate",
                        "status": "unavailable",
                        "value": None,
                        "reason": "not_provided_by_meta",
                    }
                ],
            },
        },
        "get_recent_media": {
            "media": [
                {"id": "post-1", "post_type": "IMAGE", "published_at": "2026-09-01T00:00:00+00:00"},
                {"id": "post-2", "post_type": "IMAGE", "published_at": "2026-09-08T00:00:00+00:00"},
                {"id": "post-3", "post_type": "IMAGE", "published_at": "2026-09-15T00:00:00+00:00"},
            ]
        },
        "get_account_insights": {"found": True, "insights": {"published": 3, "by_type": []}},
        "get_top_content": {"media": []},
        "get_content_performance": {"published": 3, "by_type": [{"post_type": "IMAGE", "published": 3}]},
        "get_current_trends:all": {"observations": [retail, food]},
        "get_current_trends:retail": {"observations": [retail]},
        "get_current_trends:fnb": {"observations": [food]},
        "get_current_trends:food": {"observations": []},
        "get_regional_trends:IN": {"observations": [food]},
        "get_regional_trends:IN-WB-KOLKATA": {"observations": [retail]},
        "get_festival_opportunities": {
            "opportunities": [{"title": "Diwali", "date": "2026-10-20", "region": "IN"}]
        },
        "get_business_opportunities": {
            "opportunities": [{"kind": "product", "title": "Banarasi silk saree", "sku": "silk-1", "reason": "Active product has no offer."}]
        },
        "get_content_opportunities": {"opportunities": []},
    }


def test_packet_uses_stored_evidence_only():
    request = build_trend_request(user_id="user1", analyzed_at=NOW, readings=_collected(_readings()))
    dumped = json.dumps(request.model_dump(mode="json"))
    assert request.coverage.retail_trends is True
    assert request.coverage.food_and_beverage_trends is True
    assert request.coverage.indian_trends is True
    assert request.coverage.regional_trends is True
    assert request.coverage.engagement_metrics is False
    assert request.coverage.content_history is True
    assert request.festival is not None
    assert request.festival.display_name == "Diwali"
    festival_day = request.festival.date
    if isinstance(festival_day, datetime):
        festival_day = festival_day.date()
    assert festival_day == date(2026, 10, 20)
    assert [item.name for item in request.products] == ["Banarasi silk saree"]
    assert request.offers == []
    assert request.account_insights[0].sample_size == 3
    assert request.account_insights[0].value == 3
    assert all(item.epistemic_status == "DISCOVERED" for item in request.observations)
    assert INSUFFICIENT_HISTORY in request.required_gaps
    assert "Engagement metrics were not provided." in request.required_gaps
    assert "https://" not in dumped
    assert "0.91" not in dumped
    assert "source_url" not in dumped
    packet_source = Path("backend/trends/packet.py").read_text(encoding="utf-8")
    assert "api.deepseek.com" not in packet_source
    assert "graph.facebook.com" not in packet_source
    assert "media_publish" not in packet_source
    assert "publish_instagram" not in packet_source


def _collected(payloads: dict[str, dict]) -> dict:
    return {
        "business": payloads["get_business_profile"],
        "guidelines": payloads["get_brand_guidelines"],
        "active_offers": payloads["get_active_offers"],
        "account": payloads["get_account_summary"],
        "media": payloads["get_recent_media"],
        "insights": payloads["get_account_insights"],
        "top": payloads["get_top_content"],
        "performance": payloads["get_content_performance"],
        "trends": [
            payloads["get_current_trends:all"],
            payloads["get_current_trends:retail"],
            payloads["get_current_trends:fnb"],
            payloads["get_current_trends:food"],
            payloads["get_regional_trends:IN"],
            payloads["get_regional_trends:IN-WB-KOLKATA"],
        ],
        "festivals": payloads["get_festival_opportunities"],
        "business_opportunities": payloads["get_business_opportunities"],
        "content_gaps": payloads["get_content_opportunities"],
        "read_errors": [],
    }


async def test_mcp_reads_do_not_publish_or_call_the_model(tmp_path):
    client = RecordingMCP(_readings())
    seen = {}

    class CaptureAnalyst:
        async def analyze(self, request: TrendAnalysisRequest):
            seen["request"] = request
            return request

    service = TrendIntelligence(_settings(tmp_path), client, analyst=CaptureAnalyst(), now=lambda: NOW)
    await service.analyze(trusted_tenant("user1"))
    names = [name for name, _arguments in client.calls]
    assert set(MCP_EVIDENCE_TOOLS).issubset(set(names))
    assert INSTAGRAM_PUBLISHING_TOOLS.isdisjoint(names)
    assert "get_current_trends" in names
    assert "get_regional_trends" in names
    assert seen["request"].user_id == "user1"
    assert seen["request"].observations
