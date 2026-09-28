"""Generic agent runtime.

Trigger, resolve a published version, seal an execution snapshot, then run the
workflow engine. This package does not import product publishers or evaluate
caller-supplied code.
"""

from __future__ import annotations

import re

from agent_runtime.catalog import AgentCatalog
from agent_runtime.engine import WorkflowEngine
from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import (
    AgentDefinition,
    AgentVersion,
    CancellationToken,
    ExecutionContext,
    ExecutionRecord,
    ExecutionStatus,
    RuntimeEvent,
    TriggerContext,
    WorkflowVersion,
    freeze_payload,
    requires_published_version,
)
from agent_runtime.resolver import AgentResolver, ResolvedRun
from agent_runtime.services import (
    ApprovalRegistry,
    AssetService,
    Clock,
    ExecutionObserver,
    HandlerRegistry,
    IdGenerator,
    KnowledgeService,
    ModelService,
    NullObserver,
    SystemClock,
    ToolService,
    UnavailableAssetService,
    UnavailableKnowledgeService,
    UnavailableModelService,
    UnavailableToolService,
    UuidIdGenerator,
)
from agent_runtime.store import ExecutionStore, InMemoryExecutionStore

_AGENT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_STATUS_FOR_CODE = {
    RuntimeErrorCode.CANCELLED: ExecutionStatus.CANCELLED,
    RuntimeErrorCode.TIMEOUT: ExecutionStatus.TIMED_OUT,
    RuntimeErrorCode.APPROVAL_REQUIRED: ExecutionStatus.WAITING_APPROVAL,
}


class AgentRuntime:
    """Dispatch one trigger through a resolved, immutable version snapshot."""

    def __init__(
        self,
        *,
        catalog: AgentCatalog,
        handlers: HandlerRegistry | None = None,
        tools: ToolService | None = None,
        models: ModelService | None = None,
        knowledge: KnowledgeService | None = None,
        assets: AssetService | None = None,
        approvals: ApprovalRegistry | None = None,
        store: ExecutionStore | None = None,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        observer: ExecutionObserver | None = None,
    ) -> None:
        self._handlers = handlers if handlers is not None else HandlerRegistry()
        self._approvals = approvals if approvals is not None else ApprovalRegistry()
        self._store = store if store is not None else InMemoryExecutionStore()
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else UuidIdGenerator()
        self._observer = observer if observer is not None else NullObserver()
        self._resolver = AgentResolver(catalog)
        self._engine = WorkflowEngine(
            tools=tools if tools is not None else UnavailableToolService(),
            models=models if models is not None else UnavailableModelService(),
            knowledge=knowledge if knowledge is not None else UnavailableKnowledgeService(),
            assets=assets if assets is not None else UnavailableAssetService(),
            approvals=self._approvals,
            handlers=self._handlers,
            store=self._store,
            clock=self._clock,
            observer=self._observer,
        )

    async def run(
        self,
        agent_key: str,
        trigger: TriggerContext,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionRecord:
        """Resolve the agent for a trigger and execute the pinned workflow."""
        self._require_agent_key(agent_key)
        token = cancellation if cancellation is not None else CancellationToken()
        record = self._new_record(agent_key, trigger)
        claimed = self._store.begin(record)
        if claimed is not record:
            return claimed
        if token.cancelled:
            return self._fail(claimed, RuntimeFailure(RuntimeErrorCode.CANCELLED, "The execution was cancelled."))
        try:
            resolved = self._resolver.resolve(agent_key, trigger)
        except RuntimeFailure as failure:
            return self._fail(claimed, failure)
        snapshot = resolved.snapshot(
            execution_id=claimed.execution_id,
            correlation_id=claimed.correlation_id,
            trigger=trigger,
        )
        context = ExecutionContext(
            execution_id=claimed.execution_id,
            correlation_id=claimed.correlation_id,
            tenant_id=trigger.tenant_id,
            user_id=trigger.user_id,
            mode=trigger.mode,
            trigger=trigger,
            snapshot=snapshot,
            cancellation=token,
            payload=freeze_payload(trigger.payload),
        )
        return await self.execute(
            resolved.definition,
            resolved.version,
            resolved.workflow,
            context,
            trigger,
            record=claimed,
        )

    async def execute(
        self,
        definition: AgentDefinition,
        version: AgentVersion,
        workflow: WorkflowVersion,
        context: ExecutionContext,
        trigger: TriggerContext,
        *,
        record: ExecutionRecord | None = None,
    ) -> ExecutionRecord:
        """Run a workflow from an already resolved definition and version snapshot."""
        active = record if record is not None else self._record_for_context(definition.key, context, trigger)
        if record is None:
            claimed = self._store.begin(active)
            if claimed is not active:
                return claimed
            active = claimed
        try:
            self._assert_context(definition, version, workflow, context, trigger)
            self._resolver.validate(ResolvedRun(definition, version, workflow), trigger)
        except RuntimeFailure as failure:
            return self._fail(active, failure)
        if context.cancellation.cancelled and active.status == ExecutionStatus.PENDING:
            return self._fail(active, RuntimeFailure(RuntimeErrorCode.CANCELLED, "The execution was cancelled."))
        return await self._engine.run(active, context)

    def _new_record(self, agent_key: str, trigger: TriggerContext) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=self._ids.execution_id(),
            correlation_id=trigger.correlation_id or self._ids.correlation_id(),
            tenant_id=trigger.tenant_id,
            user_id=trigger.user_id,
            trigger_key=trigger.trigger_key,
            mode=trigger.mode,
            status=ExecutionStatus.PENDING,
            agent_key=agent_key,
            idempotency_key=trigger.idempotency_key,
            occurred_at=trigger.occurred_at or self._clock.now(),
        )

    def _record_for_context(
        self,
        agent_key: str,
        context: ExecutionContext,
        trigger: TriggerContext,
    ) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=context.execution_id,
            correlation_id=context.correlation_id,
            tenant_id=trigger.tenant_id,
            user_id=trigger.user_id,
            trigger_key=trigger.trigger_key,
            mode=context.mode,
            status=ExecutionStatus.PENDING,
            agent_key=agent_key,
            idempotency_key=trigger.idempotency_key,
            occurred_at=trigger.occurred_at or self._clock.now(),
        )

    def _fail(self, record: ExecutionRecord, failure: RuntimeFailure) -> ExecutionRecord:
        record.status = _STATUS_FOR_CODE.get(failure.code, ExecutionStatus.FAILED)
        record.error = failure.to_error()
        if record.status != ExecutionStatus.WAITING_APPROVAL:
            record.completed_at = self._clock.now()
        self._store.save(record)
        self._observer.observe(
            RuntimeEvent(
                name="execution.finished",
                execution_id=record.execution_id,
                correlation_id=record.correlation_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                agent_key=record.agent_key,
                agent_version_number=record.agent_version_number,
                workflow_key=record.workflow_key,
                workflow_version_number=record.workflow_version_number,
                status=record.status.value,
                trigger_key=record.trigger_key,
                error_code=None if record.error is None else record.error.code.value,
            )
        )
        return record

    def _require_agent_key(self, agent_key: str) -> None:
        if not _AGENT_KEY.fullmatch(agent_key):
            raise RuntimeFailure(RuntimeErrorCode.INVALID_REQUEST, "Agent key is invalid.")

    def _assert_context(
        self,
        definition: AgentDefinition,
        version: AgentVersion,
        workflow: WorkflowVersion,
        context: ExecutionContext,
        trigger: TriggerContext,
    ) -> None:
        snapshot = context.snapshot
        if (
            context.tenant_id != trigger.tenant_id
            or snapshot.tenant_id != trigger.tenant_id
            or context.user_id != trigger.user_id
            or snapshot.user_id != trigger.user_id
            or context.mode != trigger.mode
            or snapshot.mode != trigger.mode
            or context.execution_id != snapshot.execution_id
            or context.correlation_id != snapshot.correlation_id
            or snapshot.trigger_key != trigger.trigger_key
            or snapshot.agent_key != definition.key
            or snapshot.agent_version_number != version.version_number
            or snapshot.agent_version_status != version.status
            or snapshot.workflow_key != workflow.workflow_key
            or snapshot.workflow_version_number != workflow.version_number
            or snapshot.steps != tuple(workflow.steps)
        ):
            raise RuntimeFailure(
                RuntimeErrorCode.INVALID_REQUEST,
                "Execution context does not match the resolved version.",
            )
        if requires_published_version(trigger.mode) and version.status.value != "published":
            raise RuntimeFailure(
                RuntimeErrorCode.DRAFT_NOT_ALLOWED,
                "Draft agent versions cannot run in production.",
            )
