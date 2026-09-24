"""Scheduler-owned trend report and opportunity rows.

These tables are the daily job's store. They are tenant-scoped and are not an
Instagram publish record.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, new_id, timestamp_column


class TrendDailyReport(Base):
    __tablename__ = "trend_daily_reports"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date", name="uq_trend_daily_reports_user_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = timestamp_column()


class TrendDailyOpportunity(Base):
    __tablename__ = "trend_daily_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "trend_id",
            "local_date",
            "campaign_id",
            name="uq_trend_opportunity_business_trend_date_campaign",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[str] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trend_id: Mapped[str] = mapped_column(String(128), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    why_now: Mapped[str] = mapped_column(Text, nullable=False)
    creative_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    festival_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NEW")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = timestamp_column()
