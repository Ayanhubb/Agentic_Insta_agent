"""Meta account intelligence reaches the DeepSeek trend packet through live MCP tools.

The tools stay read-only. Unavailable metrics are gaps, not invented numbers.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.mcp import MCPClient, RepositoryGateway, build_registry, trusted_tenant
from backend.mcp.permissions import INSTAGRAM_PUBLISHING_TOOLS
from backend.trends.analyst import DeepSeekTrendAnalyst, TrendBriefRejected, accept_trend_brief
from backend.trends.packet import TrendIntelligence, build_trend_request, collect_mcp_evidence
from backend.trends.schemas import AccountInsight, TrendAnalysisRequest
from db.crypto import encrypt_token
from db.enums import AccountStatus
from db.repositories import InstagramAccountRepository
from db.schemas import BusinessProfileWrite
from db.session import create_engine_from_url, init_db, session_factory
from db.uow import Database
from models.errors import AppError, ErrorCode
from scheduler.trend_scheduler import _analysis_request
from services.clock import FrozenClock
from tests.helpers import test_settings
from tests.test_mcp_account_intelligence import CAPTION_A, CAPTION_B, TOKEN_A, TOKEN_B, RoutingReader
from tests.test_trend_analysis import NOW, ScriptedChat

TREND_TITLE = "Retail window sets"


class RecordingClient:
    def __init__(self, client: MCPClient) -> None:
        self.client = client
        self.names: list[str] = []

    def discover(self):
        return self.client.discover()

    async def invoke(self, name, arguments, tenant, timeout=None):
        self.names.append(name)
        return await self.client.invoke(name, arguments, tenant, timeout=timeout)


@pytest.fixture
def bridge_world(tmp_path):
    settings = test_settings(tmp_path, deepseek_api_key="ds-test-key", llm_max_attempts=1)
    engine = create_engine_from_url(f"sqlite:///{(tmp_path / 'ig-bridge.db').as_posix()}")
    init_db(engine)
    factory = session_factory(engine)
    session = factory()
    db = Database(session)
    user_a = db.users.create(email="bridge-a@example.com", password_hash="hashed-a")
    user_b = db.users.create(email="bridge-b@example.com", password_hash="hashed-b")
    missing = db.users.create(email="bridge-missing@example.com", password_hash="hashed-m")
    profile = db.business.upsert(
        user_a.id,
        BusinessProfileWrite(
            business_name="Loom House",
            business_type="retail",
            location="Kolkata",
            brand_style="Warm shop windows.",
            products=["Masala chai"],
        ),
    )
    db.instagram_accounts.upsert(user_a.id, "ig-a", encrypt_token(settings, TOKEN_A))
    db.instagram_accounts.upsert(user_b.id, "ig-b", encrypt_token(settings, TOKEN_B))
    db.products.create(user_id=user_a.id, name="Masala chai", category="beverage", is_active=True)
    source = db.trend_sources.create(
        user_a.id,
        business_id=profile.id,
        name="The Hindu",
        source_type="research",
        url="https://www.thehindu.com/example",
        industry="retail",
        region="IN",
    )
    db.trend_observations.create(
        user_a.id,
        source_id=source.id,
        industry="retail",
        region="IN-WB",
        title=TREND_TITLE,
        summary="Shop windows in Kolkata featured retail sets.",
        description="Shop windows in Kolkata featured retail sets.",
        trend_type="retail",
        evidence="The Hindu described retail demand in Kolkata.",
        source="research",
        observed_at=datetime(2026, 9, 20, 9, tzinfo=timezone.utc),
        valid_until=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    db.trend_observations.create(
        user_b.id,
        industry="retail",
        region="IN",
        title="SECRET_OTHER_TREND",
        summary="Other tenant research",
        description="Other tenant research",
        evidence="Do not return this evidence.",
        source="research",
        observed_at=datetime(2026, 9, 20, 9, tzinfo=timezone.utc),
        valid_until=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    db.commit()
    session.close()
    clock = FrozenClock(datetime(2026, 9, 24, 10, tzinfo=timezone.utc))
    reader = RoutingReader()
    client = RecordingClient(
        MCPClient(
            build_registry(RepositoryGateway(factory, clock=clock), settings=settings, reader=reader),
            timeout_seconds=5.0,
        )
    )
    return client, reader, factory, user_a.id, user_b.id, missing.id, settings


def _by_metric(request: TrendAnalysisRequest) -> dict[str, AccountInsight]:
    found: dict[str, AccountInsight] = {}
    for item in request.account_insights:
        found.setdefault(item.metric, item)
    return found


async def test_live_registry_feeds_instagram_intelligence_to_the_deepseek_mock(bridge_world) -> None:
    client, reader, _factory, user_a, user_b, _missing, settings = bridge_world
    discovered = {item["name"] for item in client.discover()}
    for name in (
        "get_account_summary",
        "get_recent_media",
        "get_account_insights",
        "get_top_content",
        "get_content_performance",
    ):
        assert name in discovered
    assert INSTAGRAM_PUBLISHING_TOOLS.isdisjoint(discovered)

    seen: dict[str, TrendAnalysisRequest] = {}

    class CaptureAnalyst:
        async def analyze(self, request: TrendAnalysisRequest):
            seen["request"] = request
            return request

    service = TrendIntelligence(settings, client, analyst=CaptureAnalyst(), now=lambda: NOW)
    await service.analyze(trusted_tenant(user_a, source="scheduler"))
    request = seen["request"]
    metrics = _by_metric(request)
    dumped = json.dumps(request.model_dump(mode="json"))

    assert metrics["sample_size"].epistemic_status == "OBSERVED"
    assert metrics["sample_size"].value == 2
    assert metrics["sample_size"].period_end is not None
    assert metrics["reach"].epistemic_status == "OBSERVED"
    assert metrics["reach"].value == 40
    assert metrics["engagement_rate"].epistemic_status == "INFERRED"
    assert "impressions" not in metrics
    assert "profile_views" not in metrics
    assert any(item.id == "media-a-new" for item in request.historical_performance.points)
    assert any("IMAGE" in note for note in request.instagram_inferences)
    assert all(item.statement not in request.instagram_inferences for item in request.account_insights if item.epistemic_status == "OBSERVED")
    assert request.business_profile is not None
    assert request.business_profile.business_name == "Loom House"
    assert any(item.name == "Masala chai" for item in request.products)
    assert request.festival is not None
    assert any(item.title == TREND_TITLE for item in request.observations)
    assert any(item.epistemic_status == "DISCOVERED" for item in request.observations)
    assert "Instagram metric impressions is unavailable (unsupported_metric)." in request.required_gaps
    assert "Instagram metric profile_views is unavailable (missing_permission)." in request.required_gaps
    assert CAPTION_A not in dumped
    assert CAPTION_B not in dumped
    assert TOKEN_A not in dumped
    assert TOKEN_B not in dumped
    assert "https://" not in dumped
    assert "SECRET_OTHER_TREND" not in dumped
    assert "media-b" not in dumped
    assert INSTAGRAM_PUBLISHING_TOOLS.isdisjoint(client.names)
    assert ("bind", "ig-a") in reader.calls
    assert ("profile", "ig-b") not in reader.calls

    chat = ScriptedChat(["not-json"])
    analyst = DeepSeekTrendAnalyst(settings, transport=chat)
    with pytest.raises(AppError):
        await analyst.analyze(request)
    prompt = json.dumps(chat.payloads)
    assert "5 posts were published during the selected period." in prompt
    assert "Product-focused posts represented a larger share of recent content." in prompt
    assert "Consider testing another product-led creative." in prompt
    assert "Do not present an inference or a recommendation as an observed fact." in prompt
    assert "Loom House" in prompt
    assert "Masala chai" in prompt
    assert TREND_TITLE in prompt
    assert "media-a-new" in prompt
    assert str(metrics["reach"].value) in prompt
    assert CAPTION_A not in prompt
    assert TOKEN_A not in prompt

    other = await service.analyze(trusted_tenant(user_b))
    other_dump = json.dumps(seen["request"].model_dump(mode="json"))
    assert other is seen["request"]
    assert "media-a-new" not in other_dump
    assert "media-a-old" not in other_dump
    assert CAPTION_A not in other_dump
    assert TREND_TITLE not in other_dump
    assert "Loom House" not in other_dump
    assert any(item.id == "media-b" for item in seen["request"].historical_performance.points)


async def test_failure_modes_do_not_fabricate_metrics_or_raise(bridge_world) -> None:
    client, reader, factory, user_a, _user_b, missing, _settings = bridge_world

    async def packet_for(user_id: str) -> TrendAnalysisRequest:
        readings = await collect_mcp_evidence(client, trusted_tenant(user_id))
        return build_trend_request(user_id=user_id, analyzed_at=NOW, readings=readings)

    missing_request = await packet_for(missing)
    assert any("INSTAGRAM_NOT_CONNECTED" in gap for gap in missing_request.required_gaps)
    assert missing_request.account_insights == []
    assert reader.calls == []

    session = factory()
    account = InstagramAccountRepository(session).get_primary(user_a)
    assert account is not None
    account.status = AccountStatus.EXPIRED.value
    account.token_expires_at = NOW - timedelta(days=1)
    session.commit()
    session.close()
    expired = await packet_for(user_a)
    assert any("AUTHENTICATION_ERROR" in gap for gap in expired.required_gaps)
    assert all(item.value != 40 for item in expired.account_insights)

    session = factory()
    account = InstagramAccountRepository(session).get_primary(user_a)
    assert account is not None
    account.status = AccountStatus.CONNECTED.value
    account.token_expires_at = None
    session.commit()
    session.close()
    reader.calls.clear()
    reader.insights["ig-a"] = {"reach": "rate"}
    limited = await packet_for(user_a)
    assert any("rate_limited" in gap for gap in limited.required_gaps)
    assert "reach" not in _by_metric(limited)

    reader.transport_error = httpx.ReadTimeout("timed out")
    timed = await packet_for(user_a)
    assert any("TIMEOUT" in gap for gap in timed.required_gaps)
    assert timed.account_insights == [] or all(item.metric != "reach" for item in timed.account_insights)

    reader.transport_error = None
    reader.error = AppError(
        ErrorCode.API_ERROR,
        f"meta failed token={TOKEN_A}",
        http_status=502,
    )
    failed = await packet_for(user_a)
    failed_dump = json.dumps(failed.model_dump(mode="json"))
    assert any("API_ERROR" in gap for gap in failed.required_gaps)
    assert TOKEN_A not in failed_dump
    assert CAPTION_A not in failed_dump


def test_observed_statement_cannot_cite_an_inferred_metric() -> None:
    request = TrendAnalysisRequest(
        user_id="user-1",
        analyzed_at=NOW,
        account_insights=[
            AccountInsight(
                id="metric-sample-size",
                metric="sample_size",
                value=5,
                statement="5 posts were returned in the authorized Instagram sample.",
                sample_size=5,
                epistemic_status="OBSERVED",
            ),
            AccountInsight(
                id="ig:content_mix",
                metric="content_mix",
                value=2,
                statement="The sample interpretation for content_mix is 2.",
                sample_size=5,
                epistemic_status="INFERRED",
            ),
        ],
        instagram_inferences=["Product-focused posts represented a larger share of recent content."],
    )
    observed_as_fact = {
        "account_summary": {
            "sufficient_data": True,
            "statements": [
                {
                    "kind": "OBSERVED",
                    "text": "The sample interpretation for content_mix is 2.",
                    "evidence_ids": ["ig:content_mix"],
                }
            ],
        },
        "current_trends": [],
        "business_opportunities": [],
        "festival_opportunities": [],
        "content_opportunities": [],
        "risks": [],
        "data_gaps": ["No trend observations were supplied."],
    }
    with pytest.raises(TrendBriefRejected):
        accept_trend_brief(observed_as_fact, request, [], [])

    separated = {
        "account_summary": {
            "sufficient_data": True,
            "statements": [
                {
                    "kind": "OBSERVED",
                    "text": "5 posts were returned in the authorized Instagram sample.",
                    "evidence_ids": ["metric-sample-size"],
                },
                {
                    "kind": "INFERRED",
                    "text": "The sample interpretation for content_mix is 2.",
                    "evidence_ids": ["ig:content_mix"],
                },
            ],
        },
        "current_trends": [],
        "business_opportunities": [],
        "festival_opportunities": [],
        "content_opportunities": [],
        "risks": [],
        "data_gaps": ["No trend observations were supplied."],
    }
    brief = accept_trend_brief(separated, request, [], [])
    kinds = [item.kind.value for item in brief.account_summary.statements]
    assert kinds == ["OBSERVED", "INFERRED"]


def test_scheduler_request_omits_unavailable_metrics_and_keeps_inferences() -> None:
    request = _analysis_request(
        {
            "user_id": "user-1",
            "generated_at": "2026-09-24T09:00:00+00:00",
            "business": {"business_name": "Loom House", "location": "Kolkata"},
            "products": [{"id": "silk-1", "name": "Masala chai"}],
            "festivals": [{"festival_name": "Diwali", "date": "2026-11-08"}],
            "observations": [],
            "performance": {
                "captured_at": "2026-09-24T09:00:00+00:00",
                "sample_size": 2,
                "metrics": [
                    {"metric": "reach", "status": "available", "value": 10},
                    {"metric": "impressions", "status": "unavailable", "value": 999, "reason": "unsupported_metric"},
                    {"metric": "engagement_rate", "status": "available", "value": 0.2},
                ],
                "observations": ["Product-focused posts represented a larger share of recent content."],
                "top_content": [
                    {
                        "instagram_media_id": "media-1",
                        "media_type": "IMAGE",
                        "engagement": 9,
                        "published_at": "2026-09-01T00:00:00+00:00",
                    }
                ],
                "content_mix": {"IMAGE": 2},
            },
        }
    )
    metrics = _by_metric(request)
    assert metrics["reach"].value == 10
    assert metrics["reach"].epistemic_status == "OBSERVED"
    assert metrics["engagement_rate"].epistemic_status == "INFERRED"
    assert "impressions" not in metrics
    assert 999 not in {item.value for item in request.account_insights}
    assert request.instagram_inferences == ["Product-focused posts represented a larger share of recent content."]
    assert any(item.id == "media-1" and item.value == 9 for item in request.historical_performance.points)
    assert request.business_profile is not None
    assert request.products[0].name == "Masala chai"
    assert request.festival is not None
    assert request.festival.display_name == "Diwali"
