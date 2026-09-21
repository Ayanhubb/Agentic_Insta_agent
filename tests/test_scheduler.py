"""Autonomous scheduler tests: daily idempotency, festivals, failures, timezone."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from festivals.festival_service import FestivalService
from festivals.india_festivals import festivals_for_year
from config import Settings
from db.crypto import encrypt_token
from db.models import FestivalCampaign, GeneratedImage, InstagramPost
from db.repositories import (
    AutomationRepository,
    BusinessRepository,
    InstagramAccountRepository,
    UserRepository,
)
from db.session import create_engine_from_settings, init_db, session_factory
from models.content import (
    ApprovalStatus,
    ContentMode,
    ContentPlan,
    ContentSource,
    ContentStrategyResult,
    ContentTask,
    ContentTaskStatus,
    ContentType,
    DiversityVerdict,
    GeneratedImageSnapshot,
    InstagramHandoff,
)
from models.errors import AppError, ErrorCode
from scheduler.daily_scheduler import DailyScheduler
from scheduler.festival_scheduler import FestivalScheduler
from services.clock import FrozenClock
from tests.helpers import DummyInstagramClient, auth_client_headers, test_settings, write_jpeg


class FakeContent:
    def __init__(self, session, *, fail: bool = False, auto_approved: bool = True, image_path: str | None = None) -> None:
        self.session = session
        self.fail = fail
        self.auto_approved = auto_approved
        self.image_path = image_path or "generated.jpg"
        self.calls = 0

    async def run(self, request) -> ContentStrategyResult:
        self.calls += 1
        if self.fail:
            raise AppError(ErrorCode.CONTENT_LLM_FAILED, "Generation failed")
        image = GeneratedImage(
            user_id=request.user_id,
            original_prompt="prompt",
            enhanced_prompt="enhanced prompt for a storefront",
            filename=f"img_{uuid4().hex}.jpg",
            storage_path=self.image_path,
            source=request.mode.value if hasattr(request.mode, "value") else "DAILY_AUTOMATION",
        )
        self.session.add(image)
        self.session.flush()
        plan = ContentPlan(
            content_type=ContentType.PRODUCT,
            theme="retail",
            image_prompt="A detailed photo of the shop interior with products",
            business_context="Local retail shop",
            reason="daily_automation",
        )
        return ContentStrategyResult(
            task=ContentTask(
                id=str(uuid4()),
                user_id=request.user_id,
                mode=request.mode,
                source=ContentSource.DAILY_AUTOMATION,
                status=ContentTaskStatus.APPROVED_PENDING_PUBLISH,
                plan=plan,
                generated_image_id=image.id,
                approval_status=ApprovalStatus.AUTO_APPROVED,
                requires_approval=False,
            ),
            plan=plan,
            generated_image=GeneratedImageSnapshot(id=image.id, storage_path=image.storage_path),
            approval_status=ApprovalStatus.AUTO_APPROVED,
            diversity=DiversityVerdict(accepted=True, reason="ok"),
            handoff=InstagramHandoff(
                ready=True,
                user_id=request.user_id,
                generated_image_id=image.id,
                image_path=image.storage_path,
                auto_approved=self.auto_approved,
            ),
            mode=request.mode,
            source=ContentSource.DAILY_AUTOMATION,
        )


class FakePublisher:
    def __init__(self, session, outcomes: list[str] | str = "PUBLISHED") -> None:
        self.session = session
        self.outcomes = outcomes if isinstance(outcomes, list) else [outcomes]
        self.calls = 0

    async def publish_generated_image(self, **kwargs) -> InstagramPost:
        self.calls += 1
        status = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        post = InstagramPost(
            user_id=kwargs["user_id"],
            instagram_account_id=kwargs["account"].id if kwargs.get("account") else None,
            generated_image_id=kwargs["image"].id,
            status=status,
            post_type=kwargs["post_type"],
            scheduled_date=kwargs.get("scheduled_date"),
            instagram_media_id=f"media-{self.calls}" if status == "PUBLISHED" else None,
            published_at=datetime.now(timezone.utc) if status == "PUBLISHED" else None,
            error=None if status == "PUBLISHED" else status,
        )
        self.session.add(post)
        self.session.flush()
        return post


def _session(settings: Settings):
    engine = create_engine_from_settings(settings)
    init_db(engine)
    return session_factory(engine)()


def _seed_user(session, settings: Settings, tmp_path: Path):
    user = UserRepository(session).create(email=f"{uuid4().hex[:8]}@example.com", password_hash="x")
    BusinessRepository(session).upsert(
        user.id,
        business_name="Demo Store",
        business_type="retail",
        products="sarees",
        services="stitching",
    )
    account = InstagramAccountRepository(session).upsert(
        user.id,
        "ig-user-1",
        encrypt_token(settings, "ig-token"),
    )
    automation = AutomationRepository(session).get_or_create(user.id, "Asia/Kolkata")
    automation.daily_enabled = True
    automation.festival_enabled = True
    automation.auto_daily_publish = True
    automation.auto_festival_publish = True
    automation.festival_posts_per_festival = 2
    automation.daily_post_time = time(0, 0)
    write_jpeg(tmp_path / "generated.jpg")
    session.commit()
    return user, account, automation, tmp_path / "generated.jpg"


@pytest.fixture
def harness(tmp_path: Path):
    settings = test_settings(tmp_path, scheduler_enabled=False)
    session = _session(settings)
    user, account, automation, image_path = _seed_user(session, settings, tmp_path)
    clock = FrozenClock(datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc))
    yield settings, session, user, account, automation, clock, image_path
    session.close()


@pytest.mark.asyncio
async def test_daily_job_publishes_once(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    content = FakeContent(session, image_path=str(image_path))
    publisher = FakePublisher(session)
    scheduler = DailyScheduler(settings, session, content, publisher, clock)
    result = await scheduler.run_user(user, automation, force=True)
    assert result["status"] == "PUBLISHED"
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_daily_duplicate_prevention(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    content = FakeContent(session, image_path=str(image_path))
    publisher = FakePublisher(session)
    scheduler = DailyScheduler(settings, session, content, publisher, clock)
    await scheduler.run_user(user, automation, force=True)
    again = await scheduler.run_user(user, automation, force=True)
    assert again["status"] == "skipped"
    assert again["reason"] == "already_published"
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_daily_restart_does_not_duplicate(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    content = FakeContent(session, image_path=str(image_path))
    publisher = FakePublisher(session)
    first = DailyScheduler(settings, session, content, publisher, clock)
    await first.run_user(user, automation, force=True)
    restarted = DailyScheduler(settings, session, FakeContent(session, image_path=str(image_path)), publisher, clock)
    result = await restarted.run_user(user, automation, force=True)
    assert result["reason"] == "already_published"
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_disabled_automation_skips(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    automation.daily_enabled = False
    scheduler = DailyScheduler(settings, session, FakeContent(session, image_path=str(image_path)), FakePublisher(session), clock)
    result = await scheduler.run_user(user, automation, force=True)
    assert result["reason"] == "automation_disabled"


@pytest.mark.asyncio
async def test_timezone_uses_asia_kolkata(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    clock.set(datetime(2026, 6, 15, 18, 30, tzinfo=timezone.utc))
    publisher = FakePublisher(session)
    scheduler = DailyScheduler(settings, session, FakeContent(session, image_path=str(image_path)), publisher, clock)
    result = await scheduler.run_user(user, automation, force=True)
    assert result["scheduled_date"] == "2026-06-16"
    posts = session.query(InstagramPost).all()
    assert posts[0].scheduled_date.isoformat() == "2026-06-16"


@pytest.mark.asyncio
async def test_failed_daily_post_can_retry(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    publisher = FakePublisher(session, ["FAILED", "PUBLISHED"])
    scheduler = DailyScheduler(settings, session, FakeContent(session, image_path=str(image_path)), publisher, clock)
    first = await scheduler.run_user(user, automation, force=True)
    assert first["status"] == "FAILED"
    second = await scheduler.run_user(user, automation, force=True)
    assert second["status"] == "PUBLISHED"
    assert publisher.calls == 2


@pytest.mark.asyncio
async def test_ambiguous_daily_post_is_not_published(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    publisher = FakePublisher(session, "AMBIGUOUS_PUBLICATION")
    scheduler = DailyScheduler(settings, session, FakeContent(session, image_path=str(image_path)), publisher, clock)
    result = await scheduler.run_user(user, automation, force=True)
    assert result["status"] == "AMBIGUOUS_PUBLICATION"
    skipped = await scheduler.run_user(user, automation, force=True)
    assert skipped["reason"] == "ambiguous"
    published = [row for row in session.query(InstagramPost).all() if row.status == "PUBLISHED"]
    assert published == []


@pytest.mark.asyncio
async def test_festival_two_post_requirement_and_partial_completion(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    clock.set(datetime(2026, 11, 7, 4, 30, tzinfo=timezone.utc))  # 2026-11-07 10:00 IST, day before Diwali
    publisher = FakePublisher(session)
    scheduler = FestivalScheduler(settings, session, FakeContent(session, image_path=str(image_path)), publisher, clock)
    first = await scheduler.run_user(user, automation, force=True)
    diwali = next(item for item in first if item.get("kind") == "pre-festival" or item.get("status") == "PUBLISHED")
    campaign = session.query(FestivalCampaign).filter_by(festival_name="Diwali", user_id=user.id).one()
    assert campaign.required_posts == 2
    assert campaign.published_posts == 1
    assert campaign.remaining_posts == 1

    clock.set(datetime(2026, 11, 8, 4, 30, tzinfo=timezone.utc))  # Diwali 2026-11-08 IST
    second = await scheduler.run_user(user, automation, force=True)
    session.refresh(campaign)
    assert campaign.published_posts == 2
    assert campaign.remaining_posts == 0
    third = await scheduler.run_user(user, automation, force=True)
    assert any(item.get("reason") == "campaign_complete" for item in third)


@pytest.mark.asyncio
async def test_festival_second_post_failure_keeps_published_count(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    clock.set(datetime(2026, 11, 7, 4, 30, tzinfo=timezone.utc))
    first_publisher = FakePublisher(session)
    first = FestivalScheduler(
        settings, session, FakeContent(session, image_path=str(image_path)), first_publisher, clock
    )
    await first.run_user(user, automation, force=True)
    campaign = session.query(FestivalCampaign).filter_by(festival_name="Diwali", user_id=user.id).one()
    assert campaign.published_posts == 1
    assert campaign.remaining_posts == 1

    clock.set(datetime(2026, 11, 8, 4, 30, tzinfo=timezone.utc))
    second = FestivalScheduler(
        settings,
        session,
        FakeContent(session, image_path=str(image_path)),
        FakePublisher(session, "FAILED"),
        clock,
    )
    results = await second.run_user(user, automation, force=True)
    session.refresh(campaign)
    assert any(item.get("status") == "FAILED" for item in results)
    assert campaign.required_posts == 2
    assert campaign.published_posts == 1
    assert campaign.remaining_posts == 1


@pytest.mark.asyncio
async def test_festival_failed_post_does_not_reset_campaign(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    clock.set(datetime(2026, 11, 8, 4, 30, tzinfo=timezone.utc))
    scheduler = FestivalScheduler(
        settings, session, FakeContent(session, fail=True, image_path=str(image_path)), FakePublisher(session), clock
    )
    results = await scheduler.run_user(user, automation, force=True)
    assert any(item["status"] == "failed" for item in results)
    campaign = session.query(FestivalCampaign).filter_by(festival_name="Diwali", user_id=user.id).one()
    assert campaign.required_posts == 2
    assert campaign.published_posts == 0
    assert campaign.remaining_posts == 2


@pytest.mark.asyncio
async def test_festival_ambiguous_post_does_not_count(harness) -> None:
    settings, session, user, account, automation, clock, image_path = harness
    clock.set(datetime(2026, 11, 8, 4, 30, tzinfo=timezone.utc))
    scheduler = FestivalScheduler(
        settings,
        session,
        FakeContent(session, image_path=str(image_path)),
        FakePublisher(session, "AMBIGUOUS_PUBLICATION"),
        clock,
    )
    results = await scheduler.run_user(user, automation, force=True)
    assert any(item["status"] == "AMBIGUOUS_PUBLICATION" for item in results)
    campaign = session.query(FestivalCampaign).filter_by(festival_name="Diwali", user_id=user.id).one()
    assert campaign.published_posts == 0
    assert campaign.remaining_posts == 2


def test_festival_catalog_is_data_driven() -> None:
    items = festivals_for_year(2026)
    names = {item["festival_name"] for item in items}
    assert "Diwali" in names
    assert "Holi" in names
    assert "Eid ul-Fitr" in names
    assert "Pongal" in names
    assert "Durga Puja" in names
    assert len(items) >= 30
    diwali = next(item for item in items if item["festival_name"] == "Diwali")
    assert diwali["date"] == "2026-11-08"
    assert diwali["lunar"] is True


def test_automation_api_round_trip(tmp_settings: Settings) -> None:
    app = create_app(tmp_settings, instagram_client=DummyInstagramClient())  # type: ignore[arg-type]
    with TestClient(app) as client:
        headers = auth_client_headers(client)
        listed = client.get("/api/v1/festivals", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()["festivals"]) >= 20
        updated = client.put(
            "/api/v1/automation",
            headers=headers,
            json={"daily_enabled": True, "festival_enabled": True, "timezone": "Asia/Kolkata"},
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["daily_enabled"] is True
        assert body["timezone"] == "Asia/Kolkata"
        festivals = client.put(
            "/api/v1/festivals/settings",
            headers=headers,
            json={"festival_posts_per_festival": 2, "festival_enabled": True},
        )
        assert festivals.status_code == 200
        assert festivals.json()["festival_posts_per_festival"] == 2
        campaigns = client.get("/api/v1/festivals/campaigns", headers=headers)
        assert campaigns.status_code == 200
        assert "campaigns" in campaigns.json()
        ran = client.post("/api/v1/automation/run-now", headers=headers)
        assert ran.status_code == 200
        assert "daily" in ran.json()
