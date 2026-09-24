"""Persist one daily trend report and its content opportunities."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scheduler.trend_models import TrendDailyOpportunity, TrendDailyReport


def ensure_trend_tables(session: Session) -> None:
    bind = session.get_bind()
    TrendDailyReport.__table__.create(bind, checkfirst=True)
    TrendDailyOpportunity.__table__.create(bind, checkfirst=True)


class TrendReportStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        ensure_trend_tables(session)

    def get_report(self, user_id: str, local_date: date) -> TrendDailyReport | None:
        return self._session.scalar(
            select(TrendDailyReport).where(
                TrendDailyReport.user_id == user_id,
                TrendDailyReport.local_date == local_date,
            )
        )

    def save_report(
        self,
        *,
        user_id: str,
        business_id: str,
        local_date: date,
        timezone: str,
        mode: str,
        payload: dict[str, Any],
    ) -> TrendDailyReport:
        existing = self.get_report(user_id, local_date)
        if existing is not None:
            return existing
        row = TrendDailyReport(
            user_id=user_id,
            business_id=business_id,
            local_date=local_date,
            timezone=timezone,
            mode=mode,
            payload=payload,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def save_opportunity(
        self,
        *,
        user_id: str,
        business_id: str,
        trend_id: str,
        local_date: date,
        campaign_id: str,
        title: str,
        why_now: str,
        creative_direction: str | None,
        recommended_format: str | None,
        product_id: str | None,
        festival_id: str | None,
        confidence: float | None,
        expires_at: datetime | None,
        status: str,
        payload: dict[str, Any],
    ) -> tuple[TrendDailyOpportunity, bool]:
        campaign_key = campaign_id or ""
        existing = self._session.scalar(
            select(TrendDailyOpportunity).where(
                TrendDailyOpportunity.user_id == user_id,
                TrendDailyOpportunity.business_id == business_id,
                TrendDailyOpportunity.trend_id == trend_id,
                TrendDailyOpportunity.local_date == local_date,
                TrendDailyOpportunity.campaign_id == campaign_key,
            )
        )
        if existing is not None:
            return existing, False
        row = TrendDailyOpportunity(
            user_id=user_id,
            business_id=business_id,
            trend_id=trend_id,
            local_date=local_date,
            campaign_id=campaign_key,
            title=title[:255],
            why_now=why_now,
            creative_direction=creative_direction,
            recommended_format=recommended_format,
            product_id=product_id,
            festival_id=festival_id,
            confidence=confidence,
            expires_at=expires_at,
            status=status,
            payload=payload,
        )
        nested = self._session.begin_nested()
        self._session.add(row)
        try:
            self._session.flush()
            nested.commit()
        except IntegrityError:
            nested.rollback()
            existing = self._session.scalar(
                select(TrendDailyOpportunity).where(
                    TrendDailyOpportunity.user_id == user_id,
                    TrendDailyOpportunity.business_id == business_id,
                    TrendDailyOpportunity.trend_id == trend_id,
                    TrendDailyOpportunity.local_date == local_date,
                    TrendDailyOpportunity.campaign_id == campaign_key,
                )
            )
            if existing is None:
                raise
            return existing, False
        return row, True

    def expire_opportunities(self, user_id: str, now: datetime) -> int:
        rows = list(
            self._session.scalars(
                select(TrendDailyOpportunity).where(
                    TrendDailyOpportunity.user_id == user_id,
                    TrendDailyOpportunity.status.in_(("NEW", "REVIEWED", "ACCEPTED")),
                    TrendDailyOpportunity.expires_at.is_not(None),
                )
            )
        )
        changed = 0
        for row in rows:
            if opportunity_is_expired(row.expires_at, now):
                row.status = "EXPIRED"
                changed += 1
        if changed:
            self._session.flush()
        return changed

    def list_opportunities(self, user_id: str, local_date: date) -> list[TrendDailyOpportunity]:
        return list(
            self._session.scalars(
                select(TrendDailyOpportunity)
                .where(
                    TrendDailyOpportunity.user_id == user_id,
                    TrendDailyOpportunity.local_date == local_date,
                )
                .order_by(TrendDailyOpportunity.created_at.asc())
            )
        )

    def latest_report(self, user_id: str) -> TrendDailyReport | None:
        return self._session.scalar(
            select(TrendDailyReport)
            .where(TrendDailyReport.user_id == user_id)
            .order_by(TrendDailyReport.local_date.desc())
        )


def opportunity_is_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    moment = expires_at
    current = now
    if moment.tzinfo is None and current.tzinfo is not None:
        moment = moment.replace(tzinfo=current.tzinfo)
    elif moment.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=moment.tzinfo)
    elif moment.tzinfo is not None and current.tzinfo is not None:
        current = current.astimezone(moment.tzinfo)
    return moment <= current


def opportunity_public(row: TrendDailyOpportunity) -> dict[str, Any]:
    return {
        "id": row.id,
        "business_id": row.business_id,
        "trend_id": row.trend_id,
        "local_date": row.local_date.isoformat(),
        "campaign_id": row.campaign_id or None,
        "title": row.title,
        "why_now": row.why_now,
        "creative_direction": row.creative_direction,
        "recommended_format": row.recommended_format,
        "product_id": row.product_id,
        "festival_id": row.festival_id,
        "confidence": row.confidence,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "status": row.status,
    }


def trend_dashboard_payload(session: Session, user_id: str) -> dict[str, Any] | None:
    """Dashboard notification. Missing storage returns nothing instead of an error."""

    try:
        store = TrendReportStore(session)
        report = store.latest_report(user_id)
    except Exception:
        return None
    if report is None or report.user_id != user_id:
        return None
    now = datetime.now(timezone.utc)
    opportunities = [
        opportunity_public(row)
        for row in store.list_opportunities(user_id, report.local_date)
        if row.status in {"NEW", "REVIEWED", "ACCEPTED"} and not opportunity_is_expired(row.expires_at, now)
    ]
    return {
        "report_id": report.id,
        "local_date": report.local_date.isoformat(),
        "timezone": report.timezone,
        "mode": report.mode,
        "report": report.payload,
        "content_opportunities": opportunities,
    }
