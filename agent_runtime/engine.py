"""Ordered step dispatcher. Handlers are registry lookups, not imported paths."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_runtime.errors import SAFE_INTERNAL_MESSAGE, RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import (
    ApprovalDecision,
    ExecutionContext,
    ExecutionRecord,
    ExecutionStatus,
    RuntimeEvent,
    StepKind,
    StepRecord,
    StepStatus,
    VersionStatus,
    WorkflowStep,
)
from agent_runtime.sanitize import isolate_mapping, sanitize_payload
from agent_runtime.services import (
    ApprovalRegistry,
    AssetService,
    Clock,
    ExecutionObserver,
    HandlerRegistry,
    KnowledgeService,
    ModelService,
    ToolService,
)
from agent_runtime.store import ExecutionStore

_STEP_STATUS = {
    RuntimeErrorCode.CANCELLED: StepStatus.CANCELLED,
    RuntimeErrorCode.TIMEOUT: StepStatus.TIMED_OUT,
    RuntimeErrorCode.DRAFT_CANNOT_PUBLISH: StepStatus.BLOCKED,
    RuntimeErrorCode.APPROVAL_REQUIRED: StepStatus.WAITING,
}

_EXEC_STATUS = {
    RuntimeErrorCode.CANCELLED: ExecutionStatus.CANCELLED,
    RuntimeErrorCode.TIMEOUT: ExecutionStatus.TIMED_OUT,
    RuntimeErrorCode.APPROVAL_REQUIRED: ExecutionStatus.WAITING_APPROVAL,
}


class WorkflowEngine:
    def __init__(
        self,
        *,
        tools: ToolService,
        models: ModelService,
        knowledge: KnowledgeService,
        assets: AssetService,
        approvals: ApprovalRegistry,
        handlers: HandlerRegistry,
        store: ExecutionStore,
        clock: Clock,
        observer: ExecutionObserver,
    ) -> None:
        self._tools = tools
        self._models = models
        self._knowledge = knowledge
        self._assets = assets
        self._approvals = approvals
        self._handlers = handlers
        self._store = store
        self._clock = clock
        self._observer = observer

    async def run(self, record: ExecutionRecord, context: ExecutionContext) -> ExecutionRecord:
        if record._sealed and record.status not in {ExecutionStatus.PENDING, ExecutionStatus.RUNNING}:
            return record
        try:
            record.seal_snapshot(context.snapshot)
            if context.cancellation.cancelled:
                self._apply_failure(
                    record,
                    None,
                    RuntimeFailure(RuntimeErrorCode.CANCELLED, "The execution was cancelled."),
                    outputs={},
                )
                return record
            record.status = ExecutionStatus.RUNNING
            record.started_at = self._clock.now()
            record.steps = [
                StepRecord(step_key=step.step_key, position=step.position, status=StepStatus.PENDING)
                for step in context.snapshot.steps
            ]
            self._observer.observe(self._event(record, "execution.started"))
            state: dict[str, Any] = {"input": dict(context.payload)}
            outputs: dict[str, Any] = {}
            deadline = time.monotonic() + context.snapshot.execution_policy.timeout_seconds
            for index, step in enumerate(context.snapshot.steps):
                step_record = record.steps[index]
                try:
                    result = await self._run_step(record, context, step, step_record, state, deadline)
                except RuntimeFailure as failure:
                    self._apply_failure(record, step_record, failure, outputs, record.steps[index + 1 :])
                    return record
                except asyncio.CancelledError:
                    self._apply_failure(
                        record,
                        step_record,
                        RuntimeFailure(
                            RuntimeErrorCode.CANCELLED,
                            "The execution was cancelled.",
                            step_key=step.step_key,
                        ),
                        outputs,
                        record.steps[index + 1 :],
                    )
                    raise
                except Exception:
                    self._apply_failure(
                        record,
                        step_record,
                        RuntimeFailure(
                            RuntimeErrorCode.INTERNAL_ERROR,
                            SAFE_INTERNAL_MESSAGE,
                            step_key=step.step_key,
                        ),
                        outputs,
                        record.steps[index + 1 :],
                    )
                    return record
                outputs[step.step_key] = result
                state[step.step_key] = result
                state["output"] = result
                record.output = {"steps": dict(outputs)}
                step_record.status = StepStatus.COMPLETED
                step_record.completed_at = self._clock.now()
                self._observer.observe(self._event(record, "step.finished", step=step_record))
            record.status = ExecutionStatus.COMPLETED
            record.completed_at = self._clock.now()
            record.error = None
            record.output = {"steps": outputs, "result": outputs[context.snapshot.steps[-1].step_key] if outputs else {}}
            return record
        except RuntimeFailure as failure:
            self._apply_failure(record, None, failure, outputs={})
            return record
        finally:
            self._store.save(record)
            self._observer.observe(self._event(record, "execution.finished"))

    async def _run_step(
        self,
        record: ExecutionRecord,
        context: ExecutionContext,
        step: WorkflowStep,
        step_record: StepRecord,
        state: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        if context.cancellation.cancelled:
            raise RuntimeFailure(
                RuntimeErrorCode.CANCELLED,
                "The execution was cancelled.",
                step_key=step.step_key,
            )
        step_record.status = StepStatus.RUNNING
        step_record.started_at = self._clock.now()
        self._observer.observe(self._event(record, "step.started", step=step_record))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeFailure(
                RuntimeErrorCode.TIMEOUT,
                "The execution timed out.",
                retryable=True,
                step_key=step.step_key,
            )
        limit = remaining
        step_budget = context.snapshot.execution_policy.step_timeout_seconds
        if step_budget is not None:
            limit = min(limit, step_budget)

        async def call() -> dict[str, Any]:
            view = isolate_mapping(state)
            produced = await self._perform(context, step, view)
            return coerce_result(produced, step)

        return await self._run_limited(call, context, step, limit)

    async def _perform(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if step.side_effect.value == "external_effect" or step.kind == StepKind.EFFECT:
            self._gate_effect(ctx, step, state)
        if step.kind == StepKind.LOAD_CONTEXT:
            return await self._load_context(ctx, step)
        if step.kind == StepKind.REASON:
            return await self._reason(ctx, step, state)
        if step.kind == StepKind.INVOKE_TOOL:
            return await self._invoke_tool(ctx, step, state)
        if step.kind == StepKind.RESOLVE_ASSET:
            return await self._resolve_asset(ctx, step, state)
        if step.kind == StepKind.DECIDE_APPROVAL:
            decision = self._decision(ctx, step, state)
            return {
                "allowed": decision.allowed,
                "reason": decision.reason,
                "requires_human": decision.requires_human,
            }
        if step.kind in {StepKind.VALIDATE, StepKind.STORE, StepKind.HUMAN_DECISION, StepKind.EFFECT}:
            return await self._call_handler(ctx, step, state)
        raise RuntimeFailure(
            RuntimeErrorCode.INVALID_REQUEST,
            "Step kind is not supported.",
            step_key=step.step_key,
        )

    def _gate_effect(self, ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> None:
        if ctx.snapshot.agent_version_status == VersionStatus.DRAFT:
            raise RuntimeFailure(
                RuntimeErrorCode.DRAFT_CANNOT_PUBLISH,
                "Draft agents cannot publish.",
                step_key=step.step_key,
            )
        self._decision(ctx, step, state, enforce=True)

    def _decision(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
        *,
        enforce: bool = False,
    ) -> ApprovalDecision:
        policy_id = ctx.snapshot.approval_policy_id
        if not policy_id:
            raise RuntimeFailure(
                RuntimeErrorCode.APPROVAL_POLICY_NOT_FOUND,
                "Approval policy is not registered.",
                step_key=step.step_key,
            )
        policy = self._approvals.get(policy_id)
        if policy is None:
            raise RuntimeFailure(
                RuntimeErrorCode.APPROVAL_POLICY_NOT_FOUND,
                "Approval policy is not registered.",
                step_key=step.step_key,
            )
        decision = policy.evaluate(ctx, step, state)
        if not isinstance(decision, ApprovalDecision):
            raise RuntimeFailure(
                RuntimeErrorCode.STEP_FAILED,
                "Approval policy returned an invalid decision.",
                step_key=step.step_key,
            )
        if enforce and decision.requires_human:
            raise RuntimeFailure(
                RuntimeErrorCode.APPROVAL_REQUIRED,
                decision.reason,
                step_key=step.step_key,
            )
        if enforce and not decision.allowed:
            raise RuntimeFailure(
                RuntimeErrorCode.APPROVAL_DENIED,
                decision.reason,
                step_key=step.step_key,
            )
        return decision

    async def _load_context(self, ctx: ExecutionContext, step: WorkflowStep) -> Mapping[str, Any]:
        source = step.knowledge_source or ""
        if source not in ctx.snapshot.knowledge_sources:
            raise RuntimeFailure(
                RuntimeErrorCode.KNOWLEDGE_NOT_GRANTED,
                "Knowledge source is not granted.",
                step_key=step.step_key,
            )
        return await self._knowledge.load(ctx, source)

    async def _reason(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        role = step.model_role or ""
        if role not in ctx.snapshot.model_roles:
            raise RuntimeFailure(
                RuntimeErrorCode.ROLE_NOT_GRANTED,
                "Model role is not granted.",
                step_key=step.step_key,
            )
        return await self._models.complete(ctx, role, state)

    async def _invoke_tool(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        tool_name = step.tool_name or ""
        if tool_name not in ctx.snapshot.tool_grants:
            raise RuntimeFailure(
                RuntimeErrorCode.TOOL_NOT_GRANTED,
                "Tool is not granted.",
                step_key=step.step_key,
            )
        incoming = state.get("input")
        arguments = incoming if isinstance(incoming, dict) else {}
        return await self._tools.invoke(ctx, tool_name, arguments)

    async def _resolve_asset(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        role = step.asset_role or ""
        if role not in ctx.snapshot.asset_roles:
            raise RuntimeFailure(
                RuntimeErrorCode.ASSET_NOT_GRANTED,
                "Asset role is not granted.",
                step_key=step.step_key,
            )
        asset_id = None
        incoming = state.get("input")
        if isinstance(incoming, dict) and isinstance(incoming.get("asset_id"), str):
            asset_id = incoming["asset_id"]
        return await self._assets.resolve(ctx, role, asset_id)

    async def _call_handler(
        self,
        ctx: ExecutionContext,
        step: WorkflowStep,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        handler_id = step.handler_id
        if not handler_id:
            raise RuntimeFailure(
                RuntimeErrorCode.HANDLER_NOT_REGISTERED,
                "Step handler is not registered.",
                step_key=step.step_key,
            )
        handler = self._handlers.get(handler_id)
        if handler is None:
            raise RuntimeFailure(
                RuntimeErrorCode.HANDLER_NOT_REGISTERED,
                "Step handler is not registered.",
                step_key=step.step_key,
            )
        outcome = handler(ctx, step, state)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if not isinstance(outcome, Mapping):
            raise RuntimeFailure(
                RuntimeErrorCode.STEP_FAILED,
                "Step handler returned an invalid result.",
                step_key=step.step_key,
            )
        return outcome

    async def _run_limited(
        self,
        call: Callable[[], Awaitable[dict[str, Any]]],
        ctx: ExecutionContext,
        step: WorkflowStep,
        limit: float,
    ) -> dict[str, Any]:
        task = asyncio.create_task(call())
        waiter = asyncio.create_task(ctx.cancellation.wait())
        try:
            done, _pending = await asyncio.wait(
                {task, waiter},
                timeout=limit,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            task.cancel()
            waiter.cancel()
            raise
        if task in done:
            if not waiter.done():
                waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if not waiter.done():
            waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter
        if ctx.cancellation.cancelled:
            raise RuntimeFailure(
                RuntimeErrorCode.CANCELLED,
                "The execution was cancelled.",
                step_key=step.step_key,
            )
        raise RuntimeFailure(
            RuntimeErrorCode.TIMEOUT,
            "The execution timed out.",
            retryable=True,
            step_key=step.step_key,
        )

    def _apply_failure(
        self,
        record: ExecutionRecord,
        step_record: StepRecord | None,
        failure: RuntimeFailure,
        outputs: dict[str, Any],
        following: list[StepRecord] | None = None,
    ) -> None:
        error = failure.to_error(step_key=None if step_record is None else step_record.step_key)
        if step_record is not None:
            if step_record.status == StepStatus.PENDING:
                step_record.started_at = step_record.started_at or self._clock.now()
            step_record.status = _STEP_STATUS.get(failure.code, StepStatus.FAILED)
            step_record.error = error
            if failure.code != RuntimeErrorCode.APPROVAL_REQUIRED:
                step_record.completed_at = self._clock.now()
            self._observer.observe(self._event(record, "step.finished", step=step_record, error_code=error.code.value))
        for item in following or []:
            if item.status == StepStatus.PENDING:
                item.status = StepStatus.SKIPPED
        record.status = _EXEC_STATUS.get(failure.code, ExecutionStatus.FAILED)
        record.error = error
        record.output = {"steps": dict(outputs)}
        if record.status != ExecutionStatus.WAITING_APPROVAL:
            record.completed_at = self._clock.now()

    def _event(
        self,
        record: ExecutionRecord,
        name: str,
        *,
        step: StepRecord | None = None,
        error_code: str | None = None,
    ) -> RuntimeEvent:
        return RuntimeEvent(
            name=name,
            execution_id=record.execution_id,
            correlation_id=record.correlation_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            agent_key=record.agent_key,
            agent_version_number=record.agent_version_number,
            workflow_key=record.workflow_key,
            workflow_version_number=record.workflow_version_number,
            status=step.status.value if step is not None and name.startswith("step.") else record.status.value,
            trigger_key=record.trigger_key,
            step_key=None if step is None else step.step_key,
            error_code=error_code,
        )


def coerce_result(value: Any, step: WorkflowStep) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeFailure(
            RuntimeErrorCode.STEP_FAILED,
            "Step handler returned an invalid result.",
            step_key=step.step_key,
        )
    try:
        return sanitize_payload(value)
    except ValueError as exc:
        raise RuntimeFailure(
            RuntimeErrorCode.STEP_FAILED,
            "Step handler returned an invalid result.",
            step_key=step.step_key,
        ) from exc
