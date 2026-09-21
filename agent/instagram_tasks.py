"""The only supported entry point for LLM and scheduler publication.

Neither the Content Agent nor the Scheduler may import Instagram Graph
clients or call create/publish tools. They enqueue a task here. The
Instagram Agent is the only publication authority.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from models.errors import AppError, ErrorCode

if TYPE_CHECKING:
    from models.state import AgentState
    from services.publication import InstagramPublicationRequest, PublicationGateway

_gateway: PublicationGateway | None = None


def bind_publication_gateway(gateway: PublicationGateway | None) -> None:
    global _gateway
    _gateway = gateway


def get_publication_gateway() -> PublicationGateway:
    if _gateway is None:
        raise AppError(
            ErrorCode.CONFIGURATION_ERROR,
            "The Instagram publication gateway is not bound.",
            http_status=503,
        )
    return _gateway


async def enqueue_instagram_publication(
    request: InstagramPublicationRequest | dict[str, Any] | None = None,
    **kwargs: Any,
) -> AgentState:
    """Create and run an Instagram Agent task. Never calls Graph APIs itself."""

    from services.publication import InstagramPublicationRequest as RequestModel

    if request is None:
        payload = RequestModel(**kwargs)
    elif isinstance(request, RequestModel):
        payload = request
    else:
        payload = RequestModel.model_validate(request)
    return await get_publication_gateway().enqueue_publication(payload)


async def enqueue_from_handoff(handoff: Any, **overrides: Any) -> AgentState | None:
    """Turn a Content Agent InstagramHandoff into an Instagram Agent task."""

    ready = bool(getattr(handoff, "ready", False) if not isinstance(handoff, dict) else handoff.get("ready"))
    if not ready:
        return None
    data = handoff.model_dump() if hasattr(handoff, "model_dump") else dict(handoff)
    image_path = overrides.get("image_path") or data.get("image_path")
    if not image_path:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "An approved generated image path is required before creating an Instagram Agent task.",
        )
    payload = {
        "user_id": data.get("user_id"),
        "image_path": image_path,
        "source": data.get("source") or "USER_PROMPT",
        "generated_image_id": data.get("generated_image_id"),
        "instagram_account_id": data.get("instagram_account_id"),
        "original_filename": overrides.get("original_filename"),
        "festival_campaign_id": overrides.get("festival_campaign_id") or data.get("festival_campaign_id"),
        "scheduled_date": overrides.get("scheduled_date"),
        "wait": overrides.get("wait", True),
        "caption": data.get("caption"),
    }
    payload.update({key: value for key, value in overrides.items() if key not in {"wait"}})
    payload["wait"] = overrides.get("wait", True)
    payload["image_path"] = image_path
    return await enqueue_instagram_publication(payload)
