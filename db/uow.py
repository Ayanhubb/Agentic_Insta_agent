"""Session-scoped repository facade for FastAPI dependencies and later agents."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from db.crypto import TokenEncryptor
from db.exceptions import DuplicateRecordError
from db.asset_repositories import (
    BrandGuidelineRepository,
    BrandProfileRepository,
    BusinessAssetRepository,
    ProductAssetRepository,
    ProductRepository,
)
from db.repositories import (
    AgentEventRepository,
    AgentTaskRepository,
    AutomationSettingsRepository,
    BusinessProfileRepository,
    DailySlotRepository,
    FestivalCampaignRepository,
    FestivalPostRepository,
    GeneratedImageRepository,
    InstagramAccountRepository,
    InstagramPostRepository,
    ScheduledJobRepository,
    SessionRepository,
    UserRepository,
    commit_or_raise,
)
from db.session import get_session_factory
from sqlalchemy.exc import IntegrityError


class Database:
    """Unit of work: one Session, all repositories, one commit."""

    def __init__(self, session: Session, encryptor: TokenEncryptor | None = None) -> None:
        self.session = session
        self.encryptor = encryptor
        self.users = UserRepository(session)
        self.instagram_accounts = InstagramAccountRepository(session, encryptor)
        self.business_profiles = BusinessProfileRepository(session)
        self.generated_images = GeneratedImageRepository(session)
        self.instagram_posts = InstagramPostRepository(session)
        self.agent_tasks = AgentTaskRepository(session)
        self.agent_events = AgentEventRepository(session)
        self.scheduled_jobs = ScheduledJobRepository(session)
        self.festival_campaigns = FestivalCampaignRepository(session)
        self.festival_posts = FestivalPostRepository(session)
        self.automation_settings = AutomationSettingsRepository(session)
        self.sessions = SessionRepository(session)
        self.daily_slots = DailySlotRepository(session)
        self.business_assets = BusinessAssetRepository(session)
        self.brand_profiles = BrandProfileRepository(session)
        self.brand_guidelines = BrandGuidelineRepository(session)
        self.products = ProductRepository(session)
        self.product_assets = ProductAssetRepository(session)
        # Aliases used by auth / scheduler / platform agents.
        self.business = self.business_profiles
        self.automation = self.automation_settings
        self.posts = self.instagram_posts
        self.tasks = self.agent_tasks
        self.festivals = self.festival_campaigns
        self.jobs = self.scheduled_jobs

    def commit(self) -> None:
        commit_or_raise(self.session)

    def rollback(self) -> None:
        self.session.rollback()

    def flush(self) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateRecordError.from_integrity(exc) from exc


def get_database(encryptor: TokenEncryptor | None = None) -> Iterator[Database]:
    """FastAPI dependency yielding a Database unit of work."""
    factory = get_session_factory()
    session = factory()
    db = Database(session, encryptor=encryptor)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        session.close()
