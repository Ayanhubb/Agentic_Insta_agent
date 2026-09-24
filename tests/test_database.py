"""Persistence tests: schema, repositories, duplicate protection, and user isolation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from api.task_store import TaskStore
from db.crypto import TokenEncryptor
from db.enums import (
    DEFAULT_TIMEZONE,
    ImageSource,
    JobType,
    PostStatus,
    PostType,
    TaskTrigger,
    TaskType,
)
from db.exceptions import DuplicateRecordError
from db.models import InstagramAccount
from db.persist import load_agent_state, persist_agent_state
from db.schemas import (
    AgentEventCreate,
    AgentTaskCreate,
    AutomationSettingsWrite,
    BusinessProfileWrite,
    FestivalCampaignCreate,
    FestivalPostCreate,
    GeneratedImageCreate,
    InstagramAccountCreate,
    InstagramPostCreate,
    ScheduledJobCreate,
    UserCreate,
)
from db.session import create_engine_from_url, init_db
from db.uow import Database
from models.state import AgentState, TaskStatus


@pytest.fixture
def encryptor() -> TokenEncryptor:
    return TokenEncryptor("test-token-encryption-key")


@pytest.fixture
def engine(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'agentic.db').as_posix()}"
    engine = create_engine_from_url(url)
    init_db(engine)
    return engine


@pytest.fixture
def session(engine) -> Session:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    yield session
    session.close()


@pytest.fixture
def db(session: Session, encryptor: TokenEncryptor) -> Database:
    return Database(session, encryptor=encryptor)


def test_database_creation_creates_required_tables(engine) -> None:
    names = set(inspect(engine).get_table_names())
    expected = {
        "users",
        "instagram_accounts",
        "business_profiles",
        "generated_images",
        "instagram_posts",
        "agent_tasks",
        "agent_events",
        "scheduled_jobs",
        "festival_campaigns",
        "festival_posts",
        "automation_settings",
        "sessions",
        "daily_post_slots",
        "business_assets",
        "brand_profiles",
        "brand_guidelines",
        "products",
        "product_assets",
        "trend_sources",
        "trend_observations",
        "trend_evidence",
        "account_snapshots",
        "media_snapshots",
        "insight_snapshots",
        "trend_reports",
        "content_opportunities",
    }
    assert expected.issubset(names)


def test_user_creation(db: Database) -> None:
    user = db.users.create(UserCreate(email="Alice@Example.com", password_hash="hashed-secret"))
    db.commit()
    loaded = db.users.get_by_email("alice@example.com")
    assert loaded is not None
    assert loaded.id == user.id
    assert loaded.email == "alice@example.com"
    assert loaded.is_active is True
    assert loaded.is_admin is False
    assert loaded.password_hash == "hashed-secret"


def test_business_profile(db: Database) -> None:
    user = db.users.create(UserCreate(email="shop@example.com", password_hash="x"))
    profile = db.business_profiles.upsert(
        user.id,
        BusinessProfileWrite(
            business_name="Pal Mart",
            business_type="retail",
            business_category="grocery",
            location="Kolkata",
            products=["rice", "dal"],
            services=["delivery"],
        ),
    )
    db.commit()
    loaded = db.business_profiles.get_for_user(user.id)
    assert loaded is not None
    assert loaded.id == profile.id
    assert loaded.business_name == "Pal Mart"
    assert loaded.products == ["rice", "dal"]


def test_generated_image(db: Database) -> None:
    user = db.users.create(UserCreate(email="gen@example.com", password_hash="x"))
    image = db.generated_images.create(
        GeneratedImageCreate(
            user_id=user.id,
            original_prompt="a festive storefront",
            enhanced_prompt="a festive Kolkata storefront, 4:5",
            model="gpt-image",
            provider="openai",
            source=ImageSource.USER_PROMPT,
        )
    )
    db.commit()
    loaded = db.generated_images.get_owned(user.id, image.id)
    assert loaded is not None
    assert loaded.original_prompt == "a festive storefront"
    assert loaded.source == ImageSource.USER_PROMPT.value


def test_post_record_verified_published_only(db: Database) -> None:
    user = db.users.create(UserCreate(email="post@example.com", password_hash="x"))
    draft = db.instagram_posts.create(
        InstagramPostCreate(user_id=user.id, status=PostStatus.GENERATED, post_type=PostType.USER_PROMPT)
    )
    db.commit()
    assert db.instagram_posts.count_published(user.id) == 0
    with pytest.raises(ValueError, match="VERIFIED"):
        db.instagram_posts.create(
            InstagramPostCreate(
                user_id=user.id,
                status=PostStatus.PUBLISHED,
                instagram_media_id="media-1",
                verified=False,
            )
        )
    published = db.instagram_posts.create(
        InstagramPostCreate(
            user_id=user.id,
            status=PostStatus.PUBLISHED,
            instagram_media_id="media-1",
            permalink="https://instagram.test/p/1",
            verified=True,
        )
    )
    db.commit()
    assert published.status == PostStatus.PUBLISHED.value
    assert db.instagram_posts.count_published(user.id) == 1
    failed = db.instagram_posts.create(
        InstagramPostCreate(user_id=user.id, status=PostStatus.FAILED, error="timeout")
    )
    ambiguous = db.instagram_posts.create(
        InstagramPostCreate(user_id=user.id, status=PostStatus.AMBIGUOUS_PUBLICATION)
    )
    db.commit()
    assert db.instagram_posts.count_published(user.id) == 1
    assert failed.status == PostStatus.FAILED.value
    assert ambiguous.status == PostStatus.AMBIGUOUS_PUBLICATION.value
    assert draft.status != PostStatus.PUBLISHED.value


def test_task_and_event_record(db: Database) -> None:
    user = db.users.create(UserCreate(email="task@example.com", password_hash="x"))
    task = db.agent_tasks.upsert(
        AgentTaskCreate(
            id="task-demo-1",
            user_id=user.id,
            task_type=TaskType.INSTAGRAM_PUBLISH,
            trigger=TaskTrigger.USER,
            status="planning",
            current_step="plan",
        )
    )
    event = db.agent_events.append(
        AgentEventCreate(
            task_id=task.id,
            from_state="pending",
            to_state="planning",
            tool="Planner",
            result="started",
            observation={"ok": True},
        )
    )
    db.commit()
    loaded = db.agent_tasks.get("task-demo-1")
    assert loaded is not None
    assert loaded.user_id == user.id
    events = db.agent_events.list_for_task(task.id)
    assert events[0].id == event.id
    assert events[0].from_state == "pending"
    assert events[0].to_state == "planning"


def test_automation_settings_default_timezone(db: Database) -> None:
    user = db.users.create(UserCreate(email="auto@example.com", password_hash="x"))
    settings = db.automation_settings.upsert(user.id)
    db.commit()
    assert settings.timezone == DEFAULT_TIMEZONE
    assert settings.daily_posts_per_day == 1
    assert settings.festival_posts_per_festival == 2
    updated = db.automation_settings.upsert(
        user.id,
        AutomationSettingsWrite(daily_enabled=True, daily_post_time="09:00", timezone="Asia/Kolkata"),
    )
    db.commit()
    assert updated.daily_enabled is True
    assert updated.daily_post_time == "09:00"


def test_festival_campaign_and_independent_posts(db: Database) -> None:
    user = db.users.create(UserCreate(email="fest@example.com", password_hash="x"))
    campaign = db.festival_campaigns.create(
        FestivalCampaignCreate(
            user_id=user.id,
            festival_name="Diwali",
            festival_date=date(2026, 11, 8),
            year=2026,
        )
    )
    assert campaign.required_posts == 2
    first = db.festival_posts.create(FestivalPostCreate(campaign_id=campaign.id, sequence_number=1))
    second = db.festival_posts.create(FestivalPostCreate(campaign_id=campaign.id, sequence_number=2))
    db.commit()
    assert campaign.remaining_posts == 2
    db.festival_posts.mark_published(first.id)
    db.festival_campaigns.refresh_published_count(campaign.id)
    db.commit()
    reloaded = db.festival_campaigns.get_owned(user.id, campaign.id)
    assert reloaded is not None
    assert reloaded.published_posts == 1
    assert reloaded.remaining_posts == 1
    assert second.status != "PUBLISHED"


def test_duplicate_daily_published_protection(db: Database) -> None:
    user = db.users.create(UserCreate(email="daily@example.com", password_hash="x"))
    account = db.instagram_accounts.create(
        InstagramAccountCreate(user_id=user.id, instagram_account_id="ig-1", access_token="ig-token-secret")
    )
    db.commit()
    stored = db.session.get(InstagramAccount, account.id)
    assert stored is not None
    assert stored.access_token_encrypted != "ig-token-secret"
    assert "ig-token-secret" not in stored.access_token_encrypted

    scheduled = date(2026, 9, 21)
    db.instagram_posts.create(
        InstagramPostCreate(
            user_id=user.id,
            instagram_account_id=account.id,
            status=PostStatus.FAILED,
            post_type=PostType.DAILY_RETAIL_POST,
            scheduled_date=scheduled,
            error="temporary",
        )
    )
    db.instagram_posts.create(
        InstagramPostCreate(
            user_id=user.id,
            instagram_account_id=account.id,
            status=PostStatus.PUBLISHED,
            post_type=PostType.DAILY_RETAIL_POST,
            scheduled_date=scheduled,
            instagram_media_id="daily-media-1",
            verified=True,
        )
    )
    db.commit()
    with pytest.raises(DuplicateRecordError):
        db.instagram_posts.create(
            InstagramPostCreate(
                user_id=user.id,
                instagram_account_id=account.id,
                status=PostStatus.PUBLISHED,
                post_type=PostType.DAILY_RETAIL_POST,
                scheduled_date=scheduled,
                instagram_media_id="daily-media-2",
                verified=True,
            )
        )
        db.commit()
    db.rollback()
    later = db.instagram_posts.create(
        InstagramPostCreate(
            user_id=user.id,
            instagram_account_id=account.id,
            status=PostStatus.PUBLISHED,
            post_type=PostType.DAILY_RETAIL_POST,
            scheduled_date=date(2026, 9, 22),
            instagram_media_id="daily-media-3",
            verified=True,
        )
    )
    db.commit()
    assert later.instagram_media_id == "daily-media-3"
    job = db.scheduled_jobs.create(
        ScheduledJobCreate(user_id=user.id, instagram_account_id=account.id, job_type=JobType.DAILY_RETAIL_POST)
    )
    db.commit()
    with pytest.raises(DuplicateRecordError):
        db.scheduled_jobs.create(
            ScheduledJobCreate(user_id=user.id, instagram_account_id=account.id, job_type=JobType.DAILY_RETAIL_POST)
        )
        db.commit()
    assert job.id


def test_user_isolation(db: Database) -> None:
    alice = db.users.create(UserCreate(email="alice@example.com", password_hash="a"))
    bob = db.users.create(UserCreate(email="bob@example.com", password_hash="b"))
    alice_image = db.generated_images.create(
        GeneratedImageCreate(user_id=alice.id, original_prompt="alice prompt", source=ImageSource.USER_PROMPT)
    )
    bob_image = db.generated_images.create(
        GeneratedImageCreate(user_id=bob.id, original_prompt="bob prompt", source=ImageSource.DAILY_AUTOMATION)
    )
    db.instagram_posts.create(
        InstagramPostCreate(
            user_id=alice.id,
            generated_image_id=alice_image.id,
            status=PostStatus.PUBLISHED,
            instagram_media_id="alice-media",
            verified=True,
        )
    )
    db.commit()
    assert db.generated_images.get_owned(bob.id, alice_image.id) is None
    assert db.generated_images.get_owned(alice.id, bob_image.id) is None
    assert [item.original_prompt for item in db.generated_images.list_for_user(alice.id)] == ["alice prompt"]
    assert db.instagram_posts.count_published(bob.id) == 0
    assert db.instagram_posts.count_published(alice.id) == 1
    assert db.instagram_posts.get_owned(bob.id, db.instagram_posts.list_for_user(alice.id)[0].id) is None


@pytest.mark.asyncio
async def test_task_store_survives_new_session(engine, session: Session) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    store = TaskStore(session_factory=factory)
    state = AgentState(task_id="persist-me", image_path="photo.jpg", status=TaskStatus.COMPLETED, instagram_media_id="media-9")
    state.verified = True
    await store.save(state, persist=True)

    persist_agent_state(session, state)
    session.commit()
    cold = TaskStore(session_factory=factory)
    loaded = await cold.get("persist-me")
    assert loaded.task_id == "persist-me"
    assert loaded.instagram_media_id == "media-9"
    assert load_agent_state(session, "persist-me") is not None
