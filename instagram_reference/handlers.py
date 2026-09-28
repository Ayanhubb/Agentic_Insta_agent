"""Step handlers. Publication calls the existing Instagram handoff and nothing else."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import ExecutionContext, WorkflowStep
from instagram_reference.boundary import mentions_publish_tool
from models.errors import AppError
from models.state import AgentState, TaskStatus
from services.publication_store import PublicationSource

PUBLISH_HANDLER_ID = "instagram_agent_publish"
VERIFY_HANDLER_ID = "meta_verification"
PERSIST_HANDLER_ID = "persist_result"

_SOURCES = frozenset(item.value for item in PublicationSource)


async def instagram_agent_publish(
    ctx: ExecutionContext,
    step: WorkflowStep,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Hand the still image to ``enqueue_instagram_publication``.

    The handler does not construct a Graph client, read a token, or retry after
    ``skip_publish``. Verification and storage happen inside the Instagram Agent.
    """

    decision = state.get("approval_policy") if isinstance(state.get("approval_policy"), dict) else {}
    if decision.get("allowed") is not True or mentions_publish_tool(state.get("content_planning")):
        return _not_published("approval_denied")
    image_path = _image_path(state)
    if not image_path:
        return _not_published("image_missing")
    payload = _request_payload(ctx, state, image_path)
    try:
        from agent.instagram_tasks import enqueue_instagram_publication

        result = await enqueue_instagram_publication(payload)
    except AppError as exc:
        raise RuntimeFailure(
            RuntimeErrorCode.STEP_FAILED,
            "Instagram publication was not started.",
            step_key=step.step_key,
        ) from exc
    if not isinstance(result, AgentState):
        raise RuntimeFailure(
            RuntimeErrorCode.STEP_FAILED,
            "Instagram publication was not started.",
            step_key=step.step_key,
        )
    return _publication_view(result)


async def meta_verification(
    ctx: ExecutionContext,
    step: WorkflowStep,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Read the Instagram Agent result. This step does not call Meta."""

    del ctx, step
    publication = state.get("instagram_agent_publication")
    if not isinstance(publication, dict):
        publication = {}
    media_id = publication.get("instagram_media_id")
    verified = (
        publication.get("verified") is True
        and publication.get("skip_publish") is not True
        and isinstance(media_id, str)
        and bool(media_id.strip())
    )
    return {
        "verified": verified,
        "instagram_media_id": media_id if isinstance(media_id, str) and media_id.strip() else None,
        "publisher": "instagram_agent",
    }


async def persist_result(
    ctx: ExecutionContext,
    step: WorkflowStep,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Report the task and post the Instagram Agent already stored."""

    del ctx, step
    publication = state.get("instagram_agent_publication")
    verification = state.get("meta_verification")
    if not isinstance(publication, dict):
        publication = {}
    if not isinstance(verification, dict):
        verification = {}
    task_id = publication.get("task_id") if isinstance(publication.get("task_id"), str) else None
    db_task_id = publication.get("db_task_id") if isinstance(publication.get("db_task_id"), str) else None
    db_post_id = publication.get("db_post_id") if isinstance(publication.get("db_post_id"), str) else None
    accepted = verification.get("verified") is True and bool(verification.get("instagram_media_id"))
    return {
        "persisted": bool(task_id or db_task_id or db_post_id),
        "accepted": accepted,
        "task_id": task_id,
        "db_task_id": db_task_id,
        "db_post_id": db_post_id,
        "publisher": "instagram_agent",
    }


def _request_payload(ctx: ExecutionContext, state: Mapping[str, Any], image_path: str) -> dict[str, Any]:
    incoming = state.get("input") if isinstance(state.get("input"), dict) else {}
    source = incoming.get("source")
    if source not in _SOURCES:
        source = PublicationSource.USER_PROMPT.value
    payload: dict[str, Any] = {
        "user_id": ctx.tenant_id,
        "image_path": image_path,
        "source": source,
        "wait": True,
        "allow_environment_fallback": False,
    }
    caption = incoming.get("caption")
    if isinstance(caption, str) and caption.strip():
        payload["caption"] = caption.strip()
    image = state.get("image_generation") if isinstance(state.get("image_generation"), dict) else {}
    image_id = image.get("image_id") if isinstance(image.get("image_id"), str) else incoming.get("generated_image_id")
    if isinstance(image_id, str) and image_id.strip():
        payload["generated_image_id"] = image_id.strip()
    return payload


def _image_path(state: Mapping[str, Any]) -> str | None:
    incoming = state.get("input") if isinstance(state.get("input"), dict) else {}
    image = state.get("image_generation") if isinstance(state.get("image_generation"), dict) else {}
    for value in (incoming.get("image_path"), image.get("image_path")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _not_published(reason: str) -> dict[str, Any]:
    return {
        "publisher": "instagram_agent",
        "published": False,
        "verified": False,
        "skip_publish": False,
        "instagram_media_id": None,
        "reason": reason,
    }


def _publication_view(state: AgentState) -> dict[str, Any]:
    media_id = state.instagram_media_id.strip() if isinstance(state.instagram_media_id, str) else ""
    status = state.status.value if isinstance(state.status, TaskStatus) else str(state.status)
    verified = bool(state.verified) and bool(media_id) and not state.skip_publish
    return {
        "publisher": "instagram_agent",
        "published": verified,
        "verified": verified,
        "skip_publish": bool(state.skip_publish),
        "instagram_media_id": media_id or None,
        "task_id": state.task_id,
        "db_task_id": state.db_task_id,
        "db_post_id": state.db_post_id,
        "status": status,
    }
