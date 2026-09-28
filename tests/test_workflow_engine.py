"""Generic workflow engine. Instagram publishing is not involved."""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

from agent_platform.workflow import (
    CapabilityCall,
    CapabilityFailure,
    CapabilityRegistry,
    CapabilityRejected,
    ConditionOperator,
    ExecutionNotFound,
    ExecutionStatus,
    SecretRejected,
    StepCondition,
    StepDependency,
    StepInput,
    StepRetryPolicy,
    StepStatus,
    StepTimeout,
    StepType,
    UnknownCapability,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowStep,
)
from backend.mcp.tenant_isolation import trusted_tenant

ROOT = Path(__file__).resolve().parents[1] / "agent_platform" / "workflow"


def _engine() -> tuple[WorkflowEngine, CapabilityRegistry]:
    registry = CapabilityRegistry()
    return WorkflowEngine(registry), registry


def _linear(registry: CapabilityRegistry, workflow_id: str, middle: tuple[WorkflowStep, ...]) -> WorkflowDefinition:
    steps = [
        WorkflowStep(id="start", step_type=StepType.START, next_step=middle[0].id if middle else "end"),
        *middle,
        WorkflowStep(id="end", step_type=StepType.END),
    ]
    if not middle:
        steps[0] = WorkflowStep(id="start", step_type=StepType.START, next_step="end")
    return WorkflowDefinition(id=workflow_id, version=1, steps=tuple(steps))


def test_sequential_workflow_passes_step_output_forward() -> None:
    engine, registry = _engine()
    order: list[str] = []

    def load_context(call: CapabilityCall) -> dict[str, str]:
        order.append(call.step_id)
        assert call.tenant_id == "tenant-a"
        assert "user_id" not in call.data
        return {"topic": "widgets"}

    def apply_service(call: CapabilityCall) -> dict[str, int | str]:
        order.append(call.step_id)
        assert call.data["topic"] == "widgets"
        return {"topic": call.data["topic"], "n": 1}

    registry.register("load_context", load_context, step_types=frozenset({StepType.CONTEXT}))
    registry.register("apply_service", apply_service, step_types=frozenset({StepType.SERVICE}))
    definition = WorkflowDefinition(
        id="sample.sequence",
        version=2,
        steps=(
            WorkflowStep(id="start", step_type=StepType.START, next_step="context"),
            WorkflowStep(
                id="context",
                step_type=StepType.CONTEXT,
                capability="load_context",
                next_step="work",
                depends_on=(StepDependency("start"),),
            ),
            WorkflowStep(
                id="work",
                step_type=StepType.SERVICE,
                capability="apply_service",
                next_step="end",
                depends_on=(StepDependency("context"),),
                input=StepInput(from_steps=(("topic", "context", "topic"),)),
            ),
            WorkflowStep(id="end", step_type=StepType.END),
        ),
    )

    execution = engine.run(definition, trusted_tenant("tenant-a"), {"note": "keep"})

    assert order == ["context", "work"]
    assert execution.status is ExecutionStatus.COMPLETED
    assert execution.workflow_version == 2
    assert execution.tenant_id == "tenant-a"
    assert execution.step("work").output is not None
    assert execution.step("work").output.data["n"] == 1
    assert execution.step("end").output is not None
    assert execution.step("end").output.data["steps"]["work"]["topic"] == "widgets"


def test_failed_step_stops_the_workflow() -> None:
    engine, registry = _engine()
    called = {"later": 0}

    def boom(_call: CapabilityCall) -> dict[str, str]:
        raise CapabilityFailure("failed")

    def later(_call: CapabilityCall) -> dict[str, bool]:
        called["later"] += 1
        return {"ok": True}

    registry.register("boom", boom, step_types=frozenset({StepType.SERVICE}))
    registry.register("later", later, step_types=frozenset({StepType.SERVICE}))
    definition = _linear(
        registry,
        "sample.fail",
        (
            WorkflowStep(id="boom", step_type=StepType.SERVICE, capability="boom", next_step="later"),
            WorkflowStep(id="later", step_type=StepType.SERVICE, capability="later", next_step="end"),
        ),
    )

    execution = engine.run(definition, trusted_tenant("tenant-a"))

    assert execution.status is ExecutionStatus.FAILED
    assert execution.error_code == "failed"
    assert execution.step("boom").status is StepStatus.FAILED
    assert execution.step("later").status is StepStatus.PENDING
    assert called["later"] == 0


def test_conditional_step_runs_one_branch() -> None:
    engine, registry = _engine()
    called: list[str] = []

    def take(name: str):
        def handler(_call: CapabilityCall) -> dict[str, str]:
            called.append(name)
            return {"branch": name}

        return handler

    registry.register("take_yes", take("yes"), step_types=frozenset({StepType.ACTION}))
    registry.register("take_no", take("no"), step_types=frozenset({StepType.ACTION}))
    definition = WorkflowDefinition(
        id="sample.branch",
        steps=(
            WorkflowStep(id="start", step_type=StepType.START, next_step="gate"),
            WorkflowStep(
                id="gate",
                step_type=StepType.CONDITION,
                condition=StepCondition(
                    source_step="start",
                    field="flag",
                    operator=ConditionOperator.EQ,
                    value=True,
                    then_step="yes",
                    else_step="no",
                ),
            ),
            WorkflowStep(id="yes", step_type=StepType.ACTION, capability="take_yes", next_step="end"),
            WorkflowStep(id="no", step_type=StepType.ACTION, capability="take_no", next_step="end"),
            WorkflowStep(id="end", step_type=StepType.END),
        ),
    )

    taken = engine.run(definition, trusted_tenant("tenant-a"), {"flag": True})
    assert called == ["yes"]
    assert taken.status is ExecutionStatus.COMPLETED
    assert taken.step("yes").status is StepStatus.SUCCEEDED
    assert taken.step("no").status is StepStatus.SKIPPED

    called.clear()
    other = engine.run(definition, trusted_tenant("tenant-a"), {"flag": False})
    assert called == ["no"]
    assert other.step("yes").status is StepStatus.SKIPPED
    assert other.step("no").status is StepStatus.SUCCEEDED


def test_approval_pause_waits_until_resume() -> None:
    engine, registry = _engine()
    calls = {"gate": 0, "act": 0, "verify": 0}

    def gate(_call: CapabilityCall) -> dict[str, str]:
        calls["gate"] += 1
        return {"decision": "pause"}

    def act(_call: CapabilityCall) -> dict[str, bool]:
        calls["act"] += 1
        return {"done": True}

    def verify(_call: CapabilityCall) -> dict[str, bool]:
        calls["verify"] += 1
        return {"verified": True}

    registry.register("gate", gate, step_types=frozenset({StepType.APPROVAL}))
    registry.register("act", act, step_types=frozenset({StepType.ACTION}))
    registry.register("verify", verify, step_types=frozenset({StepType.VERIFICATION}))
    definition = _linear(
        registry,
        "sample.approval",
        (
            WorkflowStep(id="review", step_type=StepType.APPROVAL, capability="gate", next_step="act"),
            WorkflowStep(id="act", step_type=StepType.ACTION, capability="act", next_step="verify"),
            WorkflowStep(id="verify", step_type=StepType.VERIFICATION, capability="verify", next_step="end"),
        ),
    )
    tenant = trusted_tenant("tenant-a")

    paused = engine.run(definition, tenant)
    assert paused.status is ExecutionStatus.WAITING_APPROVAL
    assert paused.step("review").status is StepStatus.WAITING_APPROVAL
    assert calls == {"gate": 1, "act": 0, "verify": 0}

    approved = engine.resume(tenant, paused.id, approved=True)
    assert approved.id == paused.id
    assert approved.status is ExecutionStatus.COMPLETED
    assert calls == {"gate": 1, "act": 1, "verify": 1}

    rejected_definition = _linear(
        registry,
        "sample.approval_reject",
        (WorkflowStep(id="review", step_type=StepType.APPROVAL, capability="gate", next_step="end"),),
    )
    waiting = engine.run(rejected_definition, tenant)
    rejected = engine.resume(tenant, waiting.id, approved=False)
    assert rejected.status is ExecutionStatus.FAILED
    assert rejected.error_code == "approval_rejected"
    assert calls["act"] == 1


def test_retry_policy_repeats_only_listed_codes() -> None:
    engine, registry = _engine()
    state = {"flaky": 0, "fatal": 0}

    def flaky(_call: CapabilityCall) -> dict[str, bool]:
        state["flaky"] += 1
        if state["flaky"] < 3:
            raise CapabilityFailure("failed")
        return {"ok": True}

    def fatal(_call: CapabilityCall) -> dict[str, bool]:
        state["fatal"] += 1
        raise CapabilityFailure("not_retryable")

    registry.register("flaky", flaky, step_types=frozenset({StepType.SERVICE}))
    registry.register("fatal", fatal, step_types=frozenset({StepType.SERVICE}))
    recovered = _linear(
        registry,
        "sample.retry",
        (
            WorkflowStep(
                id="work",
                step_type=StepType.SERVICE,
                capability="flaky",
                next_step="end",
                retry=StepRetryPolicy(max_attempts=3, retry_on=frozenset({"failed"})),
            ),
        ),
    )
    stopped = _linear(
        registry,
        "sample.retry_stop",
        (
            WorkflowStep(
                id="work",
                step_type=StepType.SERVICE,
                capability="fatal",
                next_step="end",
                retry=StepRetryPolicy(max_attempts=3, retry_on=frozenset({"failed"})),
            ),
        ),
    )
    tenant = trusted_tenant("tenant-a")

    success = engine.run(recovered, tenant)
    assert success.status is ExecutionStatus.COMPLETED
    assert success.step("work").attempts == 3
    assert state["flaky"] == 3

    failure = engine.run(stopped, tenant)
    assert failure.status is ExecutionStatus.FAILED
    assert failure.step("work").attempts == 1
    assert state["fatal"] == 1


def test_timeout_fails_the_step_without_running_the_rest() -> None:
    engine, registry = _engine()
    called = {"later": 0}

    def slow(_call: CapabilityCall) -> dict[str, bool]:
        time.sleep(0.4)
        return {"ok": True}

    def later(_call: CapabilityCall) -> dict[str, bool]:
        called["later"] += 1
        return {"ok": True}

    registry.register("slow", slow, step_types=frozenset({StepType.LLM}))
    registry.register("later", later, step_types=frozenset({StepType.SERVICE}))
    definition = _linear(
        registry,
        "sample.timeout",
        (
            WorkflowStep(
                id="slow",
                step_type=StepType.LLM,
                capability="slow",
                next_step="later",
                timeout=StepTimeout(seconds=0.05),
            ),
            WorkflowStep(id="later", step_type=StepType.SERVICE, capability="later", next_step="end"),
        ),
    )

    execution = engine.run(definition, trusted_tenant("tenant-a"))

    assert execution.status is ExecutionStatus.TIMED_OUT
    assert execution.error_code == "timeout"
    assert execution.step("slow").status is StepStatus.TIMED_OUT
    assert execution.step("slow").attempts == 1
    assert execution.step("later").status is StepStatus.PENDING
    assert called["later"] == 0


def test_unknown_tool_is_rejected() -> None:
    engine, registry = _engine()

    def unused(_call: CapabilityCall) -> dict[str, bool]:
        raise AssertionError("handler must not run")

    registry.register("known", unused, step_types=frozenset({StepType.TOOL}))
    definition = _linear(
        registry,
        "sample.unknown",
        (WorkflowStep(id="work", step_type=StepType.TOOL, capability="model_invented_tool", next_step="end"),),
    )

    with pytest.raises(UnknownCapability):
        engine.run(definition, trusted_tenant("tenant-a"))

    with pytest.raises(CapabilityRejected):
        registry.register("publish_instagram_media", unused, step_types=frozenset({StepType.TOOL}))
    with pytest.raises(CapabilityRejected):
        registry.register("os.system", unused, step_types=frozenset({StepType.TOOL}))
    with pytest.raises(CapabilityRejected):
        registry.register("eval", unused, step_types=frozenset({StepType.SERVICE}))


def test_tenant_isolation_hides_other_executions() -> None:
    engine, registry = _engine()
    seen: list[tuple[str, dict[str, object]]] = []

    def watch(call: CapabilityCall) -> dict[str, str]:
        seen.append((call.tenant_id, dict(call.data)))
        return {"decision": "pause"}

    registry.register("watch", watch, step_types=frozenset({StepType.APPROVAL}))
    definition = _linear(
        registry,
        "sample.tenant",
        (
            WorkflowStep(
                id="review",
                step_type=StepType.APPROVAL,
                capability="watch",
                next_step="end",
                input=StepInput(from_steps=(("note", "start", "note"),)),
            ),
        ),
    )
    owner = trusted_tenant("tenant-a")
    other = trusted_tenant("tenant-b", source="scheduler")

    execution = engine.run(definition, owner, {"user_id": "tenant-b", "note": "hello"}, idempotency_key="same-key")

    assert execution.step("start").output is not None
    assert execution.step("start").output.data == {"note": "hello"}
    assert seen == [("tenant-a", {"note": "hello"})]
    assert engine.get(owner, execution.id).id == execution.id
    with pytest.raises(ExecutionNotFound):
        engine.get(other, execution.id)
    with pytest.raises(ExecutionNotFound):
        engine.resume(other, execution.id, approved=True)
    with pytest.raises(ExecutionNotFound):
        engine.cancel(other, execution.id)
    assert engine.get(owner, execution.id).status is ExecutionStatus.WAITING_APPROVAL

    other_run = engine.run(definition, other, {"note": "other"}, idempotency_key="same-key")
    assert other_run.id != execution.id
    assert other_run.tenant_id == "tenant-b"
    assert [item[0] for item in seen] == ["tenant-a", "tenant-b"]


def test_idempotency_key_does_not_run_the_handler_twice() -> None:
    engine, registry = _engine()
    calls = {"n": 0}

    def once(_call: CapabilityCall) -> dict[str, bool]:
        calls["n"] += 1
        return {"ok": True}

    registry.register("once", once, step_types=frozenset({StepType.SERVICE}))
    definition = _linear(
        registry,
        "sample.once",
        (WorkflowStep(id="work", step_type=StepType.SERVICE, capability="once", next_step="end"),),
    )
    tenant = trusted_tenant("tenant-a")

    first = engine.run(definition, tenant, {"n": 1}, idempotency_key="run-1")
    second = engine.run(definition, tenant, {"n": 2}, idempotency_key="run-1")

    assert first.id == second.id
    assert calls["n"] == 1
    assert second.status is ExecutionStatus.COMPLETED


def test_definition_rejects_secret_fields() -> None:
    engine, registry = _engine()

    def echo(_call: CapabilityCall) -> dict[str, bool]:
        return {"ok": True}

    registry.register("echo", echo, step_types=frozenset({StepType.MCP}))
    definition = WorkflowDefinition(
        id="sample.secret",
        steps=(
            WorkflowStep(id="start", step_type=StepType.START, next_step="work"),
            WorkflowStep(
                id="work",
                step_type=StepType.MCP,
                capability="echo",
                next_step="end",
                input=StepInput(values=(("api_key", "sk-test"),)),
            ),
            WorkflowStep(id="end", step_type=StepType.END),
        ),
    )

    with pytest.raises(SecretRejected):
        engine.run(definition, trusted_tenant("tenant-a"), {"token": "raw"})


def test_cancellation_stops_before_the_next_step() -> None:
    engine, registry = _engine()
    started = threading.Event()
    release = threading.Event()
    holder: dict[str, object] = {}

    def block(call: CapabilityCall) -> dict[str, bool]:
        holder["id"] = call.execution_id
        started.set()
        release.wait(2)
        return {"ok": True}

    def after(_call: CapabilityCall) -> dict[str, bool]:
        holder["after"] = True
        return {"ok": True}

    registry.register("block", block, step_types=frozenset({StepType.SERVICE}))
    registry.register("after", after, step_types=frozenset({StepType.SERVICE}))
    definition = _linear(
        registry,
        "sample.cancel",
        (
            WorkflowStep(id="block", step_type=StepType.SERVICE, capability="block", next_step="after"),
            WorkflowStep(id="after", step_type=StepType.SERVICE, capability="after", next_step="end"),
        ),
    )
    tenant = trusted_tenant("tenant-a")

    def runner() -> None:
        holder["execution"] = engine.run(definition, tenant)

    thread = threading.Thread(target=runner)
    thread.start()
    assert started.wait(2)
    engine.cancel(tenant, str(holder["id"]))
    release.set()
    thread.join(3)

    execution = holder["execution"]
    assert thread.is_alive() is False
    assert execution.status is ExecutionStatus.CANCELLED
    assert execution.step("block").status is StepStatus.SUCCEEDED
    assert execution.step("after").status is StepStatus.CANCELLED
    assert "after" not in holder


def test_package_does_not_call_dynamic_execution() -> None:
    forbidden_calls = {"eval", "exec", "__import__"}
    forbidden_roots = {"subprocess", "importlib", "agent", "tools", "services", "backend"}
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for module in modules:
                    root = module.split(".", 1)[0]
                    assert root not in forbidden_roots
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"system", "popen"}
