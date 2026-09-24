"""Trend research rows. Writers live outside MCP; these queries are the read path."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from db.exceptions import DuplicateRecordError, RecordNotFoundError
from db.models import (
    AccountSnapshot,
    BusinessProfile,
    ContentOpportunity,
    FestivalCampaign,
    InsightSnapshot,
    MediaSnapshot,
    Product,
    TrendEvidence,
    TrendObservation,
    TrendReport,
    TrendSource,
)
from db.repositories import flush_or_raise


class TrendObservationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> TrendObservation:
        if not fields.get("description"):
            fields["description"] = fields.get("summary") or ""
        if not fields.get("summary"):
            fields["summary"] = fields.get("description") or fields.get("title") or ""
        if fields.get("keywords") is None:
            fields["keywords"] = []
        external_id = fields.get("external_id")
        if external_id is not None:
            fields["external_id"] = str(external_id).strip() or None
        row = TrendObservation(user_id=user_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def record(
        self,
        user_id: str,
        *,
        source_id: str,
        title: str,
        description: str,
        trend_type: str,
        industry: str,
        region: str,
        observed_at: datetime,
        evidence: str = "",
        keywords: list[str] | None = None,
        published_at: datetime | None = None,
        valid_until: datetime | None = None,
        confidence: float | Decimal | None = None,
        external_id: str | None = None,
        status: str = "ACTIVE",
        observation_id: str | None = None,
    ) -> TrendObservation:
        source = self.session.get(TrendSource, source_id)
        if source is None or source.user_id != user_id:
            raise RecordNotFoundError("trend_sources", source_id)
        key = observation_external_id(
            title=title,
            observed_at=observed_at,
            published_at=published_at,
            external_id=external_id,
        )
        existing = self.session.scalar(
            select(TrendObservation).where(
                TrendObservation.user_id == user_id,
                TrendObservation.source_id == source_id,
                TrendObservation.external_id == key,
            )
        )
        if existing is not None:
            raise DuplicateRecordError(
                "This trend observation was already stored for the source.",
                entity="trend_observations",
                constraint="uq_trend_observations_source_external",
            )
        fields: dict[str, Any] = {
            "source_id": source_id,
            "title": title,
            "summary": description,
            "description": description,
            "trend_type": trend_type,
            "industry": industry,
            "region": region,
            "evidence": evidence,
            "keywords": list(keywords or []),
            "observed_at": observed_at,
            "published_at": published_at,
            "valid_until": valid_until,
            "confidence": _confidence(confidence),
            "external_id": key,
            "status": status,
            "source": "research",
        }
        if observation_id:
            fields["id"] = observation_id
        return self.create(user_id, **fields)

    def search(
        self,
        *,
        industry: str,
        region: str,
        trend_type: str,
        status: str,
        limit: int = 50,
    ) -> list[TrendObservation]:
        stmt = (
            select(TrendObservation)
            .where(
                TrendObservation.industry == industry,
                TrendObservation.region == region,
                TrendObservation.trend_type == trend_type,
                TrendObservation.status == status,
            )
            .order_by(TrendObservation.observed_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(stmt))

    def find_by_external_id(self, user_id: str, external_id: str) -> TrendObservation | None:
        return self.session.scalar(
            select(TrendObservation).where(
                TrendObservation.user_id == user_id,
                TrendObservation.external_id == external_id,
            )
        )

    def get_owned(self, user_id: str, observation_id: str) -> TrendObservation | None:
        return self.session.scalar(
            select(TrendObservation).where(
                TrendObservation.id == observation_id,
                TrendObservation.user_id == user_id,
            )
        )

    def list_for_user(
        self,
        user_id: str,
        *,
        industry: str | None = None,
        region: str | None = None,
        festival: str | None = None,
        content_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        fresh_after: datetime | None = None,
        current_at: datetime | None = None,
        legacy_after: datetime | None = None,
        limit: int,
    ) -> tuple[list[TrendObservation], bool]:
        stmt = select(TrendObservation).where(TrendObservation.user_id == user_id)
        stmt = _filtered(stmt, industry, region, festival, content_type, start, end, fresh_after)
        if current_at is not None:
            stmt = stmt.where(_current_clause(current_at, legacy_after or current_at))
        stmt = stmt.order_by(TrendObservation.observed_at.desc()).limit(limit + 1)
        rows = list(self.session.scalars(stmt))
        truncated = len(rows) > limit
        return rows[:limit], truncated

    def count_stale(
        self,
        user_id: str,
        *,
        industry: str | None = None,
        region: str | None = None,
        festival: str | None = None,
        content_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        fresh_after: datetime,
    ) -> int:
        stmt = select(func.count()).select_from(TrendObservation).where(
            TrendObservation.user_id == user_id,
            TrendObservation.observed_at < fresh_after,
        )
        stmt = _filtered(stmt, industry, region, festival, content_type, start, end, fresh_after=None)
        return int(self.session.scalar(stmt) or 0)

    def count_not_current(
        self,
        user_id: str,
        *,
        industry: str | None = None,
        region: str | None = None,
        festival: str | None = None,
        content_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        current_at: datetime,
        legacy_after: datetime,
    ) -> int:
        stmt = select(func.count()).select_from(TrendObservation).where(TrendObservation.user_id == user_id)
        stmt = _filtered(stmt, industry, region, festival, content_type, start, end, fresh_after=None)
        stmt = stmt.where(not_(_current_clause(current_at, legacy_after)))
        return int(self.session.scalar(stmt) or 0)


class TrendReportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> TrendReport:
        row = TrendReport(user_id=user_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def latest_for_user(self, user_id: str) -> TrendReport | None:
        return self.session.scalar(
            select(TrendReport)
            .where(TrendReport.user_id == user_id)
            .order_by(TrendReport.observed_at.desc())
            .limit(1)
        )


class TrendSourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> TrendSource:
        _require_business(self.session, user_id, fields.get("business_id"))
        row = TrendSource(user_id=user_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def find_by_url(self, user_id: str, url: str) -> TrendSource | None:
        return self.session.scalar(
            select(TrendSource).where(TrendSource.user_id == user_id, TrendSource.url == url)
        )

    def get_owned(self, user_id: str, source_id: str) -> TrendSource | None:
        return self.session.scalar(
            select(TrendSource).where(TrendSource.id == source_id, TrendSource.user_id == user_id)
        )

    def list_for_user(self, user_id: str) -> list[TrendSource]:
        return list(
            self.session.scalars(
                select(TrendSource).where(TrendSource.user_id == user_id).order_by(TrendSource.created_at.asc())
            )
        )


class TrendEvidenceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, observation_id: str, **fields: Any) -> TrendEvidence:
        observation = self.session.get(TrendObservation, observation_id)
        if observation is None or observation.user_id != user_id:
            raise RecordNotFoundError("trend_observations", observation_id)
        row = TrendEvidence(user_id=user_id, observation_id=observation_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def get_owned(self, user_id: str, evidence_id: str) -> TrendEvidence | None:
        return self.session.scalar(
            select(TrendEvidence).where(TrendEvidence.id == evidence_id, TrendEvidence.user_id == user_id)
        )

    def list_for_observation(self, user_id: str, observation_id: str) -> list[TrendEvidence]:
        return list(
            self.session.scalars(
                select(TrendEvidence)
                .where(TrendEvidence.user_id == user_id, TrendEvidence.observation_id == observation_id)
                .order_by(TrendEvidence.observed_at.asc())
            )
        )


class AccountSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> AccountSnapshot:
        _require_business(self.session, user_id, fields.get("business_id"))
        row = AccountSnapshot(user_id=user_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def get_owned(self, user_id: str, snapshot_id: str) -> AccountSnapshot | None:
        return self.session.scalar(
            select(AccountSnapshot).where(AccountSnapshot.id == snapshot_id, AccountSnapshot.user_id == user_id)
        )

    def list_for_user(self, user_id: str) -> list[AccountSnapshot]:
        return list(
            self.session.scalars(
                select(AccountSnapshot)
                .where(AccountSnapshot.user_id == user_id)
                .order_by(AccountSnapshot.observed_at.asc())
            )
        )


class MediaSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> MediaSnapshot:
        _require_business(self.session, user_id, fields.get("business_id"))
        row = MediaSnapshot(user_id=user_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[MediaSnapshot]:
        return list(
            self.session.scalars(
                select(MediaSnapshot)
                .where(MediaSnapshot.user_id == user_id)
                .order_by(MediaSnapshot.observed_at.desc())
                .limit(limit)
            )
        )

    def list_for_media(self, user_id: str, instagram_media_id: str) -> list[MediaSnapshot]:
        return list(
            self.session.scalars(
                select(MediaSnapshot)
                .where(MediaSnapshot.user_id == user_id, MediaSnapshot.instagram_media_id == instagram_media_id)
                .order_by(MediaSnapshot.observed_at.asc())
            )
        )


class InsightSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> InsightSnapshot:
        _require_business(self.session, user_id, fields.get("business_id"))
        row = InsightSnapshot(user_id=user_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def list_for_user(self, user_id: str, insight_type: str | None = None) -> list[InsightSnapshot]:
        stmt = select(InsightSnapshot).where(InsightSnapshot.user_id == user_id)
        if insight_type:
            stmt = stmt.where(InsightSnapshot.insight_type == insight_type)
        stmt = stmt.order_by(InsightSnapshot.observed_at.asc())
        return list(self.session.scalars(stmt))


class ContentOpportunityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, **fields: Any) -> ContentOpportunity:
        business_id = str(fields.get("business_id") or "")
        _require_business(self.session, user_id, business_id, required=True)
        trend = self.session.get(TrendObservation, fields.get("trend_id"))
        if trend is None or trend.user_id != user_id:
            raise RecordNotFoundError("trend_observations", str(fields.get("trend_id") or ""))
        product_id = fields.get("product_id")
        if product_id:
            product = self.session.get(Product, product_id)
            if product is None or product.user_id != user_id:
                raise RecordNotFoundError("products", str(product_id))
        festival_id = fields.get("festival_id")
        if festival_id:
            campaign = self.session.get(FestivalCampaign, festival_id)
            if campaign is None or campaign.user_id != user_id:
                raise RecordNotFoundError("festival_campaigns", str(festival_id))
        if fields.get("confidence") is not None:
            fields["confidence"] = _confidence(fields["confidence"])
        row = ContentOpportunity(user_id=user_id, **fields)
        self.session.add(row)
        flush_or_raise(self.session)
        return row

    def get_owned(self, user_id: str, opportunity_id: str) -> ContentOpportunity | None:
        return self.session.scalar(
            select(ContentOpportunity).where(
                ContentOpportunity.id == opportunity_id,
                ContentOpportunity.user_id == user_id,
            )
        )

    def list_for_user(self, user_id: str) -> list[ContentOpportunity]:
        return list(
            self.session.scalars(
                select(ContentOpportunity)
                .where(ContentOpportunity.user_id == user_id)
                .order_by(ContentOpportunity.created_at.asc())
            )
        )

    def set_status(self, user_id: str, opportunity_id: str, status: str) -> ContentOpportunity | None:
        row = self.get_owned(user_id, opportunity_id)
        if row is None:
            return None
        row.status = status
        flush_or_raise(self.session)
        return row


def observation_external_id(
    *,
    title: str,
    observed_at: datetime,
    published_at: datetime | None = None,
    external_id: str | None = None,
) -> str:
    if external_id and str(external_id).strip():
        return str(external_id).strip()
    stamp = _as_utc(published_at or observed_at).strftime("%Y-%m-%dT%H:%M:%S%z")
    digest = hashlib.sha256(f"{title.strip().casefold()}|{stamp}".encode()).hexdigest()
    return digest


def _confidence(value: float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    number = Decimal(str(value))
    if number < 0 or number > 1:
        raise ValueError("confidence must be between 0 and 1")
    return number


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_business(session: Session, user_id: str, business_id: str | None, *, required: bool = False) -> None:
    if not business_id:
        if required:
            raise RecordNotFoundError("business_profiles", "")
        return
    profile = session.get(BusinessProfile, business_id)
    if profile is None or profile.user_id != user_id:
        raise RecordNotFoundError("business_profiles", str(business_id))


def _current_clause(current_at: datetime, legacy_after: datetime):
    known = and_(TrendObservation.valid_until.is_not(None), TrendObservation.valid_until > current_at)
    legacy = and_(TrendObservation.valid_until.is_(None), TrendObservation.observed_at >= legacy_after)
    return and_(TrendObservation.status == "ACTIVE", or_(known, legacy))


def _filtered(stmt, industry, region, festival, content_type, start, end, fresh_after):
    if industry:
        stmt = stmt.where(TrendObservation.industry == industry)
    if region:
        stmt = stmt.where(TrendObservation.region == region)
    if festival:
        stmt = stmt.where(func.lower(TrendObservation.festival) == festival.casefold())
    if content_type:
        stmt = stmt.where(TrendObservation.content_type == content_type)
    if start is not None:
        stmt = stmt.where(TrendObservation.observed_at >= start)
    if end is not None:
        stmt = stmt.where(TrendObservation.observed_at < end)
    if fresh_after is not None:
        stmt = stmt.where(TrendObservation.observed_at >= fresh_after)
    return stmt
