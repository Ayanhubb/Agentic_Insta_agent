"""Register the reference agent and run it. HTTP routes stay on their current functions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime.models import ExecutionMode, ExecutionRecord, TriggerContext
from agent_runtime.runtime import AgentRuntime
from agent_runtime.services import ApprovalRegistry, HandlerRegistry
from instagram_reference.definition import (
    AGENT_KEY,
    POLICY_ID,
    TRIGGER_KEY,
    register_instagram_marketing,
)
from instagram_reference.handlers import (
    PERSIST_HANDLER_ID,
    PUBLISH_HANDLER_ID,
    VERIFY_HANDLER_ID,
    instagram_agent_publish,
    meta_verification,
    persist_result,
)
from instagram_reference.policy import InstagramStillImagePolicy
from instagram_reference.services import (
    ReferenceKnowledgeService,
    ReferenceModelService,
    ReferenceToolService,
)


def build_reference_runtime(
    *,
    knowledge: ReferenceKnowledgeService | None = None,
    models: Any | None = None,
    tools: ReferenceToolService | None = None,
    settings: Any | None = None,
) -> AgentRuntime:
    """Runtime for ``instagram_marketing``. Creating it does not mount a route."""

    handlers = HandlerRegistry()
    handlers.register(PUBLISH_HANDLER_ID, instagram_agent_publish)
    handlers.register(VERIFY_HANDLER_ID, meta_verification)
    handlers.register(PERSIST_HANDLER_ID, persist_result)
    approvals = ApprovalRegistry()
    approvals.register(POLICY_ID, InstagramStillImagePolicy())
    return AgentRuntime(
        catalog=register_instagram_marketing(),
        handlers=handlers,
        tools=tools if tools is not None else ReferenceToolService(),
        models=models if models is not None else ReferenceModelService(settings),
        knowledge=knowledge if knowledge is not None else ReferenceKnowledgeService(),
        approvals=approvals,
    )


async def dispatch(
    tenant_id: str,
    payload: Mapping[str, Any] | None = None,
    *,
    runtime: AgentRuntime | None = None,
) -> ExecutionRecord:
    """Run the reference workflow for a server-supplied tenant.

    The tenant id comes from the caller. A tenant id inside ``payload`` is discarded.
    """

    active = runtime if runtime is not None else build_reference_runtime()
    trigger = TriggerContext(
        trigger_key=TRIGGER_KEY,
        tenant_id=tenant_id,
        user_id=tenant_id,
        payload=dict(payload or {}),
        mode=ExecutionMode.PRODUCTION,
    )
    return await active.run(AGENT_KEY, trigger)
