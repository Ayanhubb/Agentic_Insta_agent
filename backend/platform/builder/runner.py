"""Test runs for the agent builder. They never call Meta or a model vendor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime.catalog import InMemoryCatalog
from agent_runtime.errors import RuntimeErrorCode
from agent_runtime.models import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    ExecutionMode,
    ExecutionPolicy,
    ExecutionRecord,
    OwnerKind,
    TriggerBinding,
    TriggerContext,
    VersionStatus,
    WorkflowPin,
)
from agent_runtime.runtime import AgentRuntime
from agent_runtime.services import ApprovalRegistry, HandlerRegistry
from instagram_reference.definition import (
    AGENT_KEY,
    POLICY_ID,
    WORKFLOW_KEY,
    WORKFLOW_VERSION,
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
from instagram_reference.services import ReferenceKnowledgeService, ReferenceToolService
from models.errors import AppError, ErrorCode

_MODEL_ROLES = frozenset({"content_planning", "image_generation", "vision_qa"})


async def _test_publication(ctx: object, step: object, state: Mapping[str, Any]) -> dict[str, Any]:
    del ctx, step, state
    return {
        "publisher": "instagram_agent",
        "published": False,
        "verified": False,
        "skip_publish": False,
        "instagram_media_id": None,
        "reason": "test_execution",
    }


class _TestModelService:
    def __init__(self, llm_provider: str, image_provider: str) -> None:
        self._providers = {
            "content_planning": llm_provider,
            "image_generation": image_provider,
            "vision_qa": llm_provider if llm_provider in {"deepseek", "mock"} else "mock",
        }

    async def complete(self, ctx: object, role: str, state: Mapping[str, Any]) -> dict[str, Any]:
        del ctx, state
        return {
            "role": role,
            "provider": self._providers.get(role, "mock"),
            "planned": False,
            "generated": False,
            "edited": False,
            "passed": False,
            "malformed": True,
            "reason": "test_execution",
        }


def _runtime(llm_provider: str, image_provider: str) -> tuple[AgentRuntime, InMemoryCatalog]:
    catalog = register_instagram_marketing()
    handlers = HandlerRegistry()
    handlers.register(PUBLISH_HANDLER_ID, _test_publication)
    handlers.register(VERIFY_HANDLER_ID, meta_verification)
    handlers.register(PERSIST_HANDLER_ID, persist_result)
    approvals = ApprovalRegistry()
    approvals.register(POLICY_ID, InstagramStillImagePolicy())
    runtime = AgentRuntime(
        catalog=catalog,
        handlers=handlers,
        tools=ReferenceToolService(),
        models=_TestModelService(llm_provider, image_provider),
        knowledge=ReferenceKnowledgeService(),
        approvals=approvals,
    )
    return runtime, catalog


def _http_error(record: ExecutionRecord) -> AppError:
    code = None if record.error is None else record.error.code
    message = "Agent test did not start."
    if record.error is not None and record.error.message:
        message = record.error.message
    if code == RuntimeErrorCode.AGENT_NOT_FOUND:
        return AppError(ErrorCode.NOT_FOUND, "Agent not found.")
    if code == RuntimeErrorCode.AGENT_PAUSED:
        return AppError(ErrorCode.CONFLICT, "Agent is paused.")
    return AppError(ErrorCode.INVALID_REQUEST, message)


def _view(record: ExecutionRecord, workflow_id: str) -> dict[str, Any]:
    started = record.started_at or record.occurred_at
    return {
        "id": record.execution_id,
        "status": record.status.value,
        "trigger_key": record.trigger_key,
        "workflow_id": workflow_id,
        "started_at": None if started is None else started.isoformat(),
        "mode": "test",
        "published": False,
        "agent_version_number": record.agent_version_number,
        "error": None
        if record.error is None
        else {"code": record.error.code.value, "message": record.error.message},
        "steps": [
            {
                "step_key": step.step_key,
                "status": step.status.value,
                "error": None if step.error is None else {"code": step.error.code.value},
            }
            for step in record.steps
        ],
    }


async def run_system(tenant_id: str, payload: dict[str, Any], trigger_key: str) -> dict[str, Any]:
    runtime, _catalog = _runtime("mock", "mock")
    trigger = TriggerContext(
        trigger_key=trigger_key,
        tenant_id=tenant_id,
        user_id=tenant_id,
        payload=dict(payload),
        mode=ExecutionMode.TEST,
    )
    record = await runtime.run(AGENT_KEY, trigger)
    if record.error is not None and not record.steps and record.agent_version_number is None:
        raise _http_error(record)
    return _view(record, WORKFLOW_KEY)


async def run_reference(
    *,
    tenant_id: str,
    agent_id: str,
    display_name: str,
    status: str,
    version_number: int,
    version_status: str,
    workflow_id: str,
    triggers: list[str],
    tools: list[str],
    knowledge: list[str],
    assets: list[str],
    approval_policy_id: str,
    llm_provider: str,
    image_provider: str,
    trigger_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    runtime, catalog = _runtime(llm_provider, image_provider)
    agent_status = AgentStatus(status)
    definition = AgentDefinition(
        key=agent_id,
        owner=OwnerKind.USER,
        status=agent_status,
        display_name=display_name[:120] or "Agent",
        user_id=tenant_id,
    )
    version = AgentVersion(
        version_number=version_number,
        status=VersionStatus(version_status),
        workflow_pins=(WorkflowPin(workflow_key=workflow_id, version_number=WORKFLOW_VERSION),),
        trigger_bindings=tuple(
            TriggerBinding(trigger_key=item, workflow_key=workflow_id) for item in triggers
        ),
        tool_grants=frozenset(tools),
        model_roles=_MODEL_ROLES,
        knowledge_sources=frozenset(knowledge),
        asset_roles=frozenset(assets),
        approval_policy_id=approval_policy_id,
        execution_policy=ExecutionPolicy(timeout_seconds=120),
    )
    catalog.save_definition(definition)
    catalog.save_version(definition, version)
    trigger = TriggerContext(
        trigger_key=trigger_key,
        tenant_id=tenant_id,
        user_id=tenant_id,
        payload=dict(payload),
        mode=ExecutionMode.TEST,
        requested_version_number=version_number,
    )
    record = await runtime.run(agent_id, trigger)
    if record.error is not None and not record.steps and record.agent_version_number is None:
        raise _http_error(record)
    return _view(record, workflow_id)


async def run_declared(
    *,
    execution_id: str,
    workflow_id: str,
    trigger_key: str,
    tenant_id: str,
    payload: dict[str, Any] | None = None,
    settings: Any | None = None,
    session_factory: Any | None = None,
    version_number: int | None = None,
) -> dict[str, Any]:
    """Resolve a closed workflow key and run its registered implementation."""

    import instagram_reference.shadow  # noqa: F401 - registers the reference workflow
    from agent_runtime.workflows import UnknownWorkflow, resolve_workflow

    try:
        runner = resolve_workflow(workflow_id)
    except UnknownWorkflow as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "Workflow is not registered.") from exc
    return await runner(
        tenant_id=tenant_id,
        payload=payload or {},
        settings=settings,
        session_factory=session_factory,
        execution_id=execution_id,
        trigger_key=trigger_key,
        version_number=version_number,
    )
