"""Canva connection routes. Tokens never appear in responses."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from auth.deps import require_password_ok
from backend.integrations.canva.adapter import CanvaAdapter, allowed_success_url
from backend.integrations.canva.contracts import owner_from_user_id
from db.models import User

router = APIRouter(prefix="/integrations/canva", tags=["canva"])

_CONNECTED_HTML = (
    "<!doctype html><title>Canva connected</title>"
    "<p>Canva is connected. You can close this window.</p>"
)


def _canva(request: Request) -> CanvaAdapter:
    return request.app.state.canva


def _with_query(url: str, **params: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


@router.get("/status")
async def canva_status(request: Request, user: User = Depends(require_password_ok)) -> dict:
    account = await _canva(request).status(owner_from_user_id(user.id))
    return account.public_dict()


@router.post("/connect")
def canva_connect(request: Request, user: User = Depends(require_password_ok)) -> dict:
    url = _canva(request).begin_authorization(owner_from_user_id(user.id))
    return {"authorization_url": url}


@router.get("/callback", response_model=None)
async def canva_callback(
    request: Request,
    code: str = "",
    state: str = "",
    user: User = Depends(require_password_ok),
) -> HTMLResponse | RedirectResponse:
    await _canva(request).complete_authorization(owner_from_user_id(user.id), code=code, state=state)
    success = allowed_success_url(request.app.state.settings)
    if success:
        return RedirectResponse(_with_query(success, canva="connected"), status_code=303)
    return HTMLResponse(_CONNECTED_HTML)


@router.post("/disconnect")
def canva_disconnect(request: Request, user: User = Depends(require_password_ok)) -> dict:
    _canva(request).disconnect(owner_from_user_id(user.id))
    return {"connected": False}
