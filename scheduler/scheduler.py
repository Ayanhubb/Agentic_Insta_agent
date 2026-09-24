"""Process-wide scheduler loop. Never talks to Instagram directly."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from agent.content_agent import ContentAgent
from ai.image_generator import ImageGenerationProvider
from ai.llm_client import LLMProvider
from config import Settings
from scheduler.daily_scheduler import DailyScheduler
from scheduler.festival_scheduler import FestivalScheduler
from scheduler.integrations import resolve_canva, resolve_festival_mcp, resolve_vision
from scheduler.policies import FestivalDiversityPolicy
from services.clock import Clock
from services.metrics import pipeline_metrics
from services.publication import PublicationService
from api.task_store import TaskStore
from services.instagram_client import InstagramClient

logger = logging.getLogger(__name__)


class AutomationRunner:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        store: TaskStore,
        llm: LLMProvider,
        images: ImageGenerationProvider,
        clock: Clock,
        instagram_client: InstagramClient | None = None,
        publication_gateway: PublicationService | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._store = store
        self._llm = llm
        self._images = images
        self._clock = clock
        self._instagram_client = instagram_client
        self._publication_gateway = publication_gateway
        self._vision = resolve_vision(settings)
        self._festival_mcp = resolve_festival_mcp(settings)
        self._canva = resolve_canva(settings)

    async def tick(self, *, user_id: str | None = None) -> dict[str, Any]:
        session = self._session_factory()
        try:
            content = ContentAgent(self._llm, self._images, session=session)
            festival_content = ContentAgent(
                self._llm,
                self._images,
                session=session,
                diversity=FestivalDiversityPolicy(),
            )
            publisher = self._publication_gateway or PublicationService(
                self._settings,
                session,
                self._store,
                instagram_client=self._instagram_client,
                session_factory=self._session_factory,
            )
            daily = DailyScheduler(
                self._settings,
                session,
                content,
                publisher,
                self._clock,
                vision=self._vision,
                festival_mcp=self._festival_mcp,
                canva=self._canva,
                metrics=pipeline_metrics,
            )
            festival = FestivalScheduler(
                self._settings,
                session,
                festival_content,
                publisher,
                self._clock,
                vision=self._vision,
                festival_mcp=self._festival_mcp,
                canva=self._canva,
                metrics=pipeline_metrics,
            )
            if user_id:
                from db.models import User
                from db.repositories import AutomationRepository

                user = session.get(User, user_id)
                automation = AutomationRepository(session).get_or_create(user_id, self._settings.default_timezone)
                daily_result = []
                festival_result = []
                if user is not None:
                    daily_result = [await daily.run_user(user, automation, force=True)]
                    festival_result = await festival.run_user(user, automation, force=True)
            else:
                daily_result = await daily.run_all()
                festival_result = await festival.run_all()
            session.commit()
            return {"daily": daily_result, "festival": festival_result}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


async def scheduler_loop(runner: AutomationRunner, interval_seconds: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await runner.tick()
        except Exception:
            pipeline_metrics.increment("scheduler_tick_failed")
            logger.exception("Scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(5, interval_seconds))
        except asyncio.TimeoutError:
            continue
