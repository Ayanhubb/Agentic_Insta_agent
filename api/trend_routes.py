"""Trend intelligence routes. React calls these endpoints; they never call Meta.

Reads and writes go through the SQL trend tables on the request session.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from auth.deps import get_db, require_password_ok
from db.models import InstagramPost, User
from db.repositories import PostRepository
from models.errors import AppError, ErrorCode
from services.trend_store import TrendStore, is_expired, strip_secrets

router = APIRouter(prefix="/trends", tags=["trends"])


def _store(db: Session) -> TrendStore:
    return TrendStore(db)


def _filters(industry: str | None, region: str | None) -> tuple[str | None, str | None]:
    industry_value = industry.strip() if industry else None
    region_value = region.strip() if region else None
    return industry_value or None, region_value or None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _account_payload(posts: list[InstagramPost], overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    published = [post for post in posts if post.published_at is not None]
    recent_week = [
        post for post in published if _aware(post.published_at) >= now - timedelta(days=7)
    ]
    prior_week = [
        post
        for post in published
        if now - timedelta(days=14) <= _aware(post.published_at) < now - timedelta(days=7)
    ]
    mix = Counter(post.post_type or "unknown" for post in posts)
    recent = []
    for post in posts[:8]:
        recent.append(
            {
                "id": post.id,
                "status": post.status,
                "post_type": post.post_type,
                "created_at": post.created_at.isoformat() if post.created_at else None,
                "published_at": post.published_at.isoformat() if post.published_at else None,
                "has_error": bool(post.error),
            }
        )
    change = len(recent_week) - len(prior_week)
    payload = {
        "kind": "observed",
        "recent_posts": recent,
        "posting_frequency": {
            "kind": "observed",
            "published_last_7_days": len(recent_week),
            "published_prior_7_days": len(prior_week),
            "posts_per_week": len(recent_week),
        },
        "engagement": {
            "kind": "observed",
            "available": False,
            "metrics": {},
            "note": "Engagement metrics are not stored for these posts.",
        },
        "top_performing": {
            "kind": "observed",
            "available": False,
            "items": [],
            "note": "Engagement metrics are not stored, so top-performing content cannot be ranked.",
        },
        "content_mix": {
            "kind": "observed",
            "counts": dict(mix),
        },
        "performance_changes": {
            "kind": "observed",
            "published_delta": change,
            "note": (
                f"Published posts moved by {change:+d} compared with the previous 7 days."
                if published
                else "Not enough published posts to describe a performance change."
            ),
        },
    }
    overlay = overlay or {}
    metrics = overlay.get("engagement_metrics")
    if metrics:
        payload["engagement"] = {
            "kind": "observed",
            "available": True,
            "metrics": metrics,
            "note": "Metrics come from the latest stored account snapshot.",
        }
    top_items = overlay.get("top_items")
    if top_items:
        payload["top_performing"] = {
            "kind": "observed",
            "available": True,
            "items": top_items,
            "note": "Ranked from stored media snapshots.",
        }
    if overlay.get("insight_summary"):
        payload["performance_changes"]["note"] = overlay["insight_summary"]
    return strip_secrets(payload)


@router.get("/account")
def account_performance(
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    store = _store(db)
    posts = PostRepository(db).list_for_user(user.id)
    return {"account": _account_payload(posts, store.account_overlay(user.id))}


@router.get("/research")
def research(
    industry: str | None = None,
    region: str | None = None,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    industry_value, region_value = _filters(industry, region)
    trends = _store(db).list_trends(user.id, industry=industry_value, region=region_value)
    return {
        "trends": trends,
        "filters": {"industry": industry_value or "", "region": region_value or ""},
    }


@router.get("/opportunities")
def opportunities(
    industry: str | None = None,
    region: str | None = None,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    industry_value, region_value = _filters(industry, region)
    rows = _store(db).list_opportunities(user.id, industry=industry_value, region=region_value)
    return {
        "opportunities": rows,
        "filters": {"industry": industry_value or "", "region": region_value or ""},
    }


@router.get("/reports")
def daily_report(
    industry: str | None = None,
    region: str | None = None,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    industry_value, region_value = _filters(industry, region)
    store = _store(db)
    account = _account_payload(PostRepository(db).list_for_user(user.id), store.account_overlay(user.id))
    trends = store.list_trends(user.id, industry=industry_value, region=region_value)
    current = [item for item in trends if not item.get("expired")]
    rows = store.list_opportunities(user.id, industry=industry_value, region=region_value)
    active = [item for item in rows if not item.get("expired")]
    festivals = [item for item in active if item.get("festival")]
    products = [item for item in active if item.get("product")]
    queue = [item for item in rows if item.get("status") == "saved"]
    trend_text = (
        f"{len(current)} current research signal(s) are stored."
        if current
        else "No research signals are stored for this account."
    )
    note = store.report_note(user.id, industry_value, region_value)
    if note:
        trend_text = f"{trend_text} {note}"
    report = {
        "account_summary": {
            "kind": "observed",
            "text": (
                f"{len(account['recent_posts'])} recent post(s). "
                f"{account['posting_frequency']['published_last_7_days']} published in the last 7 days."
            ),
        },
        "trend_summary": {"kind": "discovered", "text": trend_text},
        "festival_opportunities": festivals,
        "product_opportunities": products,
        "content_queue": [
            {
                "id": item["id"],
                "title": item.get("title") or item.get("creative_direction") or "Saved recommendation",
                "kind": "recommended",
                "status": item.get("status"),
                "disclaimer": item.get("disclaimer"),
            }
            for item in queue
        ],
    }
    return strip_secrets({"report": report})


def _owned(db: Session, user: User, opportunity_id: str) -> TrendStore:
    store = _store(db)
    if store.opportunity_for(user.id, opportunity_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "Content opportunity not found.")
    return store


@router.post("/opportunities/{opportunity_id}/dismiss")
def dismiss_opportunity(
    opportunity_id: str,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    store = _owned(db, user, opportunity_id)
    updated = store.set_status(user.id, opportunity_id, "dismissed")
    assert updated is not None
    return {"opportunity": store.public_opportunity(updated)}


@router.post("/opportunities/{opportunity_id}/save")
def save_opportunity(
    opportunity_id: str,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    store = _owned(db, user, opportunity_id)
    updated = store.set_status(user.id, opportunity_id, "saved")
    assert updated is not None
    return {"opportunity": store.public_opportunity(updated)}


@router.get("/opportunities/{opportunity_id}/evidence")
def opportunity_evidence(
    opportunity_id: str,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    store = _owned(db, user, opportunity_id)
    item = store.opportunity_for(user.id, opportunity_id)
    assert item is not None
    public = store.public_opportunity(item)
    return {
        "opportunity_id": public["id"],
        "evidence": public["evidence"],
        "note": "Evidence is what was recorded. The content recommendation is separate.",
    }


@router.post("/opportunities/{opportunity_id}/create-content")
def create_content(
    opportunity_id: str,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Hand the recommendation to the existing generator. This does not publish."""
    store = _owned(db, user, opportunity_id)
    item = store.opportunity_for(user.id, opportunity_id)
    assert item is not None
    if item.get("force_expired") or is_expired(item.get("expires_at")):
        raise AppError(ErrorCode.NOT_FOUND, "This content opportunity has expired.")
    public = store.public_opportunity(item)
    return strip_secrets(
        {
            "opportunity_id": public["id"],
            "path": "/generate",
            "prompt": public.get("creative_direction") or "",
            "festival": public.get("festival") or "",
            "product": public.get("product") or "",
            "kind": "recommended",
            "disclaimer": public["disclaimer"],
        }
    )
