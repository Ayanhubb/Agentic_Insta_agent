"""Daily trend intelligence: reports, failures, duplicates, and publish gates."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.models import FestivalPost, InstagramPost, Product
from festivals.india_festivals import festivals_for_year
from models.content import ContentMode
from scheduler.trend_models import TrendDailyOpportunity, TrendDailyReport
from scheduler.trend_scheduler import TrendIntelligenceScheduler
from scheduler.trend_store import trend_dashboard_payload
from services.clock import FrozenClock
from tests.helpers import test_settings
from tests.test_scheduler import FakeContent, FakePublisher, _seed_user, _session


def _observation(observation_id: str = "trend-silk", *, valid_until: str | None = None) -> dict:
    return {
        "id": observation_id,
        "title": "Silk festive retail posts",
        "description": "Retailers are posting festive silk displays.",
        "trend_type": "retail",
        "observed_at": "2026-06-15T00:00:00+05:30",
        "valid_until": valid_until,
        "source": {"name": "trade-brief", "url": "https://example.test/silk"},
        "evidence": "Published brief dated 2026-06-15.",
    }


class ScriptedMeta:
    def __init__(self, *, fail: bool = False, performance: dict | None = None) -> None:
        self.fail = fail
        self.performance = performance or {"source": "meta", "published_count": 4}
        self.calls = 0

    async def fetch_account(self, user_id: str) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("meta unavailable")
        return {"id": "ig-1", "username": "demo", "access_token": "must-not-be-stored"}

    async def fetch_performance(self, user_id: str) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("meta unavailable")
        return dict(self.performance)


class ScriptedResearch:
    def __init__(self, observations: list[dict] | None = None, *, fail: bool = False) -> None:
        self.observations = list(observations or [])
        self.fail = fail
        self.calls = 0

    async def fetch(self, _context: dict) -> list[dict]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("research unavailable")
        return list(self.observations)


class ScriptedAnalyzer:
    def __init__(self, brief: dict | None = None, *, fail: bool = False, opportunities: list[dict] | None = None) -> None:
        self.fail = fail
        self.calls = 0
        self.brief = brief
        self.opportunities = opportunities

    async def analyze(self, context: dict) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("deepseek unavailable")
        if self.brief is not None:
            return self.brief
        observations = context.get("observations") or []
        first = observations[0]["id"] if observations else "missing"
        festivals = context.get("festivals") or []
        campaign_id = festivals[0]["campaign_id"] if festivals and self.opportunities is None else None
        opportunities = self.opportunities
        if opportunities is None:
            item = {
                "trend_id": first,
                "title": "Silk window display",
                "why_now": "The observed retail trend is current.",
                "creative_direction": "Show the store's silk products.",
                "recommended_format": "image",
                "confidence": 0.7,
            }
            if campaign_id:
                item["campaign_id"] = campaign_id
                item["festival_id"] = festivals[0]["festival_name"]
            opportunities = [item, dict(item)]
        return {
            "current_trends": [{"trend_id": first, "title": "Invented headline"}],
            "content_opportunities": opportunities,
            "festival_opportunities": [],
            "business_opportunities": [],
            "data_gaps": [],
        }


class RecordingContent(FakeContent):
    def __init__(self, session, **kwargs) -> None:
        super().__init__(session, **kwargs)
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return await super().run(request)


@pytest.fixture
def harness(tmp_path: Path):
    settings = test_settings(tmp_path, scheduler_enabled=False)
    session = _session(settings)
    user, account, automation, image_path = _seed_user(session, settings, tmp_path)
    clock = FrozenClock(datetime(2026, 6, 15, 18, 30, tzinfo=timezone.utc))
    yield settings, session, user, account, automation, clock, image_path
    session.close()


def _scheduler(harness, *, meta=None, research=None, analyzer=None, content=None, publisher=None, clock=None):
    settings, session, user, account, automation, default_clock, image_path = harness
    content = content or RecordingContent(session, image_path=str(image_path))
    publisher = publisher or FakePublisher(session)
    scheduler = TrendIntelligenceScheduler(
        settings,
        session,
        clock or default_clock,
        meta=meta if meta is not None else ScriptedMeta(),
        research=research if research is not None else ScriptedResearch([_observation()]),
        analyzer=analyzer if analyzer is not None else ScriptedAnalyzer(opportunities=[]),
        content_agent=content,
        publisher=publisher,
    )
    return scheduler, content, publisher


@pytest.mark.asyncio
async def test_scheduler_twice_same_day_does_not_duplicate(harness) -> None:
    research = ScriptedResearch([_observation()])
    analyzer = ScriptedAnalyzer(
        opportunities=[
            {
                "trend_id": "trend-silk",
                "title": "Silk window display",
                "why_now": "The observed retail trend is current.",
                "creative_direction": "Show silk sarees.",
                "recommended_format": "image",
            },
            {
                "trend_id": "trend-silk",
                "title": "Silk window display",
                "why_now": "The observed retail trend is current.",
                "creative_direction": "Show silk sarees.",
                "recommended_format": "image",
            },
        ]
    )
    settings, session, user, _account, automation, _clock, image_path = harness
    automation.auto_daily_publish = False
    automation.auto_festival_publish = False
    scheduler, content, publisher = _scheduler(harness, research=research, analyzer=analyzer)
    first = await scheduler.run_user(user, automation, force=True)
    second = await scheduler.run_user(user, automation, force=True)
    rows = session.query(TrendDailyOpportunity).filter_by(user_id=user.id).all()
    reports = session.query(TrendDailyReport).filter_by(user_id=user.id).all()
    assert first["status"] == "reported"
    assert second["status"] == "already_reported"
    assert second["published"] is False
    assert research.calls == 1
    assert analyzer.calls == 1
    assert len(reports) == 1
    assert len(rows) == 1
    assert content.calls == 0
    assert publisher.calls == 0
    assert image_path.exists()
    assert settings.default_timezone == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_meta_failure_still_reports_external_trends(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = False
    scheduler, _content, _publisher = _scheduler(
        harness,
        meta=ScriptedMeta(fail=True),
        research=ScriptedResearch([_observation()]),
        analyzer=ScriptedAnalyzer(
            opportunities=[
                {
                    "trend_id": "trend-silk",
                    "title": "Silk window display",
                    "why_now": "The observed retail trend is current.",
                }
            ]
        ),
    )
    result = await scheduler.run_user(user, automation, force=True)
    report = session.get(TrendDailyReport, result["report_id"]).payload
    assert result["status"] == "reported"
    assert "meta_unavailable" in report["data_limitations"]
    assert report["account_performance"] is None
    assert report["new_observations"][0]["id"] == "trend-silk"
    assert report["current_trends"][0]["trend_id"] == "trend-silk"
    assert report["current_trends"][0]["title"] == "Silk festive retail posts"
    assert "reach" not in str(report["account_performance"])
    assert "must-not-be-stored" not in str(report)


@pytest.mark.asyncio
async def test_research_failure_keeps_account_performance_without_invented_trends(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = False
    performance = {"source": "meta", "published_count": 4}
    analyzer = ScriptedAnalyzer(
        brief={
            "current_trends": [{"trend_id": "made-up", "title": "Fabricated wave"}],
            "content_opportunities": [
                {
                    "trend_id": "made-up",
                    "title": "Post this invented trend",
                    "why_now": "Because the model guessed.",
                }
            ],
            "data_gaps": [],
        }
    )
    scheduler, _content, _publisher = _scheduler(
        harness,
        meta=ScriptedMeta(performance=performance),
        research=ScriptedResearch(fail=True),
        analyzer=analyzer,
    )
    result = await scheduler.run_user(user, automation, force=True)
    report = session.get(TrendDailyReport, result["report_id"]).payload
    assert "trend_research_unavailable" in report["data_limitations"]
    assert report["account_performance"] == performance
    assert report["new_observations"] == []
    assert report["current_trends"] == []
    assert report["content_opportunities"] == []
    assert "unbacked_recommendations_removed" in report["data_limitations"]
    assert session.query(TrendDailyOpportunity).count() == 0


@pytest.mark.asyncio
async def test_deepseek_failure_stores_raw_observations_in_degraded_mode(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = False
    scheduler, content, publisher = _scheduler(
        harness,
        research=ScriptedResearch([_observation(), _observation("old-trend", valid_until="2020-01-01")]),
        analyzer=ScriptedAnalyzer(fail=True),
    )
    result = await scheduler.run_user(user, automation, force=True)
    report = session.get(TrendDailyReport, result["report_id"]).payload
    assert result["mode"] == "degraded"
    assert report["mode"] == "degraded"
    assert "deepseek_unavailable" in report["data_limitations"]
    assert [item["id"] for item in report["new_observations"]] == ["trend-silk"]
    assert [item["id"] for item in report["expired_trends"]] == ["old-trend"]
    assert report["current_trends"] == []
    assert report["content_opportunities"] == []
    assert content.calls == 0
    assert publisher.calls == 0


@pytest.mark.asyncio
async def test_timezone_uses_business_local_date_not_utc(harness) -> None:
    _settings, _session, user, _account, automation, _clock, _image = harness
    automation.timezone = ""
    scheduler, _content, _publisher = _scheduler(harness, analyzer=ScriptedAnalyzer(opportunities=[]))
    result = await scheduler.run_user(user, automation, force=True)
    assert result["timezone"] == "Asia/Kolkata"
    assert result["local_date"] == "2026-06-16"
    assert datetime(2026, 6, 15, 18, 30, tzinfo=timezone.utc).astimezone(ZoneInfo("UTC")).date().isoformat() == "2026-06-15"


@pytest.mark.asyncio
async def test_festival_campaign_uses_approval_pipeline_when_automatic(harness) -> None:
    settings, session, user, _account, automation, _clock, image_path = harness
    festival = festivals_for_year(2026)[0]
    occurs = date.fromisoformat(str(festival["date"]))
    clock = FrozenClock(datetime.combine(occurs, time(12, 0), tzinfo=ZoneInfo("Asia/Kolkata")))
    automation.auto_daily_publish = False
    automation.auto_festival_publish = True
    automation.timezone = "Asia/Kolkata"
    content = RecordingContent(session, image_path=str(image_path))
    publisher = FakePublisher(session)
    scheduler = TrendIntelligenceScheduler(
        settings,
        session,
        clock,
        meta=ScriptedMeta(),
        research=ScriptedResearch([_observation()]),
        analyzer=ScriptedAnalyzer(),
        content_agent=content,
        publisher=publisher,
    )
    result = await scheduler.run_user(user, automation, force=True)
    assert result["published"] == 1
    assert content.calls == 1
    assert content.requests[0].mode == ContentMode.FESTIVAL
    assert publisher.calls == 1
    opportunity = session.query(TrendDailyOpportunity).filter_by(user_id=user.id).one()
    assert opportunity.campaign_id
    assert opportunity.status == "USED"
    again = await scheduler.run_user(user, automation, force=True)
    assert again["status"] == "already_reported"
    assert content.calls == 1
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_automatic_mode_publishes_only_through_approval_policy(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = True
    automation.auto_festival_publish = False
    content = RecordingContent(session, image_path=str(harness[6]))
    publisher = FakePublisher(session)
    scheduler, _, _ = _scheduler(
        harness,
        content=content,
        publisher=publisher,
        analyzer=ScriptedAnalyzer(
            opportunities=[
                {
                    "trend_id": "trend-silk",
                    "title": "Silk window display",
                    "why_now": "The observed retail trend is current.",
                    "creative_direction": "Show silk sarees in the shop.",
                    "recommended_format": "image",
                }
            ]
        ),
    )
    result = await scheduler.run_user(user, automation, force=True)
    assert result["published_because_trend_only"] is False
    assert result["published"] == 1
    assert content.calls == 1
    assert content.requests[0].mode == ContentMode.DAILY
    assert "Silk window display" in (content.requests[0].user_prompt or "")
    assert publisher.calls == 1
    posts = session.query(InstagramPost).all()
    assert posts
    assert all(post.post_type != "DAILY_RETAIL_POST" for post in posts)
    assert not hasattr(scheduler, "publish")
    assert not hasattr(scheduler, "publish_instagram_media")


@pytest.mark.asyncio
async def test_manual_mode_stops_on_the_dashboard(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = False
    automation.auto_festival_publish = False
    session.add(Product(user_id=user.id, name="sarees", offer="Festive edit", is_active=True))
    session.flush()
    scheduler, content, publisher = _scheduler(
        harness,
        analyzer=ScriptedAnalyzer(
            opportunities=[
                {
                    "trend_id": "trend-silk",
                    "title": "Silk window display",
                    "why_now": "The observed retail trend is current.",
                }
            ]
        ),
    )
    result = await scheduler.run_user(user, automation, force=True)
    notice = trend_dashboard_payload(session, user.id)
    assert result["published"] == 0
    assert result["generated"] == 0
    assert content.calls == 0
    assert publisher.calls == 0
    assert notice is not None
    assert notice["report"]["dashboard_notified"] is True
    assert notice["content_opportunities"][0]["status"] == "NEW"
    assert notice["content_opportunities"][0]["title"] == "Silk window display"
    assert notice["report"]["data_limitations"] == [] or "meta_unavailable" not in notice["report"]["data_limitations"]
    assert any(item["name"] == "sarees" for item in notice["report"]["product_opportunities"])


def test_trend_scheduler_does_not_call_meta_publish() -> None:
    source = Path("scheduler/trend_scheduler.py").read_text(encoding="utf-8").lower()
    for token in ("media_publish", "instagramgraphclient", "graph.facebook.com"):
        assert token not in source


@pytest.mark.asyncio
async def test_scheduler_once_creates_one_report(harness) -> None:
    meta = ScriptedMeta()
    research = ScriptedResearch([_observation()])
    settings, session, user, _account, automation, _clock, _image = harness
    automation.auto_daily_publish = False
    automation.auto_festival_publish = False
    scheduler, content, publisher = _scheduler(harness, meta=meta, research=research)
    result = await scheduler.run_user(user, automation, force=True)
    assert result["status"] == "reported"
    assert result["published"] == 0
    assert result["generated"] == 0
    assert result["timezone"] == "Asia/Kolkata"
    assert meta.calls == 2
    assert research.calls == 1
    assert session.query(TrendDailyReport).filter_by(user_id=user.id).count() == 1
    assert content.calls == 0
    assert publisher.calls == 0
    assert settings.default_timezone == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_user_timezone_changes_the_report_date(harness) -> None:
    _settings, _session, user, _account, automation, _clock, _image = harness
    automation.timezone = "America/Los_Angeles"
    scheduler, _content, _publisher = _scheduler(harness, analyzer=ScriptedAnalyzer(opportunities=[]))
    result = await scheduler.run_user(user, automation, force=True)
    assert result["timezone"] == "America/Los_Angeles"
    assert result["local_date"] == "2026-06-15"


@pytest.mark.asyncio
async def test_stale_trend_is_not_current_and_does_not_generate(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = True
    stale = _observation("old-silk", valid_until="2020-01-01")
    stale["freshness"] = "stale"
    scheduler, content, publisher = _scheduler(
        harness,
        research=ScriptedResearch([stale, _observation(valid_until="2026-06-01")]),
        analyzer=ScriptedAnalyzer(
            opportunities=[
                {
                    "trend_id": "old-silk",
                    "title": "Revive an old trend",
                    "why_now": "The model guessed this was still current.",
                },
                {
                    "trend_id": "trend-silk",
                    "title": "Use the June trend",
                    "why_now": "This date is already past.",
                    "expires_at": "2026-06-01T00:00:00+05:30",
                },
            ]
        ),
    )
    result = await scheduler.run_user(user, automation, force=True)
    report = session.get(TrendDailyReport, result["report_id"]).payload
    assert result["published"] == 0
    assert result["generated"] == 0
    assert {item["id"] for item in report["expired_trends"]} == {"old-silk", "trend-silk"}
    assert report["current_trends"] == []
    assert report["content_opportunities"] == []
    assert content.calls == 0
    assert publisher.calls == 0
    assert session.query(TrendDailyOpportunity).count() == 0


@pytest.mark.asyncio
async def test_expired_opportunity_stays_off_the_dashboard(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = True
    scheduler, content, publisher = _scheduler(
        harness,
        analyzer=ScriptedAnalyzer(
            opportunities=[
                {
                    "trend_id": "trend-silk",
                    "title": "Silk window display",
                    "why_now": "The observed retail trend is current.",
                    "expires_at": "2020-01-02T00:00:00+05:30",
                }
            ]
        ),
    )
    result = await scheduler.run_user(user, automation, force=True)
    notice = trend_dashboard_payload(session, user.id)
    row = session.query(TrendDailyOpportunity).filter_by(user_id=user.id).one()
    assert result["published"] == 0
    assert result["generated"] == 0
    assert row.status == "EXPIRED"
    assert content.calls == 0
    assert publisher.calls == 0
    assert notice is not None
    assert notice["content_opportunities"] == []


@pytest.mark.asyncio
async def test_festival_same_day_post_is_not_published_again(harness) -> None:
    settings, session, user, _account, automation, _clock, image_path = harness
    festival = festivals_for_year(2026)[0]
    occurs = date.fromisoformat(str(festival["date"]))
    clock = FrozenClock(datetime.combine(occurs, time(12, 0), tzinfo=ZoneInfo("Asia/Kolkata")))
    automation.auto_daily_publish = False
    automation.auto_festival_publish = True
    automation.timezone = "Asia/Kolkata"
    from festivals.festival_service import FestivalService

    campaign = FestivalService(session)._repo.get_or_create_campaign(
        user.id,
        festival_name=str(festival["festival_name"]),
        festival_date=occurs,
        year=occurs.year,
        required_posts=2,
        enabled=True,
    )
    session.add(
        FestivalPost(
            campaign_id=campaign.id,
            sequence_number=1,
            status="PUBLISHED",
            scheduled_for=datetime.combine(occurs, time(10, 0), tzinfo=ZoneInfo("Asia/Kolkata")),
        )
    )
    session.flush()
    content = RecordingContent(session, image_path=str(image_path))
    publisher = FakePublisher(session)
    scheduler = TrendIntelligenceScheduler(
        settings,
        session,
        clock,
        meta=ScriptedMeta(),
        research=ScriptedResearch([_observation()]),
        analyzer=ScriptedAnalyzer(),
        content_agent=content,
        publisher=publisher,
    )
    result = await scheduler.run_user(user, automation, force=True)
    assert result["status"] == "reported"
    assert result["published"] == 0
    assert result["generated"] == 0
    assert content.calls == 0
    assert publisher.calls == 0
    opportunity = session.query(TrendDailyOpportunity).filter_by(user_id=user.id).one()
    assert opportunity.status == "NEW"
    assert session.query(InstagramPost).count() == 0


class _DenyVision:
    async def review_image(self, **kwargs: object) -> dict[str, object]:
        return {"passed": False, "reasons": ["blurry"]}


@pytest.mark.asyncio
async def test_automatic_mode_holds_when_image_qa_fails(harness) -> None:
    session = harness[1]
    user, automation = harness[2], harness[4]
    automation.auto_daily_publish = True
    settings = harness[0]
    clock = harness[5]
    content = RecordingContent(session, image_path=str(harness[6]))
    publisher = FakePublisher(session)
    scheduler = TrendIntelligenceScheduler(
        settings,
        session,
        clock,
        meta=ScriptedMeta(),
        research=ScriptedResearch([_observation()]),
        analyzer=ScriptedAnalyzer(
            opportunities=[
                {
                    "trend_id": "trend-silk",
                    "title": "Silk window display",
                    "why_now": "The observed retail trend is current.",
                }
            ]
        ),
        content_agent=content,
        publisher=publisher,
        vision=_DenyVision(),
    )
    result = await scheduler.run_user(user, automation, force=True)
    opportunity = session.query(TrendDailyOpportunity).filter_by(user_id=user.id).one()
    assert result["published"] == 0
    assert result["generated"] == 1
    assert content.calls == 1
    assert publisher.calls == 0
    assert opportunity.status == "REVIEWED"
