"""Shadow execution of the registered Instagram workflow.

The studio publish path is unchanged. This runner records a result and does not
call the Instagram publication handoff.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from agent_runtime.models import ExecutionMode, ExecutionRecord, TriggerContext
from agent_runtime.runtime import AgentRuntime
from agent_runtime.services import ApprovalRegistry, HandlerRegistry
from agent_runtime.workflows import register_workflow
from backend.mcp.client import MCPClient
from backend.mcp.tenant_isolation import TenantContext, trusted_tenant
from db.platform_agents import (
    ensure_reference_agent,
    get_agent,
    get_execution,
    published_version,
    record_execution,
    utcnow,
)
from db.session import get_session_factory
from instagram_reference.definition import (
    AGENT_KEY,
    DISPLAY_NAME,
    POLICY_ID,
    TRIGGER_KEY,
    WORKFLOW_KEY,
    register_instagram_marketing,
)
from instagram_reference.handlers import (
    PERSIST_HANDLER_ID,
    PUBLISH_HANDLER_ID,
    VERIFY_HANDLER_ID,
    meta_verification,
    persist_result,
)
from instagram_reference.policy import InstagramStillImagePolicy
from instagram_reference.services import (
    ReferenceKnowledgeService,
    ReferenceModelService,
    ReferenceToolService,
)
from models.errors import AppError, ErrorCode

_SYSTEM_TENANT = "system"


async def shadow_publication(ctx: object, step: object, state: Mapping[str, Any]) -> dict[str, Any]:
    """Publication stand-in. It never calls Meta and never enqueues a post."""

    del ctx, step, state
    return {
        "publisher": "instagram_agent",
        "published": False,
        "verified": False,
        "skip_publish": True,
        "instagram_media_id": None,
        "reason": "shadow_mode",
    }


def build_shadow_runtime(
    *,
    settings: Any | None = None,
    session_factory: Any | None = None,
) -> AgentRuntime:
    """Runtime whose publish step is the shadow handler."""

    knowledge = ReferenceKnowledgeService()
    tools = ReferenceToolService()
    if session_factory is not None:
        from agent.content_orchestrator import build_content_mcp
        from backend.platform.knowledge.binding import instagram_marketing_knowledge
        from backend.platform.knowledge.service import PlatformKnowledgeService

        client = build_content_mcp(session_factory)
        knowledge = ReferenceKnowledgeService(
            PlatformKnowledgeService(instagram_marketing_knowledge(), invoker=_knowledge_invoker(client))
        )
        tools = ReferenceToolService(invoker=_tool_invoker(client))
    handlers = HandlerRegistry()
    handlers.register(PUBLISH_HANDLER_ID, shadow_publication)
    handlers.register(VERIFY_HANDLER_ID, meta_verification)
    handlers.register(PERSIST_HANDLER_ID, persist_result)
    approvals = ApprovalRegistry()
    approvals.register(POLICY_ID, InstagramStillImagePolicy())
    return AgentRuntime(
        catalog=register_instagram_marketing(),
        handlers=handlers,
        tools=tools,
        models=ReferenceModelService(settings),
        knowledge=knowledge,
        approvals=approvals,
    )


async def run_shadow(
    tenant_id: str,
    payload: Mapping[str, Any] | None = None,
    *,
    settings: Any | None = None,
    session_factory: Any | None = None,
    agent_key: str = AGENT_KEY,
    execution_id: str | None = None,
    trigger_key: str | None = None,
    version_number: int | None = None,
) -> dict[str, Any]:
    """Run the published reference workflow and store the execution."""

    del version_number
    factory = session_factory or get_session_factory()
    body = dict(payload or {})
    body.pop("tenant_id", None)
    started = utcnow()
    with factory() as session:
        agent, version = _runnable(session, tenant_id, agent_key)
        session.commit()
        agent_id = agent.id
        version_id = version.id
        workflow_key = str(version.snapshot.get("workflow_key") or "")
    runtime = build_shadow_runtime(settings=settings, session_factory=factory)
    trigger = TriggerContext(
        trigger_key=trigger_key or TRIGGER_KEY,
        tenant_id=tenant_id,
        user_id=tenant_id,
        payload=body,
        mode=ExecutionMode.SHADOW,
    )
    record = await runtime.run(AGENT_KEY, trigger)
    if execution_id:
        record.execution_id = execution_id
    completed = record.completed_at or utcnow()
    output = _output(record, body)
    error_code = record.error.code.value if record.error is not None else None
    error_message = record.error.message if record.error is not None else None
    with factory() as session:
        agent = get_agent(session, _SYSTEM_TENANT if agent_key == AGENT_KEY else tenant_id, agent_key)
        version = published_version(session, agent) if agent is not None else None
        if agent is None or version is None:
            raise AppError(ErrorCode.NOT_FOUND, "Agent was not found.")
        stored = record_execution(
            session,
            execution_id=record.execution_id,
            tenant_id=tenant_id,
            agent=agent,
            version=version,
            workflow_key=workflow_key or WORKFLOW_KEY,
            status=record.status.value,
            mode=ExecutionMode.SHADOW.value,
            started_at=_aware(record.started_at) or started,
            completed_at=_aware(completed),
            error_code=error_code,
            error_message=error_message,
            output=output,
        )
        session.commit()
        stored_id = stored.id
    view = _view(record, stored_id, output)
    view["agent_id"] = agent_id
    view["agent_version_id"] = version_id
    return view


def execution_for(session_factory: Any, tenant_id: str, execution_id: str) -> dict[str, Any] | None:
    with session_factory() as session:
        row = get_execution(session, tenant_id, execution_id)
        if row is None:
            return None
        return {
            "execution_id": row.id,
            "tenant_id": row.tenant_id,
            "agent_id": row.agent_id,
            "agent_version_id": row.agent_version_id,
            "workflow": row.workflow_key,
            "status": row.status,
            "mode": row.mode,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "output": row.output,
        }


def _runnable(session: Any, tenant_id: str, agent_key: str) -> tuple[Any, Any]:
    if agent_key == AGENT_KEY:
        agent = ensure_reference_agent(session)
    else:
        agent = get_agent(session, tenant_id, agent_key)
    if agent is None or agent.status == "retired":
        raise AppError(ErrorCode.NOT_FOUND, "Agent was not found.")
    if agent.status != "active":
        raise AppError(ErrorCode.CONFLICT, "Agent is paused.")
    version = published_version(session, agent)
    if version is None or version.status != "published":
        raise AppError(ErrorCode.INVALID_REQUEST, "A draft agent cannot run.")
    if version.snapshot.get("workflow_key") != WORKFLOW_KEY:
        raise AppError(ErrorCode.INVALID_REQUEST, "Workflow is not registered.")
    return agent, version


def _knowledge_invoker(client: MCPClient):
    async def invoke(tool_name: str, arguments: dict[str, Any], tenant: TenantContext) -> Mapping[str, Any]:
        result = await client.invoke(tool_name, arguments, tenant)
        return result.data

    return invoke


def _tool_invoker(client: MCPClient):
    async def invoke(tool_name: str, arguments: dict[str, Any], tenant_id: str) -> Mapping[str, Any]:
        result = await client.invoke(tool_name, arguments, trusted_tenant(tenant_id))
        return result.data

    return invoke


def _output(record: ExecutionRecord, payload: Mapping[str, Any]) -> dict[str, Any]:
    steps = record.output.get("steps") if isinstance(record.output, dict) else {}
    if not isinstance(steps, dict):
        steps = {}
    planning = steps.get("content_planning") if isinstance(steps.get("content_planning"), dict) else {}
    publication = steps.get("instagram_agent_publication")
    published = isinstance(publication, dict) and publication.get("published") is True
    studio = payload.get("studio_preview") if isinstance(payload.get("studio_preview"), dict) else None
    comparison = None
    if studio is not None:
        comparison = {
            "studio_planned": studio.get("planned"),
            "shadow_planned": planning.get("planned"),
            "studio_published": studio.get("published"),
            "shadow_published": False,
        }
    return {
        "published": False,
        "shadow_published": False,
        "handler_published": published,
        "planned": planning.get("planned"),
        "comparison": comparison,
        "steps": [
            {"step_key": step.step_key, "status": step.status.value}
            for step in record.steps
        ],
    }


def _view(record: ExecutionRecord, execution_id: str, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": execution_id,
        "execution_id": execution_id,
        "tenant_id": record.tenant_id,
        "status": record.status.value,
        "trigger_key": record.trigger_key,
        "workflow_id": record.workflow_key,
        "workflow": record.workflow_key,
        "mode": ExecutionMode.SHADOW.value,
        "published": False,
        "started_at": _iso(record.started_at),
        "completed_at": _iso(record.completed_at),
        "agent_version_number": record.agent_version_number,
        "error": None
        if record.error is None
        else {"code": record.error.code.value, "message": record.error.message},
        "output": output,
        "steps": output["steps"],
    }


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware is not None else None


def _register() -> None:
    register_workflow(WORKFLOW_KEY, run_shadow)


_register()


def reference_display_name() -> str:
    return DISPLAY_NAME
