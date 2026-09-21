"""Instagram account connection and publication-status routes.

Access tokens are never included in responses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from auth.deps import require_password_ok
from db.models import User
from models.errors import AppError, ErrorCode
from models.requests import InstagramConnectBody
from services.publication import InstagramConnectRequest, PublicationGateway, strip_secrets


def create_account_router() -> APIRouter:
    router = APIRouter()

    def _gateway(request: Request) -> PublicationGateway:
        gateway = getattr(request.app.state, "publication_gateway", None)
        if gateway is None:
            raise AppError(
                ErrorCode.CONFIGURATION_ERROR,
                "The Instagram publication gateway is not configured.",
                http_status=503,
            )
        return gateway

    @router.get("/instagram/status")
    async def instagram_status(request: Request, user: User = Depends(require_password_ok)) -> JSONResponse:
        payload = await _gateway(request).account_status(user.id)
        return JSONResponse(strip_secrets(payload))

    @router.post("/instagram/connect")
    async def instagram_connect(
        request: Request,
        body: InstagramConnectBody,
        user: User = Depends(require_password_ok),
    ) -> JSONResponse:
        payload = await _gateway(request).connect_account(
            user.id,
            InstagramConnectRequest(
                instagram_account_id=body.instagram_account_id,
                access_token=body.access_token,
                token_expires_at=body.token_expires_at,
            ),
        )
        return JSONResponse(strip_secrets(payload))

    @router.post("/instagram/disconnect")
    async def instagram_disconnect(request: Request, user: User = Depends(require_password_ok)) -> JSONResponse:
        payload = await _gateway(request).disconnect_account(user.id)
        return JSONResponse(strip_secrets(payload))

    return router
