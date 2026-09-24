"""Owner-scoped Instagram account intelligence. Publishing is not involved."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from auth.deps import get_db, require_password_ok
from db.models import User
from services.instagram_intelligence import AccountIntelligenceService

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


def _service(request: Request, session: Session) -> AccountIntelligenceService:
    return AccountIntelligenceService(
        request.app.state.settings,
        session,
        reader=getattr(request.app.state, "intelligence_reader", None),
    )


@router.get("/account")
async def account_intelligence(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await _service(request, db).account(user.id)


@router.get("/account/posts")
async def account_posts(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await _service(request, db).posts(user.id)


@router.get("/account/insights")
async def account_insights(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await _service(request, db).insights(user.id)


@router.get("/account/top-content")
async def account_top_content(
    request: Request,
    user: User = Depends(require_password_ok),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await _service(request, db).top_content(user.id)
