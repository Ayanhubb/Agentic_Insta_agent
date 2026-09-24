"""Trend storage: migration, isolation, duplicates, expiry, snapshots, indexes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker

from db.crypto import TokenEncryptor
from db.exceptions import DuplicateRecordError, RecordNotFoundError
from db.schemas import BusinessProfileWrite, UserCreate
from db.session import create_engine_from_url, init_db
from db.trend_retention import TrendRetentionPolicy
from db.uow import Database


def _engine(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'trends.db').as_posix()}"
    engine = create_engine_from_url(url)
    init_db(engine)
    return engine


@pytest.fixture
def db(tmp_path: Path) -> Database:
    engine = _engine(tmp_path)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session: Session = factory()
    try:
        yield Database(session, encryptor=TokenEncryptor("test-token-encryption-key"))
    finally:
        session.close()


def _user(database: Database, email: str):
    user = database.users.create(UserCreate(email=email, password_hash="x"))
    profile = database.business_profiles.upsert(
        user.id,
        BusinessProfileWrite(business_name=email, business_type="retail", location="Kolkata"),
    )
    database.commit()
    return user, profile


def _source(database: Database, user_id: str, business_id: str):
    source = database.trend_sources.create(
        user_id,
        business_id=business_id,
        name="Retail notes",
        source_type="research",
        industry="retail",
        region="West Bengal",
    )
    database.commit()
    return source


def _observation(database: Database, user_id: str, source_id: str, **overrides):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    fields = {
        "source_id": source_id,
        "title": "Silk gifting before Durga Puja",
        "description": "Saved product stills are ahead of brand stills on this account.",
        "trend_type": "seasonal",
        "industry": "retail",
        "region": "West Bengal",
        "observed_at": now,
        "published_at": now,
        "valid_until": now + timedelta(days=14),
        "confidence": 0.72,
        "keywords": ["durga", "silk"],
        "evidence": "12 published IMAGE posts",
        "external_id": "silk-durga-2026",
    }
    fields.update(overrides)
    row = database.trend_observations.record(user_id, **fields)
    database.commit()
    return row


def test_migration_upgrade_and_rollback(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(cfg, "head")
    engine = create_engine_from_url(f"sqlite:///{db_path.as_posix()}")
    names = set(inspect(engine).get_table_names())
    assert {
        "users",
        "trend_sources",
        "trend_observations",
        "trend_evidence",
        "account_snapshots",
        "media_snapshots",
        "insight_snapshots",
        "trend_reports",
        "content_opportunities",
    }.issubset(names)
    indexes = {item["name"] for item in inspect(engine).get_indexes("trend_observations")}
    assert "ix_trend_observations_lookup" in indexes
    assert "ix_trend_observations_valid_until" in indexes
    assert "ix_content_opportunities_business_id" in {
        item["name"] for item in inspect(engine).get_indexes("content_opportunities")
    }

    command.downgrade(cfg, "005_trend_research")
    after_step = set(inspect(engine).get_table_names())
    assert "content_opportunities" not in after_step
    assert "account_snapshots" not in after_step
    assert "trend_observations" in after_step
    assert "users" in after_step

    command.downgrade(cfg, "004_generated_image_qa")
    rolled_back = set(inspect(engine).get_table_names())
    assert "trend_observations" not in rolled_back
    assert "trend_reports" not in rolled_back
    assert "users" in rolled_back
    engine.dispose()


def test_tenant_isolation(db: Database) -> None:
    alice, alice_business = _user(db, "alice@example.com")
    bob, bob_business = _user(db, "bob@example.com")
    source = _source(db, alice.id, alice_business.id)
    observation = _observation(db, alice.id, source.id)
    opportunity = db.content_opportunities.create(
        alice.id,
        business_id=alice_business.id,
        trend_id=observation.id,
        title="Silk tray before Durga Puja",
        why_now="The stored festival window is open and saves are higher on product stills.",
        creative_direction="Show the owned silk product.",
        recommended_format="IMAGE",
        confidence=0.64,
        expires_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    snapshot = db.account_snapshots.create(
        alice.id,
        business_id=alice_business.id,
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        followers_count=1200,
    )
    db.commit()

    assert db.trend_observations.get_owned(bob.id, observation.id) is None
    assert db.content_opportunities.get_owned(bob.id, opportunity.id) is None
    assert db.account_snapshots.get_owned(bob.id, snapshot.id) is None
    assert db.trend_sources.list_for_user(bob.id) == []
    assert db.content_opportunities.list_for_user(bob.id) == []
    with pytest.raises(RecordNotFoundError):
        db.content_opportunities.create(
            bob.id,
            business_id=alice_business.id,
            trend_id=observation.id,
            title="Copied",
            why_now="no",
            creative_direction="no",
        )
    bob_source = _source(db, bob.id, bob_business.id)
    copied = _observation(db, bob.id, bob_source.id, external_id="silk-durga-2026")
    assert copied.user_id == bob.id
    assert copied.id != observation.id


def test_duplicate_observations(db: Database) -> None:
    user, business = _user(db, "dup@example.com")
    source = _source(db, user.id, business.id)
    _observation(db, user.id, source.id)
    with pytest.raises(DuplicateRecordError) as caught:
        _observation(db, user.id, source.id)
    assert caught.value.entity == "trend_observations"
    assert len(db.trend_observations.list_for_user(user.id, limit=10)[0]) == 1


def test_expired_trends_stay_stored(db: Database) -> None:
    user, business = _user(db, "expire@example.com")
    source = _source(db, user.id, business.id)
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    observation = _observation(
        db,
        user.id,
        source.id,
        valid_until=now - timedelta(days=1),
        external_id="expired-trend",
    )
    open_opportunity = db.content_opportunities.create(
        user.id,
        business_id=business.id,
        trend_id=observation.id,
        title="Catch the window",
        why_now="The date has passed.",
        creative_direction="Do not invent a new date.",
        expires_at=now - timedelta(hours=1),
    )
    db.commit()
    accepted = db.content_opportunities.create(
        user.id,
        business_id=business.id,
        trend_id=observation.id,
        title="Keep the accepted idea",
        why_now="Already chosen.",
        creative_direction="Leave this status alone.",
        expires_at=now - timedelta(hours=1),
    )
    db.content_opportunities.set_status(user.id, accepted.id, "ACCEPTED")
    db.commit()

    result = db.trend_retention.apply(now, user_id=user.id)
    db.commit()
    db.session.refresh(observation)
    db.session.refresh(open_opportunity)
    db.session.refresh(accepted)
    assert result["expired_observations"] == 1
    assert result["expired_opportunities"] == 1
    assert observation.status == "EXPIRED"
    assert open_opportunity.status == "EXPIRED"
    assert accepted.status == "ACCEPTED"
    assert db.trend_observations.get_owned(user.id, observation.id) is not None


def test_historical_snapshots_are_not_overwritten(db: Database) -> None:
    user, business = _user(db, "history@example.com")
    first = db.account_snapshots.create(
        user.id,
        business_id=business.id,
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        followers_count=100,
        metrics={"reach": 10},
    )
    second = db.account_snapshots.create(
        user.id,
        business_id=business.id,
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        followers_count=180,
        metrics={"reach": 40},
    )
    db.media_snapshots.create(
        user.id,
        business_id=business.id,
        instagram_media_id="media-1",
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        like_count=4,
    )
    db.media_snapshots.create(
        user.id,
        business_id=business.id,
        instagram_media_id="media-1",
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        like_count=11,
    )
    db.insight_snapshots.create(
        user.id,
        business_id=business.id,
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        insight_type="performance_comparison",
        summary="Saves rose between the August and September captures.",
        payload={"august_followers": 100, "september_followers": 180},
    )
    earlier = datetime(2026, 8, 1, tzinfo=timezone.utc)
    later = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db.trend_reports.create(
        user.id,
        business_id=business.id,
        title="August",
        summary="First report",
        observed_at=earlier,
        payload={"followers": 100},
    )
    db.trend_reports.create(
        user.id,
        business_id=business.id,
        title="September",
        summary="Second report",
        observed_at=later,
        payload={"followers": 180},
    )
    db.commit()

    history = db.account_snapshots.list_for_user(user.id)
    assert [item.followers_count for item in history] == [100, 180]
    assert history[0].id == first.id
    assert history[1].id == second.id
    media = db.media_snapshots.list_for_media(user.id, "media-1")
    assert [item.like_count for item in media] == [4, 11]
    assert len(db.insight_snapshots.list_for_user(user.id)) == 1
    assert db.trend_reports.latest_for_user(user.id).title == "September"


def test_retention_keeps_performance_history(db: Database) -> None:
    user, business = _user(db, "retain@example.com")
    source = _source(db, user.id, business.id)
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    old = _observation(
        db,
        user.id,
        source.id,
        title="Old unused trend",
        external_id="old-unused",
        valid_until=now - timedelta(days=400),
        observed_at=now - timedelta(days=400),
    )
    kept = _observation(
        db,
        user.id,
        source.id,
        title="Referenced trend",
        external_id="referenced",
        valid_until=now - timedelta(days=400),
        observed_at=now - timedelta(days=400),
    )
    db.content_opportunities.create(
        user.id,
        business_id=business.id,
        trend_id=kept.id,
        title="Still cited",
        why_now="An opportunity still points at this trend.",
        creative_direction="Keep the observation.",
        status="EXPIRED",
        expires_at=now - timedelta(days=10),
    )
    old_snapshot = db.account_snapshots.create(
        user.id,
        business_id=business.id,
        observed_at=now - timedelta(days=400),
        followers_count=50,
    )
    recent_snapshot = db.account_snapshots.create(
        user.id,
        business_id=business.id,
        observed_at=now - timedelta(days=2),
        followers_count=80,
    )
    db.commit()

    default = TrendRetentionPolicy()
    first = db.trend_retention.apply(now, default, user_id=user.id)
    db.commit()
    assert db.trend_observations.get_owned(user.id, old.id) is None
    assert db.trend_observations.get_owned(user.id, kept.id) is not None
    assert db.account_snapshots.get_owned(user.id, old_snapshot.id) is not None
    assert first["purged_performance_snapshots"] == 0

    opted_in = TrendRetentionPolicy(
        observation_retention_days=180,
        performance_retention_days=30,
        purge_performance_history=True,
    )
    second = db.trend_retention.apply(now, opted_in, user_id=user.id)
    db.commit()
    assert second["purged_performance_snapshots"] == 1
    assert db.account_snapshots.get_owned(user.id, old_snapshot.id) is None
    assert db.account_snapshots.get_owned(user.id, recent_snapshot.id) is not None


def test_indexed_trend_query(db: Database) -> None:
    user, business = _user(db, "query@example.com")
    source = _source(db, user.id, business.id)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(300):
        db.trend_observations.record(
            user.id,
            source_id=source.id,
            title=f"Noise {index}",
            description="Unrelated",
            trend_type="evergreen" if index else "seasonal",
            industry="general" if index else "retail",
            region="IN" if index else "West Bengal",
            observed_at=start + timedelta(days=index),
            valid_until=start + timedelta(days=index + 7),
            confidence=0.4,
            external_id=f"row-{index}",
            status="ACTIVE",
        )
    db.commit()

    found = db.trend_observations.search(
        industry="retail",
        region="West Bengal",
        trend_type="seasonal",
        status="ACTIVE",
    )
    assert [item.external_id for item in found] == ["row-0"]

    plan = db.session.execute(
        text(
            "EXPLAIN QUERY PLAN SELECT id FROM trend_observations "
            "WHERE industry = :industry AND region = :region AND trend_type = :trend_type "
            "AND status = :status ORDER BY observed_at"
        ),
        {"industry": "retail", "region": "West Bengal", "trend_type": "seasonal", "status": "ACTIVE"},
    ).all()
    detail = " ".join(str(row) for row in plan)
    assert "ix_trend_observations_lookup" in detail
