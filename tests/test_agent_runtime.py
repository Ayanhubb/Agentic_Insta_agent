"""Unit tests for the generic agent runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from agent_runtime.catalog import InMemoryCatalog
from agent_runtime.errors import RuntimeErrorCode, RuntimeFailure
from agent_runtime.models import (
    AgentDefinition,
    AgentStatus,
    AgentVersion,
    ApprovalDecision,
    CancellationToken,
    ExecutionContext,
    ExecutionMode,
    ExecutionPolicy,
    ExecutionStatus,
    OwnerKind,
    StepKind,
    StepStatus,
    TriggerBinding,
    TriggerContext,
    VersionStatus,
    WorkflowPin,
    WorkflowStep,
    WorkflowVersion,
    SideEffect,
    freeze_payload,
)
from agent_runtime.resolver import ResolvedRun
from agent_runtime.runtime import AgentRuntime
from agent_runtime.services import ApprovalRegistry, HandlerRegistry


class FixedClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 9, 25, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current


class SequenceIds:
    def __init__(self) -> None:
        self._n = 0

    def execution_id(self) -> str:
        self._n += 1
        return f"exec_{self._n}"

    def correlation_id(self) -> str:
        self._n += 1
        return f"corr_{self._n}"


class ListObserver:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def observe(self, event: Any) -> None:
        self._events.append(event)


class RecordingTools:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def invoke(self, ctx: ExecutionContext, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(f"tool:{tool_name}")
        return {"tool": tool_name, "tenant": ctx.tenant_id}


class RecordingModels:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def complete(self, ctx: ExecutionContext, role: str, state: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append(f"model:{role}")
        return {"role": role}


class RecordingKnowledge:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def load(self, ctx: ExecutionContext, source_name: str) -> dict[str, Any]:
        self.calls.append(f"knowledge:{source_name}")
        return {"source": source_name}


class RecordingAssets:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def resolve(self, ctx: ExecutionContext, role: str, asset_id: str | None) -> dict[str, Any]:
        self.calls.append(f"asset:{role}")
        return {"role": role, "asset_id": asset_id}


class DecisionPolicy:
    def __init__(self, *, allowed: bool, requires_human: bool = False, seen: list[str] | None = None) -> None:
        self.allowed = allowed
        self.requires_human = requires_human
        self.seen = seen

    def evaluate(self, ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> ApprovalDecision:
        if self.seen is not None:
            self.seen.append(step.step_key)
        reason = "needs a person" if self.requires_human else "allowed" if self.allowed else "denied"
        return ApprovalDecision(allowed=self.allowed, reason=reason, requires_human=self.requires_human)


def _ordered(steps: list[WorkflowStep]) -> tuple[WorkflowStep, ...]:
    return tuple(step.model_copy(update={"position": index}) for index, step in enumerate(steps, start=1))


def _workflow(
    steps: list[WorkflowStep],
    *,
    key: str = "demo.flow",
    number: int = 1,
    status: VersionStatus = VersionStatus.PUBLISHED,
    policy_id: str | None = "review",
) -> WorkflowVersion:
    ordered = _ordered(steps)
    effects = tuple(dict.fromkeys(step.side_effect for step in ordered if step.side_effect != SideEffect.NONE))
    return WorkflowVersion(
        workflow_key=key,
        version_number=number,
        status=status,
        steps=ordered,
        approval_policy_id=policy_id,
        side_effects=effects,
    )


def _version(
    workflow: WorkflowVersion,
    *,
    number: int = 1,
    status: VersionStatus = VersionStatus.PUBLISHED,
    policy_id: str | None = "review",
    timeout: float = 2,
    tools: frozenset[str] = frozenset(),
    roles: frozenset[str] = frozenset(),
    knowledge: frozenset[str] = frozenset(),
    assets: frozenset[str] = frozenset(),
    trigger_key: str = "manual",
) -> AgentVersion:
    return AgentVersion(
        version_number=number,
        status=status,
        workflow_pins=(WorkflowPin(workflow_key=workflow.workflow_key, version_number=workflow.version_number),),
        trigger_bindings=(TriggerBinding(trigger_key=trigger_key, workflow_key=workflow.workflow_key),),
        approval_policy_id=policy_id,
        execution_policy=ExecutionPolicy(timeout_seconds=timeout),
        tool_grants=tools,
        model_roles=roles,
        knowledge_sources=knowledge,
        asset_roles=assets,
    )


def _definition(
    *,
    key: str = "demo",
    status: AgentStatus = AgentStatus.ACTIVE,
    owner: OwnerKind = OwnerKind.SYSTEM,
    user_id: str | None = None,
    published: int | None = 1,
) -> AgentDefinition:
    return AgentDefinition(
        key=key,
        owner=owner,
        status=status,
        display_name="Demo",
        user_id=user_id,
        published_version_number=published,
    )


def _trigger(**overrides: Any) -> TriggerContext:
    payload = {
        "trigger_key": "manual",
        "tenant_id": "tenant-1",
        "user_id": "tenant-1",
        "mode": ExecutionMode.PRODUCTION,
    }
    payload.update(overrides)
    return TriggerContext(**payload)


class Harness:
    def __init__(self) -> None:
        self.catalog = InMemoryCatalog()
        self.handlers = HandlerRegistry()
        self.approvals = ApprovalRegistry()
        self.calls: list[str] = []
        self.events: list[Any] = []
        self.clock = FixedClock()
        self.ids = SequenceIds()
        self.tools = RecordingTools(self.calls)
        self.models = RecordingModels(self.calls)
        self.knowledge = RecordingKnowledge(self.calls)
        self.assets = RecordingAssets(self.calls)
        self.runtime = AgentRuntime(
            catalog=self.catalog,
            handlers=self.handlers,
            tools=self.tools,
            models=self.models,
            knowledge=self.knowledge,
            assets=self.assets,
            approvals=self.approvals,
            clock=self.clock,
            ids=self.ids,
            observer=ListObserver(self.events),
        )

    def bind(self, handler_id: str, fn: Any | None = None) -> None:
        async def default(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
            self.calls.append(handler_id)
            return {"step": step.step_key, "tenant": ctx.tenant_id}

        self.handlers.register(handler_id, default if fn is None else fn)

    def allow(
        self,
        policy_id: str = "review",
        *,
        allowed: bool = True,
        requires_human: bool = False,
        seen: list[str] | None = None,
    ) -> DecisionPolicy:
        policy = DecisionPolicy(allowed=allowed, requires_human=requires_human, seen=seen)
        self.approvals.register(policy_id, policy)
        return policy

    def replace_runtime(self, approvals: ApprovalRegistry) -> None:
        self.approvals = approvals
        self.runtime = AgentRuntime(
            catalog=self.catalog,
            handlers=self.handlers,
            tools=self.tools,
            models=self.models,
            knowledge=self.knowledge,
            assets=self.assets,
            approvals=approvals,
            clock=self.clock,
            ids=self.ids,
            observer=ListObserver(self.events),
        )

    def register(self, definition: AgentDefinition, version: AgentVersion, workflow: WorkflowVersion) -> None:
        self.catalog.save_workflow(workflow)
        self.catalog.save_definition(definition.model_copy(update={"published_version_number": None}))
        self.catalog.save_version(definition, version)
        if definition.published_version_number is not None:
            self.catalog.save_definition(definition)


def _echo_step(key: str = "echo", handler: str = "echo") -> WorkflowStep:
    return WorkflowStep(step_key=key, position=1, kind=StepKind.VALIDATE, handler_id=handler)


def _effect_step(key: str = "effect", handler: str = "effect") -> WorkflowStep:
    return WorkflowStep(
        step_key=key,
        position=1,
        kind=StepKind.EFFECT,
        handler_id=handler,
        side_effect=SideEffect.EXTERNAL_EFFECT,
    )


@pytest.fixture
def harness() -> Harness:
    box = Harness()
    box.policy = box.allow(seen=[])
    box.bind("echo")
    box.bind("effect")
    return box


@pytest.mark.asyncio
async def test_production_run_records_identity_and_step_status(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    version = _version(workflow)
    definition = _definition()
    harness.register(definition, version, workflow)

    record = await harness.runtime.run(
        "demo",
        _trigger(correlation_id="corr-fixed", idempotency_key="job-1", payload={"topic": "launch"}),
    )

    assert record.status == ExecutionStatus.COMPLETED
    assert record.execution_id == "exec_1"
    assert record.correlation_id == "corr-fixed"
    assert record.tenant_id == "tenant-1"
    assert record.user_id == "tenant-1"
    assert record.trigger_key == "manual"
    assert record.agent_key == "demo"
    assert record.agent_version_number == 1
    assert record.agent_version_status == VersionStatus.PUBLISHED
    assert record.workflow_key == "demo.flow"
    assert record.workflow_version_number == 1
    assert record.idempotency_key == "job-1"
    assert record.steps[0].status == StepStatus.COMPLETED
    assert record.output["result"]["step"] == "echo"
    assert record.error is None
    assert harness.calls == ["echo"]
    body = json.dumps(record.public_dict())
    assert "exec_1" in body
    names = [event.name for event in harness.events]
    assert "execution.started" in names
    assert "execution.finished" in names


@pytest.mark.asyncio
async def test_production_rejects_draft_and_does_not_run_steps(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    version = _version(workflow, status=VersionStatus.DRAFT)
    definition = _definition(published=None)
    harness.register(definition, version, workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.status == ExecutionStatus.FAILED
    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.VERSION_NOT_PUBLISHED
    assert harness.calls == []
    assert definition.published_version_number is None


@pytest.mark.asyncio
async def test_production_uses_published_pointer_not_a_higher_draft(harness: Harness) -> None:
    current = _workflow([_echo_step(handler="echo")], number=1)
    draft_flow = _workflow([_echo_step(key="next", handler="effect")], key="demo.flow", number=2)
    published = _version(current, number=1)
    draft = _version(draft_flow, number=2, status=VersionStatus.DRAFT)
    definition = _definition(published=1)
    harness.register(definition, published, current)
    harness.catalog.save_workflow(draft_flow)
    harness.catalog.save_version(definition, draft)

    record = await harness.runtime.run("demo", _trigger())

    assert record.agent_version_number == 1
    assert record.workflow_version_number == 1
    assert harness.calls == ["echo"]


@pytest.mark.asyncio
async def test_production_rejects_explicit_draft_version(harness: Harness) -> None:
    current = _workflow([_echo_step()], number=1)
    draft_flow = _workflow([_effect_step()], number=2)
    harness.register(_definition(published=1), _version(current, number=1), current)
    harness.catalog.save_workflow(draft_flow)
    harness.catalog.save_version(_definition(published=1), _version(draft_flow, number=2, status=VersionStatus.DRAFT))

    record = await harness.runtime.run("demo", _trigger(requested_version_number=2))

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.DRAFT_NOT_ALLOWED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_production_rejects_a_version_that_is_not_the_pointer(harness: Harness) -> None:
    first = _workflow([_echo_step(handler="echo")], number=1)
    second = _workflow([_echo_step(key="later", handler="effect")], number=2)
    definition = _definition(published=1)
    harness.register(definition, _version(first, number=1), first)
    harness.catalog.save_workflow(second)
    harness.catalog.save_version(definition, _version(second, number=2))
    harness.catalog.save_definition(_definition(published=1))

    record = await harness.runtime.run("demo", _trigger(requested_version_number=2))

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.PUBLISHED_POINTER_REQUIRED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_draft_test_runs_until_publish_and_cannot_publish(harness: Harness) -> None:
    workflow = _workflow([_echo_step(), _effect_step(), _echo_step(key="after", handler="echo")])
    version = _version(workflow, status=VersionStatus.DRAFT)
    definition = _definition(published=None)
    harness.register(definition, version, workflow)

    record = await harness.runtime.run("demo", _trigger(mode=ExecutionMode.TEST, requested_version_number=1))

    assert record.status == ExecutionStatus.FAILED
    assert record.agent_version_status == VersionStatus.DRAFT
    assert record.steps[0].status == StepStatus.COMPLETED
    assert record.steps[1].status == StepStatus.BLOCKED
    assert record.steps[1].error is not None
    assert record.steps[1].error.code == RuntimeErrorCode.DRAFT_CANNOT_PUBLISH
    assert record.steps[2].status == StepStatus.SKIPPED
    assert harness.calls == ["echo"]
    assert harness.policy.seen == []
    stored = harness.catalog.get_version(definition, 1)
    assert stored is not None
    assert stored.status == VersionStatus.DRAFT
    assert harness.catalog.get_definition("demo", tenant_id="tenant-1").published_version_number is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_published_test_can_perform_an_approved_effect(harness: Harness) -> None:
    workflow = _workflow([_effect_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger(mode=ExecutionMode.TEST))

    assert record.status == ExecutionStatus.COMPLETED
    assert harness.calls == ["effect"]


@pytest.mark.asyncio
async def test_approval_denial_does_not_call_the_effect(harness: Harness) -> None:
    denied = ApprovalRegistry()
    denied.register("review", DecisionPolicy(allowed=False))
    harness.replace_runtime(denied)
    workflow = _workflow([_echo_step(), _effect_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.status == ExecutionStatus.FAILED
    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.APPROVAL_DENIED
    assert record.steps[0].status == StepStatus.COMPLETED
    assert record.steps[1].status == StepStatus.FAILED
    assert harness.calls == ["echo"]


@pytest.mark.asyncio
async def test_human_approval_waits_and_does_not_publish(harness: Harness) -> None:
    approvals = ApprovalRegistry()
    approvals.register("review", DecisionPolicy(allowed=False, requires_human=True))
    harness.replace_runtime(approvals)
    workflow = _workflow([_effect_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.status == ExecutionStatus.WAITING_APPROVAL
    assert record.completed_at is None
    assert record.steps[0].status == StepStatus.WAITING
    assert harness.calls == []


@pytest.mark.asyncio
async def test_missing_policy_fails_closed(harness: Harness) -> None:
    workflow = _workflow([_effect_step()], policy_id=None)
    version = _version(workflow, policy_id=None)
    harness.register(_definition(), version, workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.APPROVAL_POLICY_NOT_FOUND
    assert harness.calls == []


@pytest.mark.asyncio
async def test_grants_are_enforced_before_services(harness: Harness) -> None:
    steps = [
        WorkflowStep(step_key="read", position=1, kind=StepKind.LOAD_CONTEXT, knowledge_source="notes"),
        WorkflowStep(step_key="think", position=2, kind=StepKind.REASON, model_role="writer", side_effect=SideEffect.PROVIDER_CALL),
        WorkflowStep(step_key="act", position=3, kind=StepKind.INVOKE_TOOL, tool_name="lookup"),
        WorkflowStep(step_key="file", position=4, kind=StepKind.RESOLVE_ASSET, asset_role="logo"),
    ]
    workflow = _workflow(steps, policy_id=None)
    version = _version(workflow, policy_id=None)
    harness.register(_definition(), version, workflow)

    record = await harness.runtime.run("demo", _trigger(payload={"asset_id": "asset-1"}))

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.KNOWLEDGE_NOT_GRANTED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_granted_services_run_in_order(harness: Harness) -> None:
    steps = [
        WorkflowStep(step_key="read", position=1, kind=StepKind.LOAD_CONTEXT, knowledge_source="notes"),
        WorkflowStep(step_key="think", position=2, kind=StepKind.REASON, model_role="writer", side_effect=SideEffect.PROVIDER_CALL),
        WorkflowStep(step_key="act", position=3, kind=StepKind.INVOKE_TOOL, tool_name="lookup"),
        WorkflowStep(step_key="file", position=4, kind=StepKind.RESOLVE_ASSET, asset_role="logo"),
    ]
    workflow = _workflow(steps, policy_id=None)
    version = _version(
        workflow,
        policy_id=None,
        knowledge=frozenset({"notes"}),
        roles=frozenset({"writer"}),
        tools=frozenset({"lookup"}),
        assets=frozenset({"logo"}),
    )
    harness.register(_definition(), version, workflow)

    record = await harness.runtime.run("demo", _trigger(payload={"asset_id": "asset-1"}))

    assert record.status == ExecutionStatus.COMPLETED
    assert harness.calls == ["knowledge:notes", "model:writer", "tool:lookup", "asset:logo"]
    assert record.output["result"]["asset_id"] == "asset-1"


@pytest.mark.asyncio
async def test_unknown_handler_is_not_imported(harness: Harness) -> None:
    workflow = _workflow([_echo_step(handler="missing")])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.HANDLER_NOT_REGISTERED
    assert harness.calls == []


def test_handler_id_cannot_be_a_module_path() -> None:
    with pytest.raises(ValidationError):
        WorkflowStep(step_key="bad", position=1, kind=StepKind.VALIDATE, handler_id="os.system")


def test_payload_cannot_carry_code() -> None:
    with pytest.raises(ValidationError):
        _trigger(payload={"script": "print(1)"})


@pytest.mark.asyncio
async def test_payload_cannot_override_tenant_or_keep_secrets(harness: Harness) -> None:
    seen: dict[str, Any] = {}

    async def capture(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        seen["tenant"] = ctx.tenant_id
        seen["user"] = ctx.user_id
        seen["input"] = dict(state["input"])
        return {"ok": True}

    harness.bind("capture", capture)
    workflow = _workflow([_echo_step(handler="capture")])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run(
        "demo",
        _trigger(payload={"topic": "launch", "tenant_id": "other", "access_token": "secret-token", "note": "sk-abcdefghijklmnopqrstuvwxyz"}),
    )

    assert record.status == ExecutionStatus.COMPLETED
    assert seen["tenant"] == "tenant-1"
    assert seen["user"] == "tenant-1"
    assert "tenant_id" not in seen["input"]
    assert "access_token" not in seen["input"]
    assert "secret-token" not in json.dumps(seen)
    assert "[REDACTED]" in seen["input"]["note"]


@pytest.mark.asyncio
async def test_unexpected_exception_is_a_safe_internal_error(harness: Harness) -> None:
    async def boom(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError("access_token=super-secret-value")

    harness.bind("boom", boom)
    workflow = _workflow([_echo_step(handler="boom"), _echo_step(key="later", handler="echo")])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.status == ExecutionStatus.FAILED
    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.INTERNAL_ERROR
    assert record.steps[1].status == StepStatus.SKIPPED
    rendered = json.dumps(record.public_dict())
    assert "super-secret" not in rendered
    assert "RuntimeError" not in rendered


@pytest.mark.asyncio
async def test_step_failure_stops_the_workflow(harness: Harness) -> None:
    async def reject(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeFailure(RuntimeErrorCode.STEP_FAILED, "The plan was rejected.")

    harness.bind("reject", reject)
    workflow = _workflow([_echo_step(), _echo_step(key="check", handler="reject"), _echo_step(key="later", handler="echo")])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.steps[0].status == StepStatus.COMPLETED
    assert record.steps[1].status == StepStatus.FAILED
    assert record.steps[1].error is not None
    assert record.steps[1].error.code == RuntimeErrorCode.STEP_FAILED
    assert record.steps[2].status == StepStatus.SKIPPED
    assert harness.calls == ["echo"]


@pytest.mark.asyncio
async def test_idempotent_replay_does_not_run_again(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)
    trigger = _trigger(idempotency_key="job-9")

    first = await harness.runtime.run("demo", trigger)
    second = await harness.runtime.run("demo", trigger)

    assert first.execution_id == second.execution_id
    assert second.status == ExecutionStatus.COMPLETED
    assert harness.calls == ["echo"]


@pytest.mark.asyncio
async def test_idempotent_failure_stays_failed_after_the_agent_is_resumed(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    definition = _definition(status=AgentStatus.PAUSED)
    harness.register(definition, _version(workflow), workflow)
    trigger = _trigger(idempotency_key="job-paused")

    first = await harness.runtime.run("demo", trigger)
    harness.catalog.save_definition(_definition(status=AgentStatus.ACTIVE))
    second = await harness.runtime.run("demo", trigger)

    assert first.error is not None
    assert first.error.code == RuntimeErrorCode.AGENT_PAUSED
    assert second.execution_id == first.execution_id
    assert second.error is not None
    assert second.error.code == RuntimeErrorCode.AGENT_PAUSED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_parallel_idempotency_runs_the_handler_once(harness: Harness) -> None:
    started = asyncio.Event()

    async def slow(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        harness.calls.append("slow")
        started.set()
        await asyncio.sleep(0.2)
        return {"ok": True}

    harness.bind("slow", slow)
    workflow = _workflow([_echo_step(handler="slow")])
    harness.register(_definition(), _version(workflow), workflow)
    trigger = _trigger(idempotency_key="job-parallel")

    first, second = await asyncio.gather(
        harness.runtime.run("demo", trigger),
        harness.runtime.run("demo", trigger),
    )

    assert first.execution_id == second.execution_id
    assert first.status == ExecutionStatus.COMPLETED
    assert harness.calls == ["slow"]
    assert started.is_set()


@pytest.mark.asyncio
async def test_idempotency_key_is_scoped_to_the_tenant(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)

    first = await harness.runtime.run("demo", _trigger(tenant_id="tenant-1", user_id="tenant-1", idempotency_key="shared"))
    second = await harness.runtime.run("demo", _trigger(tenant_id="tenant-2", user_id="tenant-2", idempotency_key="shared"))

    assert first.execution_id != second.execution_id
    assert harness.calls == ["echo", "echo"]


@pytest.mark.asyncio
async def test_cancellation_during_a_step(harness: Harness) -> None:
    token = CancellationToken()
    started = asyncio.Event()

    async def slow(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        harness.calls.append("entered")
        started.set()
        await asyncio.sleep(2)
        harness.calls.append("finished")
        return {"ok": True}

    harness.bind("slow", slow)
    workflow = _workflow([_echo_step(handler="slow"), _echo_step(key="later")])
    harness.register(_definition(), _version(workflow), workflow)

    task = asyncio.create_task(harness.runtime.run("demo", _trigger(), cancellation=token))
    await asyncio.wait_for(started.wait(), timeout=1)
    token.cancel()
    record = await task

    assert record.status == ExecutionStatus.CANCELLED
    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.CANCELLED
    assert record.steps[0].status == StepStatus.CANCELLED
    assert record.steps[1].status == StepStatus.SKIPPED
    assert harness.calls == ["entered"]


@pytest.mark.asyncio
async def test_cancellation_before_start_does_not_run(harness: Harness) -> None:
    token = CancellationToken()
    token.cancel()
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger(idempotency_key="cancel-early"), cancellation=token)

    assert record.status == ExecutionStatus.CANCELLED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_timeout_marks_the_step_and_skips_the_rest(harness: Harness) -> None:
    async def slow(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        harness.calls.append("slow")
        await asyncio.sleep(1)
        return {"ok": True}

    harness.bind("slow", slow)
    workflow = _workflow([_echo_step(handler="slow"), _echo_step(key="later")])
    version = _version(workflow, timeout=0.05)
    harness.register(_definition(), version, workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.status == ExecutionStatus.TIMED_OUT
    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.TIMEOUT
    assert record.error.retryable is True
    assert record.steps[0].status == StepStatus.TIMED_OUT
    assert record.steps[1].status == StepStatus.SKIPPED


@pytest.mark.asyncio
async def test_snapshot_stays_immutable_when_the_catalog_changes(harness: Harness) -> None:
    async def mutate(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        with pytest.raises(ValidationError):
            ctx.snapshot.agent_version_number = 9  # type: ignore[misc]
        replacement = _workflow([_echo_step(key="other", handler="effect")], number=2)
        harness.catalog.save_workflow(replacement)
        current = harness.catalog.get_definition("demo", tenant_id="tenant-1")
        assert current is not None
        emptied = _version(
            _workflow([_echo_step()]),
            number=9,
            status=VersionStatus.DRAFT,
            tools=frozenset(),
        )
        harness.catalog.save_version(current, emptied)
        harness.calls.append("mutated")
        return {"step": step.step_key}

    harness.bind("mutate", mutate)
    steps = [
        _echo_step(key="first", handler="mutate"),
        WorkflowStep(step_key="act", position=2, kind=StepKind.INVOKE_TOOL, tool_name="lookup"),
    ]
    workflow = _workflow(steps, policy_id=None)
    version = _version(workflow, policy_id=None, tools=frozenset({"lookup"}))
    harness.register(_definition(), version, workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.status == ExecutionStatus.COMPLETED
    assert record.agent_version_number == 1
    assert [step.step_key for step in record.steps] == ["first", "act"]
    assert "tool:lookup" in harness.calls
    with pytest.raises(RuntimeFailure):
        record.seal_snapshot(record.snapshot)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_paused_and_retired_do_not_run(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    version = _version(workflow)
    harness.register(_definition(status=AgentStatus.PAUSED), version, workflow)
    paused = await harness.runtime.run("demo", _trigger())
    assert paused.error is not None
    assert paused.error.code == RuntimeErrorCode.AGENT_PAUSED

    harness.catalog.save_definition(_definition(status=AgentStatus.RETIRED))
    retired = await harness.runtime.run("demo", _trigger(idempotency_key="other-job"))
    assert retired.error is not None
    assert retired.error.code == RuntimeErrorCode.AGENT_RETIRED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_user_agent_is_invisible_to_another_tenant(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    definition = _definition(owner=OwnerKind.USER, user_id="user-a", published=1)
    harness.register(definition, _version(workflow), workflow)

    missing = await harness.runtime.run("demo", _trigger(tenant_id="user-b", user_id="user-b"))
    mismatched = await harness.runtime.run(
        "demo",
        _trigger(tenant_id="user-a", user_id="user-b", idempotency_key="mismatch"),
    )
    owned = await harness.runtime.run(
        "demo",
        _trigger(tenant_id="user-a", user_id="user-a", idempotency_key="owned"),
    )

    assert missing.error is not None
    assert missing.error.code == RuntimeErrorCode.AGENT_NOT_FOUND
    assert mismatched.error is not None
    assert mismatched.error.code == RuntimeErrorCode.AGENT_NOT_FOUND
    assert owned.status == ExecutionStatus.COMPLETED
    assert harness.calls == ["echo"]


@pytest.mark.asyncio
async def test_same_key_is_isolated_per_tenant(harness: Harness) -> None:
    async def owned(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        harness.calls.append(ctx.tenant_id)
        return {"tenant": ctx.tenant_id}

    harness.bind("owned", owned)
    workflow = _workflow([_echo_step(handler="owned")])
    harness.catalog.save_workflow(workflow)
    for user in ("user-a", "user-b"):
        definition = _definition(key="helper", owner=OwnerKind.USER, user_id=user, published=1)
        harness.catalog.save_definition(definition.model_copy(update={"published_version_number": None}))
        harness.catalog.save_version(definition, _version(workflow))
        harness.catalog.save_definition(definition)

    await harness.runtime.run("helper", _trigger(tenant_id="user-a", user_id="user-a"))
    await harness.runtime.run("helper", _trigger(tenant_id="user-b", user_id="user-b"))

    assert harness.calls == ["user-a", "user-b"]


@pytest.mark.asyncio
async def test_system_agent_runs_for_any_tenant(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger(tenant_id="tenant-9", user_id="tenant-9"))

    assert record.status == ExecutionStatus.COMPLETED
    assert record.tenant_id == "tenant-9"


@pytest.mark.asyncio
async def test_unpinned_trigger_does_not_run(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger(trigger_key="other.trigger"))

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.WORKFLOW_NOT_PINNED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_unpublished_workflow_is_rejected(harness: Harness) -> None:
    workflow = _workflow([_echo_step()], status=VersionStatus.DRAFT)
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.VERSION_NOT_PUBLISHED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_invalid_handler_result_fails_the_step(harness: Harness) -> None:
    async def bad(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> str:
        return "not-a-mapping"

    harness.bind("bad", bad)
    workflow = _workflow([_echo_step(handler="bad")])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.STEP_FAILED


@pytest.mark.asyncio
async def test_a_failed_step_is_not_retried(harness: Harness) -> None:
    async def once(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        harness.calls.append("once")
        raise RuntimeFailure(RuntimeErrorCode.STEP_FAILED, "no")

    harness.bind("once", once)
    workflow = _workflow([_echo_step(handler="once")])
    harness.register(_definition(), _version(workflow), workflow)

    await harness.runtime.run("demo", _trigger())

    assert harness.calls == ["once"]


@pytest.mark.asyncio
async def test_observer_events_do_not_include_payload_secrets(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)

    await harness.runtime.run("demo", _trigger(payload={"access_token": "hidden-token", "topic": "launch"}))

    rendered = json.dumps([event.__dict__ for event in harness.events])
    assert "hidden-token" not in rendered
    assert "launch" not in rendered


@pytest.mark.asyncio
async def test_correlation_id_is_generated_when_absent(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.correlation_id.startswith("corr_")
    assert record.execution_id == "exec_1"


@pytest.mark.asyncio
async def test_payload_cannot_choose_the_execution_id(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger(payload={"execution_id": "exec_injected"}))

    assert record.execution_id == "exec_1"
    assert record.output["result"]["step"] == "echo"


@pytest.mark.asyncio
async def test_empty_workflow_completes(harness: Harness) -> None:
    workflow = _workflow([])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.status == ExecutionStatus.COMPLETED
    assert record.steps == []
    assert record.output["result"] == {}


@pytest.mark.asyncio
async def test_direct_execute_rejects_a_draft_in_production(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    version = _version(workflow, status=VersionStatus.DRAFT)
    definition = _definition(published=None)
    trigger = _trigger()
    snapshot = ResolvedRun(definition, version, workflow).snapshot(
        execution_id="exec_direct",
        correlation_id="corr_direct",
        trigger=trigger,
    )
    context = ExecutionContext(
        execution_id="exec_direct",
        correlation_id="corr_direct",
        tenant_id=trigger.tenant_id,
        user_id=trigger.user_id,
        mode=trigger.mode,
        trigger=trigger,
        snapshot=snapshot,
        cancellation=CancellationToken(),
        payload=freeze_payload(trigger.payload),
    )

    record = await harness.runtime.execute(definition, version, workflow, context, trigger)

    assert record.error is not None
    assert record.error.code == RuntimeErrorCode.DRAFT_NOT_ALLOWED
    assert harness.calls == []


@pytest.mark.asyncio
async def test_direct_execute_of_a_published_version_runs(harness: Harness) -> None:
    workflow = _workflow([_echo_step()])
    version = _version(workflow)
    definition = _definition()
    trigger = _trigger()
    snapshot = ResolvedRun(definition, version, workflow).snapshot(
        execution_id="exec_direct",
        correlation_id="corr_direct",
        trigger=trigger,
    )
    context = ExecutionContext(
        execution_id="exec_direct",
        correlation_id="corr_direct",
        tenant_id=trigger.tenant_id,
        user_id=trigger.user_id,
        mode=trigger.mode,
        trigger=trigger,
        snapshot=snapshot,
        cancellation=CancellationToken(),
        payload=freeze_payload(trigger.payload),
    )

    record = await harness.runtime.execute(definition, version, workflow, context, trigger)

    assert record.status == ExecutionStatus.COMPLETED
    assert record.execution_id == "exec_direct"
    assert harness.calls == ["echo"]


def test_catalog_rejects_a_second_draft_and_published_edits() -> None:
    catalog = InMemoryCatalog()
    workflow = _workflow([_echo_step()])
    definition = _definition(published=None)
    catalog.save_workflow(workflow)
    catalog.save_definition(definition)
    catalog.save_version(definition, _version(workflow, number=1, status=VersionStatus.DRAFT))
    with pytest.raises(RuntimeFailure) as second:
        catalog.save_version(definition, _version(workflow, number=2, status=VersionStatus.DRAFT))
    assert second.value.code == RuntimeErrorCode.INVALID_REQUEST

    catalog.save_version(definition, _version(workflow, number=1, status=VersionStatus.PUBLISHED))
    with pytest.raises(RuntimeFailure) as frozen:
        catalog.save_version(definition, _version(workflow, number=1, status=VersionStatus.PUBLISHED, tools=frozenset({"lookup"})))
    assert frozen.value.code == RuntimeErrorCode.INVALID_REQUEST
    with pytest.raises(RuntimeFailure):
        catalog.save_workflow(_workflow([_echo_step(key="changed")]))


def test_handler_registry_rejects_eval_and_strings() -> None:
    registry = HandlerRegistry()
    with pytest.raises(TypeError):
        registry.register("run", eval)  # noqa: S307
    with pytest.raises(ValueError):
        registry.register("os.system", lambda ctx, step, state: {})


@pytest.mark.asyncio
async def test_draft_can_call_a_model_without_publishing(harness: Harness) -> None:
    step = WorkflowStep(
        step_key="think",
        position=1,
        kind=StepKind.REASON,
        model_role="writer",
        side_effect=SideEffect.PROVIDER_CALL,
    )
    workflow = _workflow([step], policy_id=None)
    version = _version(workflow, status=VersionStatus.DRAFT, policy_id=None, roles=frozenset({"writer"}))
    harness.register(_definition(published=None), version, workflow)

    record = await harness.runtime.run("demo", _trigger(mode=ExecutionMode.TEST))

    assert record.status == ExecutionStatus.COMPLETED
    assert harness.calls == ["model:writer"]


@pytest.mark.asyncio
async def test_test_mode_without_a_requested_version_uses_the_pointer(harness: Harness) -> None:
    current = _workflow([_echo_step(handler="echo")], number=1)
    draft_flow = _workflow([_echo_step(key="next", handler="effect")], number=2)
    harness.register(_definition(published=1), _version(current, number=1), current)
    harness.catalog.save_workflow(draft_flow)
    harness.catalog.save_version(_definition(published=1), _version(draft_flow, number=2, status=VersionStatus.DRAFT))

    record = await harness.runtime.run("demo", _trigger(mode=ExecutionMode.TEST))

    assert record.agent_version_number == 1
    assert harness.calls == ["echo"]


@pytest.mark.asyncio
async def test_handler_result_strips_secret_keys(harness: Harness) -> None:
    async def leak(ctx: ExecutionContext, step: WorkflowStep, state: Mapping[str, Any]) -> dict[str, Any]:
        return {"ok": True, "token": "abc", "note": "done"}

    harness.bind("leak", leak)
    workflow = _workflow([_echo_step(handler="leak")])
    harness.register(_definition(), _version(workflow), workflow)

    record = await harness.runtime.run("demo", _trigger())

    assert record.output["result"]["ok"] is True
    assert "token" not in record.output["result"]


def test_runtime_failure_redacts_secret_values() -> None:
    failure = RuntimeFailure(RuntimeErrorCode.STEP_FAILED, "bad access_token=abc123")
    assert "abc123" not in failure.message
    assert "[REDACTED]" in failure.message
