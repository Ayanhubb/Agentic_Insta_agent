"""Expire stale trend rows without deleting performance history by default."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from db.models import (
    AccountSnapshot,
    ContentOpportunity,
    InsightSnapshot,
    MediaSnapshot,
    TrendObservation,
)


@dataclass(frozen=True)
class TrendRetentionPolicy:
    """How long unused expired observations may be removed.

    ``performance_retention_days`` is ignored unless ``purge_performance_history``
    is set. Account, media, and insight snapshots stay when that flag is off.
    """

    observation_retention_days: int = 180
    performance_retention_days: int | None = None
    purge_performance_history: bool = False

    @classmethod
    def from_env(cls) -> TrendRetentionPolicy:
        raw_days = os.getenv("TREND_OBSERVATION_RETENTION_DAYS", "180").strip() or "180"
        raw_performance = os.getenv("TREND_PERFORMANCE_RETENTION_DAYS", "").strip()
        purge = os.getenv("TREND_PURGE_PERFORMANCE_HISTORY", "").strip().lower() in {"1", "true", "yes"}
        return cls(
            observation_retention_days=int(raw_days),
            performance_retention_days=int(raw_performance) if raw_performance else None,
            purge_performance_history=purge,
        )


class TrendRetention:
    def __init__(self, session: Session) -> None:
        self.session = session

    def apply(
        self,
        now: datetime,
        policy: TrendRetentionPolicy | None = None,
        *,
        user_id: str | None = None,
    ) -> dict[str, int]:
        rules = policy or TrendRetentionPolicy()
        expired_observations = self.expire_observations(now, user_id=user_id)
        expired_opportunities = self.expire_opportunities(now, user_id=user_id)
        purged_observations = self.purge_observations(now, rules, user_id=user_id)
        purged_performance = self.purge_performance(now, rules, user_id=user_id)
        return {
            "expired_observations": expired_observations,
            "expired_opportunities": expired_opportunities,
            "purged_observations": purged_observations,
            "purged_performance_snapshots": purged_performance,
        }

    def expire_observations(self, now: datetime, *, user_id: str | None = None) -> int:
        stmt = update(TrendObservation).where(
            TrendObservation.status == "ACTIVE",
            TrendObservation.valid_until.is_not(None),
            TrendObservation.valid_until < now,
        )
        if user_id is not None:
            stmt = stmt.where(TrendObservation.user_id == user_id)
        result = self.session.execute(stmt.values(status="EXPIRED"))
        self.session.flush()
        return int(result.rowcount or 0)

    def expire_opportunities(self, now: datetime, *, user_id: str | None = None) -> int:
        stmt = update(ContentOpportunity).where(
            ContentOpportunity.status.in_(("NEW", "REVIEWED")),
            ContentOpportunity.expires_at.is_not(None),
            ContentOpportunity.expires_at < now,
        )
        if user_id is not None:
            stmt = stmt.where(ContentOpportunity.user_id == user_id)
        result = self.session.execute(stmt.values(status="EXPIRED"))
        self.session.flush()
        return int(result.rowcount or 0)

    def purge_observations(self, now: datetime, policy: TrendRetentionPolicy, *, user_id: str | None = None) -> int:
        cutoff = now - timedelta(days=max(0, int(policy.observation_retention_days)))
        referenced = select(ContentOpportunity.trend_id)
        if user_id is not None:
            referenced = referenced.where(ContentOpportunity.user_id == user_id)
        stmt = delete(TrendObservation).where(
            TrendObservation.status == "EXPIRED",
            TrendObservation.valid_until.is_not(None),
            TrendObservation.valid_until < cutoff,
            TrendObservation.id.not_in(referenced),
        )
        if user_id is not None:
            stmt = stmt.where(TrendObservation.user_id == user_id)
        result = self.session.execute(stmt)
        self.session.flush()
        return int(result.rowcount or 0)

    def purge_performance(self, now: datetime, policy: TrendRetentionPolicy, *, user_id: str | None = None) -> int:
        if not policy.purge_performance_history or policy.performance_retention_days is None:
            return 0
        cutoff = now - timedelta(days=max(0, int(policy.performance_retention_days)))
        removed = 0
        for model in (AccountSnapshot, MediaSnapshot, InsightSnapshot):
            stmt = delete(model).where(model.observed_at < cutoff)
            if user_id is not None:
                stmt = stmt.where(model.user_id == user_id)
            result = self.session.execute(stmt)
            removed += int(result.rowcount or 0)
        self.session.flush()
        return removed
