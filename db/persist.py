"""Map V1 AgentState onto agent_tasks / agent_events / instagram_posts without rewriting the Agent."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from db.enums import PostStatus, PostType, TaskTrigger, TaskType
from db.models import User
from db.repositories import AgentEventRepository, AgentTaskRepository, InstagramPostRepository
from db.schemas import AgentEventCreate, AgentTaskCreate
from models.errors import ErrorCode, OperationCertainty
from models.state import AgentState, TaskStatus

logger = logging.getLogger(__name__)


def _account_fk(state: AgentState) -> str | None:
    return state.instagram_account_pk or state.instagram_account_ref or state.instagram_account_id


def _existing_fk(session: Session, model, value: str | None) -> str | None:
    if not value:
        return None
    return value if session.get(model, value) is not None else None


def _task_error(state: AgentState) -> str | None:
    if state.error is None:
        return None
    return f"{state.error.code.value}: {state.error.message}"


def _post_type(state: AgentState) -> PostType:
    raw = (state.post_type or "").strip().upper()
    try:
        return PostType(raw) if raw else PostType.USER_PROMPT
    except ValueError:
        return PostType.USER_PROMPT


def _post_status(state: AgentState) -> PostStatus:
    if state.status == TaskStatus.COMPLETED and state.instagram_media_id and (
        state.verified or state.publish_certainty == OperationCertainty.SUCCEEDED
    ):
        return PostStatus.PUBLISHED
    if state.status == TaskStatus.COMPLETED and state.instagram_media_id:
        # Completing with a media id is the V1 verified success path.
        return PostStatus.PUBLISHED
    if state.error and state.error.code == ErrorCode.AMBIGUOUS_PUBLICATION:
        return PostStatus.AMBIGUOUS_PUBLICATION
    if state.status == TaskStatus.FAILED:
        return PostStatus.FAILED
    if state.status in {TaskStatus.PUBLISHING, TaskStatus.VERIFYING}:
        return PostStatus.PUBLISHING
    return PostStatus.GENERATED


def persist_agent_state(session: Session, state: AgentState) -> None:
    """Write AgentState into agent_tasks + agent_events, and upsert a post on terminal success/failure."""
    user_id = _existing_fk(session, User, state.user_id)
    tasks = AgentTaskRepository(session)
    events = AgentEventRepository(session)
    snapshot = state.model_dump(mode="json")
    tasks.upsert(
        AgentTaskCreate(
            id=state.task_id,
            user_id=user_id,
            task_type=TaskType.INSTAGRAM_PUBLISH,
            trigger=TaskTrigger.USER,
            status=state.status.value,
            current_step=state.current_step,
            started_at=state.created_at,
            completed_at=state.completed_at,
            error=_task_error(state),
            state_json=snapshot,
        )
    )
    event_rows = [
        AgentEventCreate(
            task_id=state.task_id,
            from_state=item.source.value if hasattr(item.source, "value") else str(item.source),
            to_state=item.target.value if hasattr(item.target, "value") else str(item.target),
            tool=item.tool,
            result=item.result,
            observation=None,
            timestamp=item.timestamp,
        )
        for item in state.execution_history
    ]
    for trace in state.execution_trace:
        event_rows.append(
            AgentEventCreate(
                task_id=state.task_id,
                from_state=None,
                to_state=trace.status,
                tool=trace.tool,
                result=trace.status,
                observation=trace.observation_summary or None,
                timestamp=state.updated_at,
            )
        )
    events.replace_for_task(state.task_id, event_rows)

    if state.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
        posts = InstagramPostRepository(session)
        from db.models import InstagramAccount, GeneratedImage

        account_id = _existing_fk(session, InstagramAccount, _account_fk(state))
        image_id = _existing_fk(session, GeneratedImage, state.generated_image_id)
        status = _post_status(state)
        verified = status == PostStatus.PUBLISHED
        scheduled: date | None = state.scheduled_date
        post = posts.upsert_from_publication(
            id=state.db_post_id,
            user_id=user_id,
            instagram_account_id=account_id,
            generated_image_id=image_id,
            agent_task_id=state.task_id,
            status=status,
            post_type=_post_type(state),
            instagram_media_id=state.instagram_media_id,
            permalink=state.permalink,
            published_at=state.completed_at if verified else None,
            error=_task_error(state),
            scheduled_date=scheduled,
            verified=verified,
        )
        if post is not None:
            state.db_post_id = post.id
            state.db_task_id = state.task_id
            if verified:
                state.verified = True
            if image_id:
                image = session.get(GeneratedImage, image_id)
                if image is not None:
                    image.publication_status = status.value if hasattr(status, "value") else str(status)


def load_agent_state(session: Session, task_id: str) -> AgentState | None:
    task = AgentTaskRepository(session).get(task_id)
    if task is None or not task.state_json:
        return None
    return AgentState.model_validate(task.state_json)


def persist_agent_state_with_factory(factory: sessionmaker, state: AgentState) -> None:
    from sqlalchemy.exc import OperationalError

    session = factory()
    try:
        persist_agent_state(session, state)
        session.commit()
    except OperationalError:
        session.rollback()
        logger.warning("Skipping agent state persist (database busy) for task %s", state.task_id)
    except Exception:
        session.rollback()
        logger.exception("Failed to persist agent state for task %s", state.task_id)
        raise
    finally:
        session.close()
